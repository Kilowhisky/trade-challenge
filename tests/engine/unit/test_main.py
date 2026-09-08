"""The engine as one process: one loop, one lock per job, one row per fire.

Every assertion here is about the *seam* between the scheduler, the loops and
the store — the loops themselves are tested in their own modules. What this
file exists to pin down:

* **A fire that ran writes exactly one `job_runs` row; a fire the engine slept
  through writes a `missed` row and is never run late** (scheduler.py's
  contract, now enforced end to end: a 09:47 sweep executed at 10:20 would be
  a row claiming a sweep that did not happen).
* **Broker trouble is recorded, not raised.** A dead token starts the engine
  BLIND with `/health` saying `ok: false`; a non-401 `BrokerError` inside a job
  is one `failed` row plus one Discord line, and the loop keeps running.
* **`tc run` never binds a public interface**, and `--once` is a real sweep
  against the fixture broker rather than a dry run.

The clock is a mutable holder rather than real time, and `sleep_s=0` removes
the 1 s pacing: a test that needed wall-clock seconds to observe a 15-minute
schedule would be a test nobody runs.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tc import cli, main
from tc.broker.client import Broker, BrokerError
from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, MarketWindow
from tc.broker.token import TokenStore
from tc.clock import ET, trading_days_between
from tc.config import Settings, load_settings
from tc.http.app import build_app
from tc.jobs.dispatch import JobRunner, RunnerClient
from tc.loops.session import seed_hwm
from tc.main import (
    BACKUPS_KEPT,
    CLAUDE_JOBS,
    JOBS,
    TRIP_REPOST_S,
    WINDOW_RETRY_S,
    Engine,
    build_engine,
    check_bind,
)
from tc.notify import Notifier, Pinger
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
REPO = Path(__file__).resolve().parents[3]

TODAY = date(2026, 9, 4)  # Friday — the day every broker fixture is pinned to
NOW = datetime(2026, 9, 4, 17, 31, tzinfo=UTC)  # 13:31 ET, inside RTH
RESERVE = D("900.00")

CONFIG = """
engine:
  timezone: America/New_York
  data_dir: "{data}"
  repo_dir: "{repo}"
  research_dir: "{data}/research"
  http_bind: "{bind}"
  reserve_usd: "900.00"
  orders_from: 2026-08-14
  leveraged_symbols: []
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
shadow:
  enabled: true
schedule:
  tick:          "every 15m 09:32-15:47 weekdays"
  session_close: "at 16:04 weekdays"
  token_check:   "at 07:05 daily"
  expectations:  "at 07:30 daily"
  backup:        "at 23:30 daily"
expectations:
  - name: session_status_present
    check: session_status_present
