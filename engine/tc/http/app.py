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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import httpx
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
    """Task 10 (`main.py`) constructs and owns one of these; the routes here
    read its fields and call `on_token_installed`, but never decide `blind`
    for themselves — the engine sets that from a read that succeeded. Not a
    pydantic model:
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
    # Set by the engine to a coroutine that re-opens the broker against the
    # token that has just landed and re-reads the account. The callback route
    # awaits it and never touches `blind` itself: a token on disk is not a
    # working broker, and only a read that actually succeeded may clear BLIND.
    on_token_installed: Callable[[], Awaitable[None]] | None = None
    # Consecutive `failed` job runs, per job, reset by the first done/noop.
    # /health turns the tick's count into `ok: false` — a job that fails every
    # sweep is an engine that is not working, however healthy its parts look.
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    # Trip posting is on transition (main.py `_job_tick`): the signature of the
    # trips last posted, and when. Standing trips are re-posted hourly, not
    # every sweep.
    last_trip_signature: str | None = None
    last_trip_posted_at: datetime | None = None


async def _db_ok(store: Store) -> bool:
    try:
        await store.fetchone("SELECT 1")
    except Exception:
        logger.exception("health: db check failed")
        return False
    return True


async def _open_alert_count(store: Store) -> int:
    """Unacked alerts. A store that will not answer reports 0 rather than
    failing the request: `db_ok` is already false in that case and the probe
    alerts on it, and /health's one contract is that it always answers."""
    try:
        return len(await store.open_alerts())
    except Exception:
        logger.exception("health: open-alert count failed")
        return 0


def build_app(state: EngineState) -> Starlette:
    async def health(request: Request) -> Response:
        db_ok = await _db_ok(state.store)
        token_state, _age_days, days_until_dead = state.token.status()

        last_ok = state.last_broker_read_ok_at
        age_s = None if last_ok is None else int((state.now() - last_ok).total_seconds())

        naked = None if state.last_tick is None or state.last_tick.view is None else (
            len(state.last_tick.view.naked)
        )
        open_alerts = await _open_alert_count(state.store)
        # Two consecutive failures is the threshold: one failed sweep is a
        # transient upstream, two in a row is a loop that is not working. The
        # engine cannot report this about itself any other way — every job
        # failure is already absorbed into a ledger row by design.
        tick_failures = state.consecutive_failures.get("tick", 0)

        body = {
            "ok": (not state.blind) and db_ok and tick_failures < 2,
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
            "open_alerts": open_alerts,
            "consecutive_tick_failures": tick_failures,
            "in_flight_proposal": state.in_flight_proposal,
            "action": action_for(token_state),
        }
        return JSONResponse(body)

    async def oauth_callback(request: Request) -> Response:
        try:
            await run_in_threadpool(state.token.complete_auth, str(request.url))
        except (
            NoAuthInProgress,
            ValueError,
            AuthlibBaseError,
            OSError,
            KeyError,
            httpx.HTTPError,
        ) as e:
            # The class name is the entire body on every one of these: the
            # request URL and its code/state params are one-time OAuth
            # secrets, and an exception message here can carry them.
            await state.store.record_token_event("install_failed", type(e).__name__)
            return PlainTextResponse(type(e).__name__, status_code=400)
        await state.store.record_token_event("installed", "callback")
        # The token is installed, so the standing "token dead" alert is
        # answered — nothing else ever acks it, and an alert that can only
        # open is one an operator learns to ignore.
        await state.store.ack_alerts_of_kind("token_dead")
        # `blind` is NOT cleared here. The engine's hook re-opens the broker
        # and re-reads the account; whether that read succeeds is the only
        # evidence the engine can see, and /health must keep saying ok:false
        # until it does.
        if state.on_token_installed is not None:
            await state.on_token_installed()
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
