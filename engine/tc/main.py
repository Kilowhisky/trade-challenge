"""The engine: one process, one loop, one lock per job.

This module is the only place where the scheduler, the loops, the broker, the
store and the two side channels meet. Everything below it is testable without
a clock or a network; everything above it is `tc run`.

Four properties it exists to hold:

* **A fire that ran writes exactly one `job_runs` row, whatever happened.**
  Done, noop, failed or missed — the ledger is how an unattended operator
  reconstructs a day, and a job that ran without leaving a row is
  indistinguishable from one that never fired (CLAUDE.md §0: absence of action
  is never evidence that nothing needed doing).
* **Down is recorded as `missed`, never as a late sweep.** `Scheduler.mark_missed`
  supplies the fires the process slept through and they are written, never
  run: a 09:47 tick executed at 10:20 would be a ledger row claiming a sweep
  that did not happen.
* **Broker trouble never takes the loop down.** `start()` degrades to BLIND
  (fallback window, no account hash) rather than raising, and `_dispatch`
  turns any exception from a job into one `failed` row, one healthchecks
  ping and one Discord line. Store and config trouble *do* raise: a database
  that will not open is not a degraded engine, it is no engine.
* **One job never overlaps itself.** Each job holds its own lock; a fire that
  finds it held is recorded as a skipped noop and dropped, not queued behind
  the run already going — a queue would turn one slow sweep into a backlog
  of sweeps all claiming times that have passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import signal
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import uvicorn

from tc import __version__
from tc.broker.client import Broker, BrokerError, BrokerUnauthorized, SchwabBroker
from tc.broker.fake import FakeBroker
from tc.broker.models import MarketWindow
from tc.broker.token import TokenStore
from tc.clock import ET, fallback_window, trading_days_between
from tc.config import Settings
from tc.http.app import EngineState, build_app
from tc.loops.expectations import digest, run_expectations
from tc.loops.reconcile import reconcile
from tc.loops.session import close_session
from tc.loops.tick import run_tick
from tc.loops.token import token_check
from tc.notify import Notifier, Pinger
from tc.rules.model import Rules
from tc.scheduler import Fire, Scheduler, ScheduleSpec
from tc.store.db import Store, Verdict

log = logging.getLogger(__name__)

# The job table. A name not in here is a config error, not a job that quietly
# never runs: `Engine.start` rejects the schedule and `--once` rejects the
# argument, both before anything is opened.
JOBS: tuple[str, ...] = ("tick", "session_close", "token_check", "expectations", "backup")

BACKUPS_KEPT = 14  # ~3 weeks of trading days; the store is small and the disk is not
LOOP_INTERVAL_S = 1.0


@runtime_checkable
class _Lifecycle(Protocol):
    """The half of the broker that is not in the `Broker` protocol. The real
    `SchwabBroker` holds an HTTP client to open and close; `FakeBroker` holds
    a directory and has neither, so the engine asks rather than assumes."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...