"""
ENV = (
    "TC_SCHWAB_APP_KEY=k\n"
    "TC_SCHWAB_APP_SECRET=s\n"
    "TC_DISCORD_WEBHOOK_URL=https://live.example/h\n"
    "TC_DISCORD_SHADOW_WEBHOOK_URL=https://shadow.example/h\n"
)


def et(hh: int, mm: int, d: date = TODAY) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=ET)


class Clock:
    """A datetime the test moves by hand. Every engine read of `now` goes
    through this, so a 15-minute schedule is exercised in microseconds."""

    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class RecordingNotifier(Notifier):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(None, client)
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


class BoomBroker(FakeBroker):
    """Authorised, reachable, and broken: the non-401 `BrokerError` that
    `run_tick` deliberately does not catch (its BLIND path is for 401s only),
    so the dispatcher is the thing under test."""

    async def account(self, account_hash: str) -> AccountSnapshot:
        raise BrokerError("500: upstream")


class FlakyWindowBroker(FakeBroker):
    """The market-hours read fails once, then works — a 30-second outage at
    04:00 that used to pin the engine to a guessed calendar all day."""

    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        super().__init__(fixture_dir, frozen_now)
        self.window_calls = 0

    async def market_window(self, d: date) -> MarketWindow:
        self.window_calls += 1
        if self.window_calls == 1:
            raise BrokerError("503: hours unavailable")
        return await super().market_window(d)


class DeadWindowBroker(FakeBroker):
    """The market-hours read never answers. The engine keeps the guessed
    calendar and must not hammer the endpoint once a second for it."""

    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        super().__init__(fixture_dir, frozen_now)
        self.window_calls = 0

    async def market_window(self, d: date) -> MarketWindow:
        self.window_calls += 1
        raise BrokerError("503: hours unavailable")


class RecoveringBroker(FakeBroker):
    """Broken until `fail` is cleared — the upstream outage that ends."""

    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        super().__init__(fixture_dir, frozen_now)
        self.fail = True

    async def account(self, account_hash: str) -> AccountSnapshot:
        if self.fail:
            raise BrokerError("500: upstream")
        return await super().account(account_hash)


class UnwritableStore(Store):
    """Reads fine, refuses every ledger write."""

    async def record_job_run(self, *a: object, **k: object) -> None:
        raise OSError("disk full")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def _fx(tmp_path: Path) -> Path:
    d = tmp_path / "fx"
    if not d.exists():
        shutil.copytree(FIX, d)
    return d


def _write_config(
    tmp_path: Path, *, bind: str = "127.0.0.1:8080", env_extra: str = ""
) -> Path:
    (tmp_path / "config.yml").write_text(
        CONFIG.format(data=tmp_path, repo=REPO, bind=bind)
    )
    (tmp_path / ".env").write_text(ENV + env_extra)
    return tmp_path / "config.yml"


def _cli_args(
    tmp_path: Path, *, bind: str = "127.0.0.1:8080", env_extra: str = ""
) -> list[str]:
    cfg = _write_config(tmp_path, bind=bind, env_extra=env_extra)
    return ["--config", str(cfg), "--env", str(tmp_path / ".env")]


def _settings(
    tmp_path: Path, *, bind: str = "127.0.0.1:8080", env_extra: str = ""
) -> Settings:
    cfg = _write_config(tmp_path, bind=bind, env_extra=env_extra)
    return load_settings(cfg, tmp_path / ".env")


def _engine(
    settings: Settings,
    store: Store,
    broker: Broker,
    clock: Clock,
    notifier: Notifier,
    client: httpx.AsyncClient,
    jobs: JobRunner | None = None,
) -> Engine:
    token = TokenStore(
        settings.engine.data_dir / "token.json", settings.token, "k", "s"
    )
    return Engine(
        settings,
        broker=broker,
        store=store,
        token=token,
        notifier=notifier,
        pinger=Pinger(None, client),
        clock=clock,
        sleep_s=0.0,
        jobs=jobs,
    )


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "engine.db")
    await s.open()
    yield s
    await s.close()


async def _seed(store: Store, hwm: str = "3800.00") -> None:
    await seed_hwm(store, D(hwm), date(2026, 9, 3), RESERVE)


async def _job_runs(store: Store, job: str | None = None) -> list[tuple[str, str, str]]:
    rows = await store.fetchall(
        "SELECT job, verdict, started_at FROM job_runs ORDER BY id"
    )
    out = [(r["job"], r["verdict"], r["started_at"]) for r in rows]
    return out if job is None else [r for r in out if r[0] == job]


# --- the loop ---------------------------------------------------------------

async def test_two_due_ticks_are_two_done_runs_and_two_rows(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    await _seed(store)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), clock, RecordingNotifier(client), client)
    await eng.start()

    await asyncio.wait_for(eng.run_for(1), 5)
    clock.t = et(9, 47)
    await asyncio.wait_for(eng.run_for(1), 5)

    assert [v for _, v, _ in await _job_runs(store, "tick")] == ["done", "done"]
    # The 07:05 and 07:30 dailies were already past when the engine started:
    # recorded as missed on the first pass, never run four hours late.
    assert [(j, v) for j, v, _ in await _job_runs(store) if j != "tick"] == [
        ("token_check", "missed"),
        ("expectations", "missed"),
    ]
    assert [t.at_et for t in await store.ticks_for("2026-09-04")] == [
        "2026-09-04 09:32",
        "2026-09-04 09:47",
    ]
    assert eng.state.last_tick is not None and eng.state.last_tick.trips == []
    assert eng.state.blind is False
    await eng.stop()


async def test_a_clock_jump_records_missed_not_a_late_sweep(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """The engine was down 09:47-10:17. Those sweeps did not happen and are
    never run late — three `missed` rows, no extra ticks row."""
    s = _settings(tmp_path)
    await _seed(store)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), clock, RecordingNotifier(client), client)
    await eng.start()
    await asyncio.wait_for(eng.run_for(1), 5)

    clock.t = et(10, 20)
    await asyncio.wait_for(eng.run_for(1), 5)

    runs = await _job_runs(store, "tick")
    assert [v for _, v, _ in runs] == ["done", "missed", "missed", "missed"]
    assert [datetime.fromisoformat(a).astimezone(ET).strftime("%H:%M") for _, v, a in runs if v == "missed"] == [
        "09:47", "10:02", "10:17",
    ]
    assert len(await store.ticks_for("2026-09-04")) == 1
    await eng.stop()


async def test_a_held_lock_skips_the_fire_it_does_not_queue_it(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """Overlapping sweeps are the one thing a 15-minute loop must never do:
    the second fire is recorded as a noop, not stacked behind the first."""
    s = _settings(tmp_path)
    await _seed(store)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), clock, RecordingNotifier(client), client)
    await eng.start()

    async with eng.scheduler.lock("tick"):
        await asyncio.wait_for(eng.run_for(1), 5)

    assert [v for _, v, _ in await _job_runs(store, "tick")] == ["noop"]
    assert await store.ticks_for("2026-09-04") == []
    await eng.stop()


# --- blind ------------------------------------------------------------------

async def test_a_dead_token_starts_blind_and_health_says_so(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    d = _fx(tmp_path)
    (d / "unauthorized").touch()
    eng = _engine(s, store, FakeBroker(d, NOW), Clock(et(9, 32)), RecordingNotifier(client), client)

    await eng.start()  # must not raise: a blind engine still runs its loops

    assert eng.state.blind is True
    app = build_app(eng.state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
    ) as c:
        body = (await c.get("/health")).json()
    assert body["ok"] is False and body["blind"] is True
    await eng.stop()


async def test_a_broker_error_is_one_failed_row_and_one_discord_line(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    await _seed(store)
    notifier = RecordingNotifier(client)
    eng = _engine(s, store, BoomBroker(_fx(tmp_path), NOW), Clock(et(9, 32)), notifier, client)
    await eng.start()

    await asyncio.wait_for(eng.run_for(1), 5)

    rows = await store.fetchall(
        "SELECT verdict, detail_json FROM job_runs WHERE job='tick'"
    )
    assert [r["verdict"] for r in rows] == ["failed"]
    assert json.loads(rows[0]["detail_json"])["error"] == "BrokerError"
    assert notifier.posts == ["⚠️ tick failed: BrokerError"]
    await eng.stop()


# --- the other jobs ---------------------------------------------------------

async def test_session_close_writes_the_ledger_row_and_posts(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    await _seed(store)
    notifier = RecordingNotifier(client)
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(16, 4)), notifier, client)
    await eng.start()

    await eng.run_job("session_close")

    row = await store.latest_session_status()
    assert row is not None and row.date == TODAY and row.close_value == D("3781.06")
    assert [v for _, v, _ in await _job_runs(store, "session_close")] == ["done"]
    assert len(notifier.posts) == 1 and notifier.posts[0].startswith("📒 close 2026-09-04")
    await eng.stop()


async def test_session_close_on_a_closed_day_is_a_noop(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """Sunday: the window says it is not a trading day, so there is no close
    to record. A noop row, not a fabricated ledger entry."""
    s = _settings(tmp_path)
    await _seed(store)
    sunday = date(2026, 9, 6)
    clock = Clock(et(16, 4, sunday))
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), clock, RecordingNotifier(client), client)
    await eng.start()

    await eng.run_job("session_close")

    assert [v for _, v, _ in await _job_runs(store, "session_close")] == ["noop"]
    assert (await store.latest_session_status()) is not None  # still just the seed
    assert (await store.latest_session_status()).date == date(2026, 9, 3)  # type: ignore[union-attr]
    await eng.stop()


async def test_expectations_job_posts_the_digest(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    await _seed(store)
    notifier = RecordingNotifier(client)
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(7, 30)), notifier, client)
    await eng.start()

    await eng.run_job("expectations")

    assert [v for _, v, _ in await _job_runs(store, "expectations")] == ["done"]
    assert notifier.posts[0].startswith("expectations: 1 checks")
    await eng.stop()


async def test_backup_vacuums_and_keeps_only_the_newest(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    backups = tmp_path / "backup"
    backups.mkdir()
    old = [backups / f"engine-2026-08-{n:02d}.db" for n in range(1, 16)]
    for p in old:
        p.write_bytes(b"stale")
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(23, 30)), RecordingNotifier(client), client)
    await eng.start()

    await eng.run_job("backup")

    made = backups / "engine-2026-09-04.db"
    assert made.exists() and made.stat().st_size > 0
    kept = sorted(p.name for p in backups.glob("engine-*.db"))
    assert len(kept) == BACKUPS_KEPT and kept[-1] == made.name
    assert not old[0].exists() and not old[1].exists()

    await eng.run_job("backup")  # same day again: nothing to do, no clobber
    assert [v for _, v, _ in await _job_runs(store, "backup")] == ["done", "noop"]
    await eng.stop()


async def test_an_unknown_job_is_refused_before_it_is_dispatched(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(9, 32)), RecordingNotifier(client), client)
    await eng.start()
    with pytest.raises(ValueError, match="unknown job"):
        await eng.run_job("nope")
    await eng.stop()


# --- wiring -----------------------------------------------------------------

async def test_build_engine_routes_to_the_shadow_webhook_with_a_tag(
    tmp_path: Path,
) -> None:
    """shadow.enabled routes to the shadow webhook AND tags the text: the
    channel is the guarantee, the tag is what a human reads."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)["content"]))
        return httpx.Response(204)

    s = _settings(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        eng = build_engine(
            s, broker=FakeBroker(_fx(tmp_path), NOW), clock=lambda: NOW, client=c
        )
        await eng.notifier.post("hello")
    assert seen == [("https://shadow.example/h", "[shadow] hello")]


async def test_trading_days_between_is_the_scheduler_and_the_clocks_answer() -> None:
    """One weekday counter, shared by §3.5's hold clock and the scheduler's
    trading-day test — so a holiday is unknown in exactly one place."""
    assert trading_days_between(date(2026, 9, 4), date(2026, 9, 7)) == 1  # Fri -> Mon
    assert trading_days_between(date(2026, 9, 4), date(2026, 9, 4)) == 0
    assert trading_days_between(date(2026, 9, 7), date(2026, 9, 4)) == 0
    assert trading_days_between(date(2026, 9, 4), date(2026, 9, 11)) == 5


# --- the CLI ----------------------------------------------------------------

def test_run_once_tick_is_a_real_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`tc run --once tick` is the operator's smoke test: paper broker, one
    sweep, one ledger row, exit 0."""
    fx = _fx(tmp_path)
    today = datetime.now(UTC).astimezone(ET).date()
    shutil.copy(FIX / f"hours-{TODAY.isoformat()}.json", fx / f"hours-{today.isoformat()}.json")
    monkeypatch.setenv("TC_MODE", "paper")
    monkeypatch.setenv("TC_FIXTURES", str(fx))
    args = _cli_args(tmp_path)
    assert cli.main([*args, "seed-hwm", "--value", "3800.00", "--recorded-on", "2026-09-03"]) == 0

    assert cli.main([*args, "run", "--once", "tick"]) == 0

    async def rows() -> list[str]:
        s = Store(tmp_path / "engine.db")
        await s.open()
        try:
            return [t.at_et for t in await s.ticks_for(today.isoformat())]
        finally:
            await s.close()

    assert len(asyncio.run(rows())) == 1


def test_run_once_rejects_an_unknown_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TC_MODE", "paper")
    monkeypatch.setenv("TC_FIXTURES", str(_fx(tmp_path)))
    rc = cli.main([*_cli_args(tmp_path), "run", "--once", "sweep-everything"])
    assert rc == 4 and "unknown job" in capsys.readouterr().err


def test_run_refuses_to_bind_every_interface(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The engine's HTTP surface installs a token; it is reachable over
    Tailscale, never over the LAN. Refused before anything is opened."""
    args = _cli_args(tmp_path, bind="0.0.0.0:8080")  # the string under test
    rc = cli.main([*args, "run"])
    err = capsys.readouterr().err
    assert rc == 4 and "engine must bind loopback" in err


def test_run_rejects_a_malformed_bind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main([*_cli_args(tmp_path, bind="8080"), "run"])
    assert rc == 4 and "host:port" in capsys.readouterr().err


# --- restart, retries and the ledger ----------------------------------------

async def test_a_restart_does_not_bury_a_finished_day_in_missed_rows(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """A restarted engine re-enumerates the whole day. The 09:32 sweep the
    *previous process* ran is already in the ledger, and a `missed` row on top
    of it would claim the day never happened.

    The first engine really dispatches that fire — hand-inserting the row
    would test the lookup against a key nothing in production writes.
    """
    s = _settings(tmp_path)
    await _seed(store)
    # 09:32:07 — the loop wakes on a 1 s tick, so a real dispatch is never
    # exactly on the scheduled second. Keying the row on the dispatch instant
    # is precisely what those seven seconds used to break.
    first = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(9, 32).replace(second=7)), RecordingNotifier(client), client)
    await first.start()
    await asyncio.wait_for(first.run_for(1), 5)
    await first.stop()

    # A new process on the same store, started an hour later.
    second = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(10, 20)), RecordingNotifier(client), client)
    await second.start()
    await asyncio.wait_for(second.run_for(1), 5)

    runs = await _job_runs(store, "tick")
    assert [v for _, v, _ in runs] == ["done", "missed", "missed", "missed"]
    assert [datetime.fromisoformat(a).astimezone(ET).strftime("%H:%M") for _, v, a in runs] == [
        "09:32", "09:47", "10:02", "10:17",
    ]
    assert len(await store.ticks_for("2026-09-04")) == 1  # not swept a second time
    await second.stop()


