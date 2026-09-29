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
from tc.broker.models import AccountSnapshot, MarketWindow
from tc.broker.token import TokenStore
from tc.clock import ET
from tc.desk.calls import NewCall, insert_call
from tc.desk.paper import (
    book_row,
    book_state,
    create_proposal,
    ensure_book,
    pending_proposals,
)
from tc.jobs.dispatch import JobRunner, RunnerClient
from tc.main import Engine
from tc.notify import Notifier, Pinger
from tc.scheduler import Scheduler, ScheduleSpec
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

    async def post_message(self, text: str) -> str | None:
        # The base `Notifier.post_message` returns `None` with no target
        # configured (`target=None` above), which `post_proposals` now (fix
        # round 1, finding 3) reads as a failed post and expires the
        # proposal rather than posting it -- this test exists to exercise
        # the SUCCESSFUL post path, so it fakes a real Discord message id.
        self.posts.append(text)
        return f"msg-{len(self.posts)}"


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
            seen: list[tuple[str, str | None]], *, busy: bool = False) -> Engine:
    s = desk_settings(tmp_path, env_extra=MCP_ENV)
    holder: dict[str, Engine] = {}

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        seen.append((body["job"], holder["e"]._active.name))
        if busy:
            return httpx.Response(409, json={"error": "runner busy"})
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


async def test_desk_watch_reads_the_engines_market_window(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    """Final review I1: an early close is the engine's window, not the
    schedule's -- the 13:05 fire records a noop and touches nothing."""
    at = datetime(2026, 11, 27, 13, 5, tzinfo=ET)
    e = _engine(tmp_path, desk_store, client, at, [])
    e._window = MarketWindow(date=at.date(), is_trading_day=True,
                             rth_start=datetime(2026, 11, 27, 9, 30, tzinfo=ET),
                             rth_end=datetime(2026, 11, 27, 13, 0, tzinfo=ET))
    assert await e.run_job("desk_watch", at) == "noop"
    row = await desk_store.fetchone("SELECT detail_json FROM job_runs WHERE job='desk_watch'")
    assert row is not None and json.loads(row["detail_json"])["skipped"] == "outside RTH"


# --- final review I2/I3, paper-book start, M6 ---------------------------

async def _account(store: Store) -> None:
    await store.record_account(AccountSnapshot(
        account_hash="H", read_at=PM_AT, liquidation_value=Decimal("3700.00"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[]))


def _posts(e: Engine) -> list[str]:
    assert isinstance(e.notifier, Notes)
    return e.notifier.posts


async def _detail(store: Store, job: str) -> dict[str, Any]:
    row = await store.fetchone(
        "SELECT detail_json FROM job_runs WHERE job=? ORDER BY id DESC LIMIT 1", (job,))
    assert row is not None
    return dict(json.loads(row["detail_json"]))


@pytest.mark.parametrize("job,at", [
    ("pm", PM_AT), ("pm_midday", datetime(2026, 9, 29, 16, 30, tzinfo=UTC)),
    ("desk_evening", EVENING), ("desk_preopen", datetime(2026, 9, 29, 12, 0, tzinfo=UTC)),
])
async def test_a_blind_engine_dispatches_no_pm_and_no_analyst_chain(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient, job: str, at: datetime,
) -> None:
    """Spec §14 "Blind: no PM": no runner call, a noop naming the reason,
    one warning line -- and no paper book started by a PM that never ran."""
    await _account(desk_store)
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, at, seen)
    e.state.blind = True
    assert await e.run_job(job, at) == "noop"
    assert seen == []
    assert (await _detail(desk_store, job))["skipped"] == "blind"
    assert [p for p in _posts(e) if p.startswith("⚠️")] == _posts(e) and len(_posts(e)) == 1
    assert await book_row(desk_store) is None


@pytest.mark.parametrize("verdict,runs", [("failed", False), ("missed", False), ("done", True)])
async def test_the_evening_chain_waits_on_that_evenings_bars(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient, verdict: str, runs: bool,
) -> None:
    bars_at = datetime(2026, 9, 28, 16, 10, tzinfo=ET)
    await desk_store.record_job_run("bars_refresh", bars_at, bars_at, verdict, {})
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, EVENING, seen)
    got = await e.run_job("desk_evening", EVENING)
    if runs:
        assert got == "done" and len(seen) == 4
    else:
        assert got == "noop" and seen == []
        assert (await _detail(desk_store, "desk_evening"))["skipped"] == "bars stale"
        assert len([p for p in _posts(e) if p.startswith("⚠️")]) == 1