class Engine:
    def __init__(
        self,
        settings: Settings,
        *,
        broker: Broker,
        store: Store,
        token: TokenStore,
        notifier: Notifier,
        pinger: Pinger,
        clock: Callable[[], datetime],
        sleep_s: float = LOOP_INTERVAL_S,
    ) -> None:
        self._s = settings
        self._broker = broker
        self._store = store
        self._token = token
        self._pinger = pinger
        self._clock = clock
        self._sleep_s = sleep_s
        # Rules load here, not in start(): a rules.yml that will not parse is
        # a config failure the operator must see at once, and loading it early
        # means every job below can treat `self._rules` as present.
        self._rules = Rules.load(settings.engine.repo_dir / "rules.yml")
        self._leveraged = set(settings.engine.leveraged_symbols)
        self._window: MarketWindow | None = None
        # A fallback window is a guess (every weekday trades, 09:30-16:00). It
        # is held only until the broker will answer: an early close read as a
        # full session would have session_close and the §3.3/§3.5 clocks
        # working from a calendar the market does not share.
        self._window_is_fallback = False
        self._account_hash: str | None = None
        self._tasks: set[asyncio.Task[Verdict | None]] = set()
        self._stopping = False
        self.notifier = notifier
        self.scheduler = Scheduler({}, self._trading_day)
        self.state = EngineState(
            token=token,
            store=store,
            started_at=clock(),
            now=clock,
            version=__version__,
            shadow=settings.shadow.enabled,
        )

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Store, schedule, broker, window, account hash — in that order.

        Broker trouble is absorbed at every step: the engine that cannot read
        the account is exactly the engine that most needs its token loop
        running.
        """
        await self._store.open()
        self.scheduler = Scheduler(
            {job: ScheduleSpec.parse(text) for job, text in self._s.schedule.items()},
            self._trading_day,
        )
        unknown = sorted(set(self.scheduler.specs) - set(JOBS))
        if unknown:
            raise ValueError(f"schedule names jobs that do not exist: {', '.join(unknown)}")
        await self._open_broker()
        await self._refresh_window(self._et(self._clock()).date())
        self._account_hash = await self._resolve_hash()

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._close_broker()
        finally:
            # The store is the ledger: a broker that will not close cleanly
            # must not cost us the WAL checkpoint on the way out.
            await self._store.close()

    async def run_forever(self) -> None:
        await self.run_for(None)

    async def run_for(self, iterations: int | None) -> None:
        """`iterations=None` is the service; a number is the same loop body,
        bounded, for tests and operators.

        A bounded run waits for the fires it started before returning — jobs
        run as tasks, so a `run_for(1)` that returned the instant it created
        one would return before the work it exists to do had happened.
        """
        n = 0
        while not self._stopping and (iterations is None or n < iterations):
            try:
                await self._pass(self._clock())
            except Exception:
                # The loop body itself failing (a store write for a `missed`
                # row, say) must not end the process: log it and take the next
                # pass. Jobs have their own handling in _dispatch.
                log.exception("engine loop pass failed")
            n += 1
            if self._sleep_s > 0:
                await asyncio.sleep(self._sleep_s)
        if iterations is not None:
            await self._drain()

    async def run_job(self, job: str, at: datetime | None = None) -> Verdict:
        """One job, now, outside the schedule — `tc run --once` and tests.
        Returns the recorded verdict so a caller can exit non-zero on
        `failed`."""
        if job not in JOBS:
            raise ValueError(f"unknown job {job!r} (one of {', '.join(JOBS)})")
        return await self._dispatch(Fire(job, at or self._clock()))

    # --- the loop ----------------------------------------------------------

    async def _pass(self, now: datetime) -> None:
        today = self._et(now).date()
        if self._window is None or self._window.date != today:
            await self._refresh_window(today)
            self.scheduler.prune(today)
        elif self._window_is_fallback:
            # One retry per pass until the real calendar answers, then never
            # again for this day.
            await self._refresh_window(today)
        for fire in self.scheduler.mark_missed(now):
            # A restart re-enumerates the whole day, so most of what this
            # process "missed" was run by the process before it. Recording a
            # `missed` row over a `done` one would turn every restart into a
            # ledger claiming the day never happened.
            if await self._store.job_run_exists(fire.job, fire.at):
                continue
            log.warning("missed %s scheduled for %s", fire.job, fire.at)
            await self._store.record_job_run(fire.job, fire.at, fire.at, "missed", {})
        for fire in self.scheduler.due(now):
            self._spawn(self._run_fire(fire))

    def _spawn(self, coro: Coroutine[Any, Any, Verdict | None]) -> None:
        task: asyncio.Task[Verdict | None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[Verdict | None]) -> None:
        """Nothing awaits a fire task, so without this an exception escaping
        `_run_fire` itself would surface only as asyncio's "exception was
        never retrieved" at garbage-collection time, hours later and
        attributed to nothing."""
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("fire task ended in an unhandled %s", type(exc).__name__, exc_info=exc)

    async def _drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _record(
        self, job: str, started: datetime, ended: datetime, verdict: Verdict,
        detail: dict[str, Any],
    ) -> None:
        """The ledger write, which must never be the thing that takes a job
        down. A store that will not accept the row is a real failure — but it
        is one the health check and the deadman report, not one worth losing
        the sweep that already happened over."""
        try:
            await self._store.record_job_run(job, started, ended, verdict, detail)
        except Exception:
            log.exception("recording %s as %s failed", job, verdict)

    async def _run_fire(self, fire: Fire) -> Verdict:
        lock = self.scheduler.lock(fire.job)
        if lock.locked():
            # Skipped, not queued: the previous run is still sweeping, and a
            # second sweep behind it would report a moment that has passed.
            log.warning("%s still running; skipping the fire at %s", fire.job, fire.at)
            await self._record(
                fire.job, fire.at, self._clock(), "noop", {"skipped": "lock held"}
            )
            return "noop"
        async with lock:
            return await self._dispatch(fire)

    async def _dispatch(self, fire: Fire) -> Verdict:
        started = self._clock()
        await self._pinger.start(fire.job)
        try:
            verdict, detail = await self._execute(fire.job, started)
        except Exception as e:
            # Every failure mode of every loop lands here, a non-401
            # BrokerError included (run_tick's BLIND path covers 401s only).
            # The class name is the whole report: an exception message can
            # carry a URL, a token fragment or an account number.
            name = type(e).__name__
            log.exception("job %s failed", fire.job)
            await self._record(fire.job, started, self._clock(), "failed", {"error": name})
            await self._pinger.fail(fire.job, "failed")
            await self.notifier.post(f"⚠️ {fire.job} failed: {name}")
            return "failed"
        await self._record(fire.job, started, self._clock(), verdict, detail)
        await self._pinger.ok(fire.job, verdict)
        return verdict

    async def _execute(self, job: str, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        if job == "tick":
            return await self._job_tick(now)
        if job == "session_close":
            return await self._job_session_close(now)
        if job == "token_check":
            return await self._job_token_check(now)
        if job == "expectations":
            return await self._job_expectations(now)
        if job == "backup":
            return await self._job_backup(now)
        raise ValueError(f"unknown job {job!r}")

    # --- jobs --------------------------------------------------------------

    async def _job_tick(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        res = await run_tick(
            broker=self._broker,
            store=self._store,
            rules=self._rules,
            reserve=self._s.engine.reserve_usd,
            account_hash=self._account_hash,
            now=now,
            window=self._window,
            orders_from=self._s.engine.orders_from,
            leveraged=self._leveraged,
            trading_days_between=trading_days_between,
        )
        self.state.last_tick = res
        # The tick is the engine's most frequent broker read, so it is also
        # the freshest evidence of whether the token still works. /health
        # reads both fields.
        self.state.blind = res.row.state == "BLIND"
        if not self.state.blind:
            self.state.last_broker_read_ok_at = now
        if not res.trips:
            return "done", {}
        # Trips are the loop working, not the job failing: a sweep that finds
        # a naked position did its job. The verdict stays `done` so the
        # expectations layer's "no failed runs" check keeps meaning what it
        # says, and the trips ride in the detail.
        lines = [f"🚨 TICK TRIP {res.row.at_et}"]
        lines += [f"  watch {t.watch} {t.name}: {t.detail}" for t in res.trips]
        await self.notifier.post("\n".join(lines))
        return "done", {"trips": [t.model_dump() for t in res.trips]}

    async def _job_session_close(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        today = self._et(now).date()
        w = self._window
        if w is not None and w.date == today and not w.is_trading_day:
            # A holiday the schedule's weekday test could not see (it defers
            # to this same window, but a fallback window says every weekday
            # trades). No close happened, so none is written.
            return "noop", {"reason": "not a trading day"}
        h = await self._hash()
        # §4.5: the close is read fresh, never taken from the last tick — a
        # stop can fill between 15:47 and 16:04.
        view = await reconcile(self._broker, self._store, h, now, self._s.engine.orders_from)
        row = await close_session(self._store, self._rules, today, view.account.liquidation_value)
        ratchet = " ⬆ new high-water" if row.ratcheted else ""
        await self.notifier.post(
            f"📒 close {row.date} value {row.close_value} hwm {row.hwm} "
            f"drawdown {row.drawdown_pct}% {row.level}{ratchet}"
        )
        return "done", {
            "close_value": str(row.close_value),
            "hwm": str(row.hwm),
            "level": row.level,
            "ratcheted": row.ratcheted,
        }

    async def _job_token_check(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        # The loop posts for itself (it owns the dedupe and the standing
        # alert), so nothing is posted here.
        rep = await token_check(self._token, self._store, self.notifier, now)
        return "done", {"state": rep.state, "days_until_dead": rep.days_until_dead}

    async def _job_expectations(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        results = await run_expectations(
            self._store, self._token, self._s.expectations, self._et(now).date()
        )
        await self.notifier.post(digest(results))
        return "done", {
            "checks": len(results),
            "breached": [r.name for r in results if not r.ok],
        }

    async def _job_backup(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        d = self._et(now).date()
        path = self._s.engine.data_dir / "backup" / f"engine-{d.isoformat()}.db"
        if path.exists():
            # Today's backup is already on disk. VACUUM INTO would refuse the
            # existing file anyway; saying noop is more honest than failing.
            return "noop", {"reason": "backup exists", "path": str(path)}
        await self._store.backup_to(path)
        return "done", {"path": str(path), "pruned": _prune_backups(path.parent, BACKUPS_KEPT)}

    # --- broker/window helpers ---------------------------------------------

    @property
    def window(self) -> MarketWindow | None:
        """Today's market window as the engine currently understands it."""
        return self._window

    @property
    def window_is_fallback(self) -> bool:
        return self._window_is_fallback

    def _et(self, dt: datetime) -> datetime:
        return dt.astimezone(ET)

    def _trading_day(self, d: date) -> bool:
        """The scheduler's `weekdays` test. Today is answered from the cached
        window (the broker's own calendar, holidays included); any other day
        falls back to "weekday", which is all a blind engine can know."""
        w = self._window
        if w is not None and w.date == d:
            return w.is_trading_day
        return fallback_window(d).is_trading_day

    async def _refresh_window(self, d: date) -> None:
        """The broker's calendar if it will answer, the weekday guess if it
        will not — and the guess is retried on the next pass, because a
        transient failure at 04:00 should not leave the whole session running
        on an assumed holiday calendar."""
        try:
            self._window = await self._broker.market_window(d)
            self._window_is_fallback = False
        except (BrokerError, OSError) as e:
            # OSError covers the fixture broker in paper mode reading a day it
            # has no hours file for. Either way: no calendar, so assume the
            # weekday window and keep monitoring rather than stopping.
            log.warning(
                "market_window(%s) failed (%s); using the fallback window", d, type(e).__name__
            )
            self._window = fallback_window(d)
            self._window_is_fallback = True

    async def _resolve_hash(self) -> str | None:
        try:
            hashes = await self._broker.account_hashes()
        except BrokerUnauthorized:
            log.warning("account hash unavailable: token dead or absent — starting BLIND")
            self.state.blind = True
            return None
        except BrokerError as e:
            # Reachable but broken. Not blind — the token may be fine — and
            # each job re-resolves, so the next one may well succeed.
            log.warning("account hash unavailable (%s); jobs will retry", type(e).__name__)
            return None
        return hashes[0] if hashes else None

    async def _hash(self) -> str:
        """For a job that cannot proceed without one. Raises, and the raise is
        the point: `_dispatch` records it as a failed run."""
        if self._account_hash is None:
            hashes = await self._broker.account_hashes()
            if not hashes:
                raise BrokerError("no account hashes in the token's scope")
            self._account_hash = hashes[0]
        return self._account_hash

    async def _open_broker(self) -> None:
        if not isinstance(self._broker, _Lifecycle):
            return
        try:
            await self._broker.open()
        except BrokerError as e:
            log.warning("broker open failed (%s); starting BLIND", type(e).__name__)
            self.state.blind = True

    async def _close_broker(self) -> None:
        if isinstance(self._broker, _Lifecycle):
            await self._broker.close()


