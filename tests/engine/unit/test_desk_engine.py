"""Task 17: the chain runs the analysts one after another under their own
identity, and the PM run starts the paper book and posts its proposals."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from desk_fixtures import desk_settings

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot
from tc.broker.token import TokenStore
from tc.desk.calls import NewCall, insert_call
from tc.desk.paper import book_row, create_proposal, pending_proposals
from tc.jobs.dispatch import JobRunner, RunnerClient
from tc.main import Engine
from tc.notify import Notifier, Pinger
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
MCP_ENV = ("TC_RUNNER_TOKEN=runner-token-not-real\nTC_MCP_RESEARCH_TOKEN=research-token-not-real\n"
           "TC_MCP_DECIDE_TOKEN=decide-token-not-real\n")
EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)
PM_AT = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)


class Notes(Notifier):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(None, client)
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


async def _no_sleep(s: float) -> None:
    return None


def _result(verdict: dict[str, Any]) -> dict[str, Any]:
    return {"verdict_raw": verdict, "result_text": "{}", "is_error": False, "subtype": "success",
            "num_turns": 3, "permission_denials": [], "usage": {}, "duration_s": 1.0,
            "timed_out": False}


def _engine(tmp_path: Path, store: Store, client: httpx.AsyncClient, now: datetime,
            seen: list[tuple[str, str | None]]) -> Engine:
    s = desk_settings(tmp_path, env_extra=MCP_ENV)
    holder: dict[str, Engine] = {}

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        seen.append((body["job"], holder["e"]._active.name))
        if body["mcp_role"] == "decide":
            return httpx.Response(200, json=_result({"held": [], "calls": [], "summary": "PM 0 calls"}))
        return httpx.Response(200, json=_result({"pitched": [], "withdrawn": [], "summary": "0"}))

    runner = RunnerClient("http://runner", "runner-token-not-real",
                          httpx.AsyncClient(transport=httpx.MockTransport(h)), 120.0,
                          role_token="research-token-not-real",  # noqa: S106
                          role_tokens={"decide": "decide-token-not-real"})
    notes = Notes(client)
    e = Engine(s, broker=FakeBroker(FIX, now), store=store,
               token=TokenStore(tmp_path / "token.json", s.token, "k", "s"), notifier=notes,
               pinger=Pinger(None, client), clock=lambda: now, sleep_s=0, client=client,
               jobs=JobRunner(runner, notes, lambda: now, sleep=_no_sleep))
    holder["e"] = e
    return e


async def test_the_evening_chain_runs_each_analyst_under_its_own_name(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, EVENING, seen)
    assert await e.run_job("desk_evening", EVENING) == "done"
    jobs = ["analyst_technical", "analyst_earnings", "analyst_news", "analyst_macro"]
    assert seen == [(j, j) for j in jobs]
    assert e._active.name is None
    rows = await desk_store.fetchall("SELECT job FROM job_runs ORDER BY id")
    assert [r["job"] for r in rows] == [*jobs, "desk_evening"]
    notes = e.notifier
    assert isinstance(notes, Notes) and any("DESK" in p for p in notes.posts)


async def test_the_pm_run_starts_the_book_and_posts_its_proposals(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    await desk_store.record_account(AccountSnapshot(
        account_hash="H", read_at=PM_AT, liquidation_value=Decimal("3700.00"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[]))
    call = await insert_call(desk_store, NewCall(
        made_at=PM_AT, session=PM_AT.date(), origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="t" * 12, target=Decimal(55), invalidation=Decimal(48),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50), spy_ref=Decimal(500),
        bench_ref=Decimal(200), funding="shares"))
    await create_proposal(desk_store, call_id=call.id, created_at=PM_AT, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, PM_AT, seen)
    assert await e.run_job("pm", PM_AT) == "done"
    assert seen == [("pm", "pm")]
    assert await book_row(desk_store) == (PM_AT.date(), Decimal("3700.00"))
    [p] = await pending_proposals(desk_store)
    assert p.posted_at is not None
    # Posted at 09:50, before the 10:00 entry window opens: the ten-minute
    # veto window runs from 10:00, so nothing can execute before 10:10.
    assert p.veto_deadline == datetime(2026, 9, 29, 14, 0, tzinfo=UTC) + timedelta(minutes=10)
