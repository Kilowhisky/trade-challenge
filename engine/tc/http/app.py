"""The engine's HTTP surface (spec §7 layer 2).

Three routes, three different trust levels:

* `/health` is read by an unattended probe (`scripts/job-deadman.sh`'s
  cousin, and any external uptime check) on a schedule the engine does not
  control. It must never itself fail the request -- a probe reading a 5xx
  cannot tell "the engine is unhealthy" from "the health check crashed", so
  every field here is computed defensively and the response is always 200.
  `ok` is the single boolean a probe should actually alert on.
* `/oauth/callback` is where Chris's phone lands after approving Schwab's
  OAuth consent screen (CLAUDE.md's "standing operating constraint: token
  expiry" -- a 7-day refresh token needs 4-5 manual re-auths). It runs
  `TokenStore.complete_auth` -- sync, schwab-py network I/O -- off the event
  loop via `run_in_threadpool`, and its error body is deliberately terse: the
  exception class name only, never the request URL or its `code`/`state`
  query params, which are one-time OAuth secrets.
* `/api/*` is a read-only feed for a dashboard: the latest `session_status`
  ledger row, the most recent in-memory tick, and a day's tick history.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from authlib.common.errors import AuthlibBaseError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from tc.broker.token import NoAuthInProgress, TokenStore, action_for
from tc.loops.tick import TickResult
from tc.store.db import Store

logger = logging.getLogger(__name__)

_CALLBACK_OK = "Token installed — you can close this tab."


@dataclass
class EngineState:
    """Task 10 (`main.py`) constructs and owns one of these; every route here
    only reads (or, for `blind`, flips) its fields. Not a pydantic model:
    this is live in-process state mutated between requests (`last_tick`,
    `blind`), not a value ever serialised, validated, or sent over the wire
    as a unit."""

    token: TokenStore
    store: Store
    started_at: datetime
    now: Callable[[], datetime]
    version: str
    shadow: bool
    blind: bool = False
    last_broker_read_ok_at: datetime | None = None
    last_tick: TickResult | None = None
    runner_ok: bool | None = None
    pending_approval_age_s: float | None = None
    in_flight_proposal: bool = False


async def _db_ok(store: Store) -> bool:
    try:
        await store.fetchone("SELECT 1")
    except Exception:
        logger.exception("health: db check failed")
        return False
    return True


def build_app(state: EngineState) -> Starlette:
    async def health(request: Request) -> Response:
        db_ok = await _db_ok(state.store)
        token_state, _age_days, days_until_dead = state.token.status()

        last_ok = state.last_broker_read_ok_at
        age_s = None if last_ok is None else int((state.now() - last_ok).total_seconds())

        naked = None if state.last_tick is None or state.last_tick.view is None else (
            len(state.last_tick.view.naked)
        )

        body = {
            "ok": (not state.blind) and db_ok,
            "version": state.version,
            "shadow": state.shadow,
            "blind": state.blind,
            "token_state": token_state,
            "token_days_until_dead": days_until_dead,
            "last_broker_read_ok_at": None if last_ok is None else last_ok.isoformat(),
            "last_broker_read_age_s": age_s,
            "positions_without_stop": naked,
            "pending_approval_age_s": state.pending_approval_age_s,
            "runner_ok": state.runner_ok,
            "db_ok": db_ok,
            "in_flight_proposal": state.in_flight_proposal,
            "action": action_for(token_state),
        }
        return JSONResponse(body)

    async def oauth_callback(request: Request) -> Response:
        try:
            await run_in_threadpool(state.token.complete_auth, str(request.url))
        except (NoAuthInProgress, ValueError, AuthlibBaseError) as e:
            await state.store.record_token_event("install_failed", type(e).__name__)
            return PlainTextResponse(type(e).__name__, status_code=400)
        await state.store.record_token_event("installed", "callback")
        state.blind = False
        return PlainTextResponse(_CALLBACK_OK)

    async def api_status(request: Request) -> Response:
        session = await state.store.latest_session_status()
        tick = state.last_tick
        return JSONResponse({
            "session": None if session is None else session.model_dump(mode="json"),
            "tick": None if tick is None else tick.row.model_dump(mode="json"),
        })

    async def api_ticks(request: Request) -> Response:
        date_str = request.query_params.get("date")
        if date_str is None:
            return JSONResponse({"detail": "date query parameter is required"}, status_code=400)
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"detail": "date must be YYYY-MM-DD"}, status_code=400)
        rows = await state.store.ticks_for(date_str)
        return JSONResponse({"rows": [r.model_dump(mode="json") for r in rows]})

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/oauth/callback", oauth_callback, methods=["GET"]),
            Route("/api/status", api_status, methods=["GET"]),
            Route("/api/ticks", api_ticks, methods=["GET"]),
        ]
    )