def _prune_backups(directory: Path, keep: int) -> list[str]:
    """Newest `keep` survive. Named `engine-YYYY-MM-DD.db`, so name order is
    date order — no mtime, which a restore or an rsync would rewrite."""
    files = sorted(directory.glob("engine-*.db"))
    stale = files[: max(0, len(files) - keep)]
    for p in stale:
        p.unlink()
    return [p.name for p in stale]


# --- wiring -----------------------------------------------------------------


def _is_loopback(host: str) -> bool:
    """An allowlist, not a denylist. `127.0.0.0/8`, `::1` and the literal
    `localhost` are the whole of it: a denylist naming the obvious offenders
    would still have accepted a LAN address, which is the case that actually
    exposes the callback."""
    h = host.strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        # A hostname the engine cannot prove is loopback. Resolving it would
        # make the answer depend on DNS at startup; refusing is the safe half.
        return False


def check_bind(bind: str) -> tuple[str, int]:
    """`host:port`, loopback only.

    The HTTP surface carries `/oauth/callback`, which installs a Schwab token
    from whatever hits it. It is reached over Tailscale, never over the LAN,
    so anything but loopback is refused here rather than left to a firewall.
    """
    host, sep, port = bind.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise ValueError(f"engine.http_bind must be host:port, got {bind!r}")
    if not _is_loopback(host):
        raise ValueError("engine must bind loopback")
    return host, int(port)