async def test_a_job_run_is_keyed_on_the_fire_not_the_dispatch_instant(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """`started_at` is the scheduled time — the only key on which "this fire
    already ran" can be answered across a restart. The real clock times ride
    in `ended_at` and the detail, so lateness is still on the record."""
    s = _settings(tmp_path)
    await _seed(store)
    late = et(9, 32).replace(second=41)  # a wake-up inside the scheduler's grace
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(late), RecordingNotifier(client), client)
    await eng.start()

    await asyncio.wait_for(eng.run_for(1), 5)

    row = await store.fetchone(
        "SELECT started_at, ended_at, detail_json FROM job_runs WHERE job='tick'"
    )
    assert row is not None
    assert datetime.fromisoformat(row["started_at"]) == et(9, 32)
    assert datetime.fromisoformat(row["ended_at"]) == late
    assert json.loads(row["detail_json"])["dispatched_at"] == late.isoformat()
    assert await store.job_run_exists("tick", et(9, 32)) is True
    await eng.stop()


async def test_a_fallback_window_is_retried_until_the_calendar_answers(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """A guessed calendar says every weekday is a full 09:30-16:00 session.
    Held for a day, that would run session_close and the §3.3/§3.5 clocks
    against a market that closed at 13:00."""
    s = _settings(tmp_path)
    await _seed(store)
    broker = FlakyWindowBroker(_fx(tmp_path), NOW)
    clock = Clock(et(9, 0))
    eng = _engine(s, store, broker, clock, RecordingNotifier(client), client)

    await eng.start()
    assert eng.window_is_fallback is True

    # The retry is on a WINDOW_RETRY_S timer, so the pass that gets the real
    # calendar is the first one after that window has elapsed.
    clock.t = et(9, 0) + timedelta(seconds=WINDOW_RETRY_S)
    await asyncio.wait_for(eng.run_for(1), 5)
    assert eng.window_is_fallback is False
    assert eng.window == await FakeBroker(FIX, NOW).market_window(TODAY)

    await asyncio.wait_for(eng.run_for(1), 5)
    assert broker.window_calls == 2  # retried until it answered, then never again
    await eng.stop()


async def test_a_ledger_write_failure_does_not_take_the_job_down(
    tmp_path: Path, client: httpx.AsyncClient
) -> None:
    """The row is how a sweep is remembered, but losing the row must not also
    lose the sweep — or raise into a task nothing awaits."""
    s = _settings(tmp_path)
    store = UnwritableStore(tmp_path / "engine.db")
    await store.open()
    eng = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(et(9, 32)), RecordingNotifier(client), client)
    await eng.start()

    assert await eng.run_job("tick") == "done"
    assert len(await store.ticks_for("2026-09-04")) == 1  # the sweep still happened
    await eng.stop()