async def test_a_pm_lost_to_a_busy_runner_is_said_out_loud(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, PM_AT, seen, busy=True)
    assert await e.run_job("pm", PM_AT) == "missed"
    assert [p for p in _posts(e) if p.startswith("⚠️ pm missed")] != []


async def test_an_evening_where_no_analyst_ran_is_missed_not_done(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, EVENING, seen, busy=True)
    assert await e.run_job("desk_evening", EVENING) == "missed"


async def test_a_pm_fire_the_engine_slept_through_is_said_out_loud(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
) -> None:
    at = datetime(2026, 9, 29, 10, 40, tzinfo=ET)
    e = _engine(tmp_path, desk_store, client, at, [])
    e.scheduler = Scheduler({"pm": ScheduleSpec.parse("at 09:50 weekdays")}, e._trading_day)
    await e._pass(at)
    assert [r["verdict"] for r in await desk_store.fetchall("SELECT verdict FROM job_runs")] == [
        "missed"]
    assert any(p.startswith("⚠️ pm missed") and "09:50" in p for p in _posts(e))


@pytest.mark.parametrize("at", [
    datetime(2026, 9, 29, 11, 0, tzinfo=ET),       # after the PM's 10:30 window
    datetime(2026, 9, 29, 9, 0, tzinfo=ET),        # before it
])
async def test_a_pm_fire_that_never_dispatches_does_not_start_the_book(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient, at: datetime,
) -> None:
    """The book's start date is the checkpoint baseline (spec §8): only a PM
    run that actually dispatches may fix it."""
    await _account(desk_store)
    seen: list[tuple[str, str | None]] = []
    e = _engine(tmp_path, desk_store, client, at, seen)
    assert await e.run_job("pm", at) == "noop"
    assert seen == [] and await book_row(desk_store) is None


async def test_a_pm_run_that_raises_still_posts_what_it_proposed(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final review M6: a proposal the run created before it raised is posted
    (behind its veto window) rather than left unposted until tomorrow."""
    await _account(desk_store)
    e = _engine(tmp_path, desk_store, client, PM_AT, [])
    assert e._jobs is not None

    async def boom(job: str, now: datetime | None = None, *, ignore_window: bool = False,
                   on_dispatch: Any = None) -> Any:
        await on_dispatch()
        call = await insert_call(desk_store, NewCall(
            made_at=PM_AT, session=PM_AT.date(), origin="pm", pitch_id=None,
            extends_call_id=None, symbol="AAA", direction="up", thesis="t" * 12,
            target=Decimal(55), invalidation=Decimal(48), horizon_days=5, conviction=3,
            benchmark="XLK", ref_price=Decimal(50), spy_ref=Decimal(500),
            bench_ref=Decimal(200), funding="shares"))
        await create_proposal(desk_store, call_id=call.id, created_at=PM_AT, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
        raise RuntimeError("the run fell over")

    monkeypatch.setattr(e._jobs, "execute", boom)
    assert await e.run_job("pm", PM_AT) == "failed"
    [p] = await pending_proposals(desk_store)
    assert p.posted_at is not None and p.message_id is not None


async def test_yesterdays_unfilled_proposal_is_expired_before_the_pm_sizes(
    tmp_path: Path, desk_store: Store, client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 09:50 PM runs before the first desk_watch; a proposal left over
    from yesterday must already be expired when the PM's tools size."""
    await _account(desk_store)
    yesterday = PM_AT - timedelta(days=1)
    await ensure_book(desk_store, yesterday.date(), Decimal("3700.00"), yesterday)
    call = await insert_call(desk_store, NewCall(
        made_at=yesterday, session=yesterday.date(), origin="pm", pitch_id=None,
        extends_call_id=None, symbol="AAA", direction="up", thesis="t" * 12,
        target=Decimal(55), invalidation=Decimal(48), horizon_days=5, conviction=3,
        benchmark="XLK", ref_price=Decimal(50), spy_ref=Decimal(500), bench_ref=Decimal(200),
        funding="shares"))
    await create_proposal(desk_store, call_id=call.id, created_at=yesterday, instrument="shares",
                          symbol="AAA", underlying="AAA", quantity=7,
                          max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    e = _engine(tmp_path, desk_store, client, PM_AT, [])
    assert e._jobs is not None
    seen_cash: list[Decimal] = []

    async def pm_run(job: str, now: datetime | None = None, *, ignore_window: bool = False,
                     on_dispatch: Any = None) -> Any:
        seen_cash.append((await book_state(desk_store)).cash)
        return "done", {}

    monkeypatch.setattr(e._jobs, "execute", pm_run)
    assert await e.run_job("pm", PM_AT) == "done"
    assert seen_cash == [Decimal("3700.00")]
    assert await pending_proposals(desk_store) == []