def token_store(settings: Settings) -> TokenStore:
    return TokenStore(
        settings.engine.data_dir / "token.json",
        settings.token,
        settings.schwab_app_key,
        settings.schwab_app_secret,
    )


def make_broker(settings: Settings, token: TokenStore) -> Broker:
    """`TC_MODE=paper` swaps in the fixture broker — the operator smoke test,
    and the only way to exercise `tc run` without touching the account."""
    if os.environ.get("TC_MODE") == "paper":
        fixtures = os.environ.get("TC_FIXTURES")
        if not fixtures:
            raise ValueError("TC_MODE=paper requires TC_FIXTURES=<fixture dir>")
        return FakeBroker(Path(fixtures), datetime.now(UTC))
    return SchwabBroker(token, settings.schwab_app_key, settings.schwab_app_secret)


def build_engine(
    settings: Settings,
    *,
    broker: Broker,
    clock: Callable[[], datetime],
    client: httpx.AsyncClient,
) -> Engine:
    shadow = settings.shadow.enabled
    webhook = settings.discord_shadow_webhook_url if shadow else settings.discord_webhook_url
    if shadow and webhook is None:
        # Not an error: shadow mode exists precisely to keep messages out of
        # #llm-yolo. But silence is a state an operator must be told about,
        # because every notification below will now be dropped.
        log.warning("shadow mode is on with no shadow webhook configured: Discord is silent")
    return Engine(
        settings,
        broker=broker,
        store=Store(settings.engine.data_dir / "engine.db"),
        token=token_store(settings),
        notifier=Notifier(
            None if webhook is None else str(webhook), client, "[shadow] " if shadow else ""
        ),
        pinger=Pinger(
            None
            if settings.healthchecks_base_url is None
            else str(settings.healthchecks_base_url),
            client,
        ),
        clock=clock,
    )