async def test_the_fallback_window_is_not_retried_every_pass(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """The loop wakes once a second and the market calendar does not change
    that fast. Retrying on every pass meant a broker outage at 04:00 produced
    tens of thousands of failed market-hours calls before the open — one HTTP
    round trip and one log line each."""
    s = _settings(tmp_path)
    await _seed(store)
    broker = DeadWindowBroker(_fx(tmp_path), NOW)
    base = et(9, 0)
    clock = Clock(base)
    eng = _engine(s, store, broker, clock, RecordingNotifier(client), client)

    await eng.start()  # the one attempt: it fails, and the guess is taken
    assert broker.window_calls == 1 and eng.window_is_fallback is True

    for seconds in (0, 1, 2):
        clock.t = base + timedelta(seconds=seconds)
        await asyncio.wait_for(eng.run_for(1), 5)
    assert broker.window_calls == 1  # three passes, no second attempt

    clock.t = base + timedelta(seconds=WINDOW_RETRY_S + 1)
    await asyncio.wait_for(eng.run_for(1), 5)
    assert broker.window_calls == 2
    await eng.stop()


# --- the token install path -------------------------------------------------

async def test_a_token_install_reopens_the_broker_and_ends_blind(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """The engine came up before any token existed: every job is BLIND and
    nothing re-opens the broker on its own. `/oauth/callback` awaits
    `on_token_installed`, and only the read it performs may clear `blind` —
    the flag the probe and `/health` are reading."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    (d / "unauthorized").touch()  # no token: every broker call is a 401
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(d, NOW), clock, RecordingNotifier(client), client)
    await eng.start()

    await asyncio.wait_for(eng.run_for(1), 5)
    assert eng.state.blind is True
    assert [t.state for t in await store.ticks_for("2026-09-04")] == ["BLIND"]

    (d / "unauthorized").unlink()  # the phone re-auth lands
    assert eng.state.on_token_installed is not None
    await eng.state.on_token_installed()

    assert eng.state.blind is False
    assert eng.window_is_fallback is False  # the calendar was re-read too

    clock.t = et(9, 47)
    await asyncio.wait_for(eng.run_for(1), 5)
    assert [t.state for t in await store.ticks_for("2026-09-04")] == ["BLIND", "RTH"]

    app = build_app(eng.state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
    ) as c:
        body = (await c.get("/health")).json()
    assert body["ok"] is True and body["blind"] is False
    await eng.stop()


async def test_a_token_install_whose_read_fails_stays_blind(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """A token that installs but does not authenticate — the wrong app's
    token, or a grant revoked at the broker. The file on disk proves nothing,
    so `/health` must keep saying `ok: false`."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    (d / "unauthorized").touch()
    eng = _engine(s, store, FakeBroker(d, NOW), Clock(et(9, 32)), RecordingNotifier(client), client)
    await eng.start()
    assert eng.state.blind is True

    assert eng.state.on_token_installed is not None
    await eng.state.on_token_installed()  # the marker is still there: still 401

    assert eng.state.blind is True
    app = build_app(eng.state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
    ) as c:
        assert (await c.get("/health")).json()["ok"] is False
    await eng.stop()


# --- trips are posted on transition -----------------------------------------

async def test_a_standing_trip_is_posted_once_not_every_sweep(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """A naked position is true every 15 minutes until someone fixes it.
    Re-posting it each sweep is ~26 identical messages an hour, which trains
    the reader to scroll past the one that is new."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    (d / "orders.json").write_text("[]")  # AMH held with no stop
    notifier = RecordingNotifier(client)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(d, NOW), clock, notifier, client)
    await eng.start()

    await eng.run_job("tick")
    clock.t = et(9, 47)
    await eng.run_job("tick")

    assert len(notifier.posts) == 1
    assert notifier.posts[0].startswith("🚨 TICK TRIP")
    assert "watch 4 naked" in notifier.posts[0]
    await eng.stop()


async def test_a_changed_trip_set_posts_again(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """Silence is only safe while nothing has changed. A second, different
    watch tripping is new information and must not be swallowed by the
    dedupe."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    (d / "orders.json").write_text("[]")
    notifier = RecordingNotifier(client)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(d, NOW), clock, notifier, client)
    await eng.start()
    await eng.run_job("tick")

    payload = json.loads((d / "account.json").read_text())
    payload["securitiesAccount"]["isClosingOnlyRestricted"] = True
    (d / "account.json").write_text(json.dumps(payload))
    clock.t = et(9, 47)
    await eng.run_job("tick")

    assert len(notifier.posts) == 2
    assert "watch 1 restriction" in notifier.posts[1]
    await eng.stop()


async def test_clearing_the_trips_posts_one_all_clear(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """The other half of posting on transition: a book that has stopped being
    tripped is worth exactly one line, and a book that was never tripped is
    worth none."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    notifier = RecordingNotifier(client)
    clock = Clock(et(9, 32))
    eng = _engine(s, store, FakeBroker(d, NOW), clock, notifier, client)
    await eng.start()

    await eng.run_job("tick")  # clean book: nothing to say
    assert notifier.posts == []

    (d / "orders.json").write_text("[]")
    clock.t = et(9, 47)
    await eng.run_job("tick")
    assert len(notifier.posts) == 1

    (d / "orders.json").write_text((FIX / "orders.json").read_text())
    clock.t = et(10, 2)
    await eng.run_job("tick")
    assert len(notifier.posts) == 2
    assert notifier.posts[1].startswith("✅ book clean again")

    clock.t = et(10, 17)
    await eng.run_job("tick")
    assert len(notifier.posts) == 2  # still clean: still silent
    await eng.stop()


async def test_a_standing_trip_is_reposted_after_an_hour(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """Posting on transition must not become posting once and never again: a
    trip that is still true an hour later gets said again, marked as standing
    so nobody reads it as a new event."""
    s = _settings(tmp_path)
    await _seed(store)
    d = _fx(tmp_path)
    (d / "orders.json").write_text("[]")
    notifier = RecordingNotifier(client)
    start = et(9, 32)
    clock = Clock(start)
    eng = _engine(s, store, FakeBroker(d, NOW), clock, notifier, client)
    await eng.start()

    await eng.run_job("tick")
    clock.t = start + timedelta(seconds=TRIP_REPOST_S - 1)
    await eng.run_job("tick")
    assert len(notifier.posts) == 1

    clock.t = start + timedelta(seconds=TRIP_REPOST_S)
    await eng.run_job("tick")
    assert len(notifier.posts) == 2
    assert "still tripped" in notifier.posts[1]
    assert "watch 4 naked" in notifier.posts[1]
    await eng.stop()


# --- repeated failure is visible in /health ---------------------------------

async def test_two_consecutive_tick_failures_make_health_not_ok(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """Every job failure is absorbed into a ledger row by design, so nothing
    about a loop that fails every single sweep reached `/health` — the probe
    read a green light over a dead loop. One failure is a transient upstream;
    two in a row is not."""
    s = _settings(tmp_path)
    await _seed(store)
    broker = RecoveringBroker(_fx(tmp_path), NOW)
    eng = _engine(s, store, broker, Clock(et(9, 32)), RecordingNotifier(client), client)
    await eng.start()

    async def health() -> dict[str, Any]:
        app = build_app(eng.state)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as c:
            body: dict[str, Any] = (await c.get("/health")).json()
            return body

    assert await eng.run_job("tick") == "failed"
    body = await health()
    assert body["consecutive_tick_failures"] == 1 and body["ok"] is True

    assert await eng.run_job("tick") == "failed"
    body = await health()
    assert body["consecutive_tick_failures"] == 2 and body["ok"] is False

    broker.fail = False
    assert await eng.run_job("tick") == "done"
    body = await health()
    assert body["consecutive_tick_failures"] == 0 and body["ok"] is True
    await eng.stop()


# --- the bind allowlist -----------------------------------------------------

@pytest.mark.parametrize(
    "bind", ["127.0.0.1:8080", "127.0.0.7:8080", "localhost:8080", "[::1]:8080"]
)
def test_loopback_binds_are_accepted(bind: str) -> None:
    assert check_bind(bind)[1] == 8080


@pytest.mark.parametrize(
    "bind",
    [
        "0.0.0.0:8080",
        "192.168.1.20:8080",
        "[::]:8080",
        "10.0.0.4:8080",
        "engine.local:8080",
    ],
)
def test_everything_but_loopback_is_refused(bind: str) -> None:
    """An allowlist: a denylist naming 0.0.0.0 still accepted the LAN address,
    which is the case that actually exposes /oauth/callback."""
    with pytest.raises(ValueError, match="must bind loopback"):
        check_bind(bind)


def test_run_once_exits_1_when_the_job_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A smoke test that exits 0 on a failed sweep is not a smoke test."""
    fx = _fx(tmp_path)
    today = datetime.now(UTC).astimezone(ET).date()
    shutil.copy(FIX / f"hours-{TODAY.isoformat()}.json", fx / f"hours-{today.isoformat()}.json")
    monkeypatch.setenv("TC_MODE", "paper")
    monkeypatch.setenv("TC_FIXTURES", str(fx))
    monkeypatch.setattr(main, "make_broker", lambda s, token: BoomBroker(fx, NOW))
    args = _cli_args(tmp_path)
    assert cli.main([*args, "seed-hwm", "--value", "3800.00", "--recorded-on", "2026-09-03"]) == 0

    assert cli.main([*args, "run", "--once", "tick"]) == 1


# --- the Claude jobs --------------------------------------------------------

MCP_ENV = (
    "TC_RUNNER_TOKEN=runner-token-not-real\n"
    "TC_MCP_RESEARCH_TOKEN=research-token-not-real\n"
    "TC_MCP_DECIDE_TOKEN=decide-token-not-real\n"
)
SCOUT_AT = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)  # 07:15 ET, inside scout's window
SCOUT_RESULT: dict[str, Any] = {
    "verdict_raw": {"cohort": 3, "observed": 2, "escalations": [], "summary": "SCOUT ok"},
    "result_text": "{}",
    "is_error": False,
    "subtype": "success",
    "num_turns": 5,
    "permission_denials": [],
    "usage": {},
    "duration_s": 9.0,
    "timed_out": False,
}


def _fake_runner(payload: dict[str, Any]) -> RunnerClient:
    def h(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"ok": True, "busy": False})
        return httpx.Response(200, json=payload)

    return RunnerClient(
        "http://runner",
        "runner-token-not-real",
        httpx.AsyncClient(transport=httpx.MockTransport(h)),
        120.0,
    )


def test_every_scheduled_job_in_the_repo_config_is_a_known_job() -> None:
    """config.yml is the deployed schedule. A key naming no job stops the
    container at start, on a box where "it did not come up" is discovered
    whenever someone next looks."""
    import yaml

    cfg = yaml.safe_load((REPO / "config.yml").read_text())
    assert set(cfg["schedule"]) <= set(JOBS)
    # And every Claude job the spec table defines is actually scheduled: a job
    # with a spec and no schedule reads as a working feature and is not one.
    assert set(CLAUDE_JOBS) <= set(cfg["schedule"])


async def test_a_claude_job_dispatches_through_the_job_runner(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    e = _engine(
        s, store, FakeBroker(_fx(tmp_path), NOW), Clock(SCOUT_AT),
        RecordingNotifier(client), client, jobs=JobRunner(
            _fake_runner(SCOUT_RESULT), RecordingNotifier(client), lambda: SCOUT_AT
        ),
    )
    assert await e.run_job("scout", SCOUT_AT) == "done"
    rows = await _job_runs(store)
    assert [r[0] for r in rows] == ["scout"]
    assert rows[0][1] == "done"


async def test_a_claude_job_with_no_runner_is_a_noop_row_not_a_failure(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """An engine deployed without a runner is a supported deployment. A
    `failed` row every weekday at 07:12 for a service nobody installed is how
    a deadman gets muted."""
    s = _settings(tmp_path)
    e = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(SCOUT_AT),
                RecordingNotifier(client), client)
    assert await e.run_job("scout", SCOUT_AT) == "noop"
    assert (await _job_runs(store))[0][1] == "noop"


async def test_start_mounts_the_mcp_surface_and_reads_the_runners_health(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path, env_extra=MCP_ENV)
    e = _engine(
        s, store, FakeBroker(_fx(tmp_path), NOW), Clock(NOW), RecordingNotifier(client),
        client, jobs=JobRunner(_fake_runner(SCOUT_RESULT), RecordingNotifier(client),
                               lambda: NOW),
    )
    await e.start()
    try:
        assert e.mcp is not None
        assert set(e.mcp.servers) == {"research", "decide"}
        assert e.mcp.tokens == s.mcp_tokens()
        assert e.state.runner_ok is True
        # No `allow_stubs`: every declared tool has a real registrar, which is
        # what makes "the role lists what the registry declares" mean anything.
        research = {t.name for t in e.mcp.servers["research"]._tool_manager.list_tools()}
        assert "quotes" in research and "doc_write" in research
        assert "book" not in research  # the research roles hold no account tool
    finally:
        await e.stop()


async def test_no_mcp_tokens_means_no_mount_and_an_engine_that_still_runs(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    s = _settings(tmp_path)
    e = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(NOW),
                RecordingNotifier(client), client)
    await e.start()
    try:
        assert e.mcp is None
        assert build_app(e.state, mcp=e.mcp) is not None
    finally:
        await e.stop()


async def test_start_refuses_a_declared_tool_no_registrar_supplies(
    tmp_path: Path, store: Store, client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stub-filled server satisfies every "live matches declared" check while
    answering nothing, so an unimplemented tool must fail boot, not ship."""
    from tc.mcp.registry import ROLE_TOOLS

    monkeypatch.setitem(ROLE_TOOLS, "research", (*ROLE_TOOLS["research"], "not_a_tool"))
    s = _settings(tmp_path, env_extra=MCP_ENV)
    e = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(NOW),
                RecordingNotifier(client), client)
    with pytest.raises(RuntimeError, match="not_a_tool"):
        await e.start()
    await e.stop()


async def test_the_mounted_research_role_answers_ping_only_with_its_bearer(
    tmp_path: Path, store: Store, client: httpx.AsyncClient
) -> None:
    """The engine's own wiring, over the wire: the mount exists, the gate is
    on it, and the lifespan entered the session manager."""
    s = _settings(tmp_path, env_extra=MCP_ENV)
    e = _engine(s, store, FakeBroker(_fx(tmp_path), NOW), Clock(NOW),
                RecordingNotifier(client), client)
    await e.start()
    app = build_app(e.state, mcp=e.mcp)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)

            def in_process(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
            ) -> httpx.AsyncClient:
                # The MCP client speaks to this app over ASGI, so no port is
                # bound and no other test can collide with one.
                return httpx.AsyncClient(
                    transport=transport, headers=headers, timeout=timeout, auth=auth
                )

            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8080"
            ) as c:
                bare = await c.post("/mcp/research/", json={})
                assert bare.status_code == 401
                async with streamablehttp_client(
                    # A port in the Host header: the MCP transport's DNS-
                    # rebinding guard allows `127.0.0.1:*`, not a bare host.
                    "http://127.0.0.1:8080/mcp/research/",
                    headers={"Authorization": "Bearer research-token-not-real"},
                    httpx_client_factory=in_process,
                ) as (r_, w_, _id):
                    async with ClientSession(r_, w_) as session:
                        await session.initialize()
                        res = await session.call_tool("ping", {})
        assert res.isError is False
        assert res.structuredContent is not None
        assert res.structuredContent["role"] == "research"
    finally:
        await e.stop()


def test_run_once_accepts_a_claude_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tc run --once scout` is the operator's way to fire a research pass by
    hand. Before the six jobs joined JOBS it was refused as an unknown job."""
    fx = _fx(tmp_path)
    monkeypatch.setenv("TC_MODE", "paper")
    monkeypatch.setenv("TC_FIXTURES", str(fx))
    monkeypatch.setattr(main, "_now", lambda: SCOUT_AT)

    async def fake_run(
        self: RunnerClient, spec: Any, *, prompt_extra: str = ""
    ) -> Any:
        from tc.jobs.dispatch import RunnerReply, RunResultView

        return RunnerReply(result=RunResultView.model_validate(SCOUT_RESULT))

    async def fake_health(self: RunnerClient) -> bool:
        return True

    monkeypatch.setattr(RunnerClient, "run", fake_run)
    monkeypatch.setattr(RunnerClient, "health", fake_health)
    args = _cli_args(tmp_path, env_extra=MCP_ENV)
    assert cli.main([*args, "run", "--once", "scout"]) == 0