class _Server(uvicorn.Server):
    """uvicorn installs its own SIGINT/SIGTERM handlers inside `serve()`.
    They would replace the engine's and stop the web surface without ever
    calling `Engine.stop()` — no broker close, no store close, in-flight jobs
    killed by process exit. The engine owns the signals; the server exits when
    it is told to."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _now() -> datetime:
    return datetime.now(UTC)


async def serve(settings: Settings) -> None:
    host, port = check_bind(settings.engine.http_bind)
    token = token_store(settings)
    broker = make_broker(settings, token)
    async with httpx.AsyncClient() as client:
        engine = build_engine(settings, broker=broker, clock=_now, client=client)
        await engine.start()
        server = _Server(
            uvicorn.Config(
                build_app(engine.state), host=host, port=port, log_level="warning"
            )
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        http = asyncio.create_task(server.serve())
        loop_task = asyncio.create_task(engine.run_forever())
        waiter = asyncio.create_task(stop.wait())
        try:
            # Any of the three ending ends the process: a dead scheduler with
            # a live web surface would answer /health from a corpse.
            await asyncio.wait(
                [http, loop_task, waiter], return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            waiter.cancel()
            server.should_exit = True
            loop_task.cancel()
            await asyncio.gather(http, loop_task, waiter, return_exceptions=True)
            await engine.stop()


async def run_once(settings: Settings, job: str) -> int:
    """`tc run --once JOB`: start, one job, stop. No HTTP, no schedule."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r} (one of {', '.join(JOBS)})")
    token = token_store(settings)
    broker = make_broker(settings, token)
    async with httpx.AsyncClient() as client:
        engine = build_engine(settings, broker=broker, clock=_now, client=client)
        await engine.start()
        try:
            verdict = await engine.run_job(job)
        finally:
            await engine.stop()
    # The operator smoke test is only a smoke test if a failed job fails the
    # command: `tc run --once tick` in a deploy script must not print a
    # traceback into the log and then exit 0.
    return 1 if verdict == "failed" else 0
