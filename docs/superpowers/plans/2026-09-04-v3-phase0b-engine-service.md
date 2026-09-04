# v3 Phase 0b — Engine Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 0a foundation into a running engine process on the Pi that owns every *mechanical* loop — scheduler, reconciliation, the tick watches, session close and the high-water mark, the §3.3/§3.5 clocks, the token lifecycle with the phone re-auth callback, expectations — with outbound Discord and healthchecks, an HTTP `/health` + `/oauth/callback` + `/api`, a shadow-diff tool against the old ledgers, a docker service, and a host probe outside docker.

**Architecture:** One asyncio process (`tc run`) wiring `Store`, `Broker`, `TokenStore`, `Scheduler`, the loops, a `Notifier` and `Pinger`, and a Starlette app on `127.0.0.1:8080`. Loops are plain `async` functions with explicit inputs and typed results; the scheduler is data in `config.yml`, not cron. Nothing here places, replaces or cancels an order — the order path is Plan 1 (`orders/`), and the Claude runner + MCP server are Plan 0c (`runner/`, `mcp/`, `jobs/`). Those three plans together retire `scripts/*.sh`, `docker/crontab` and `schwab-mcp`.

**Tech Stack:** Python 3.12, `schwab-py` 1.5.1, `pydantic` 2 + `pydantic-settings`, `PyYAML`, `aiosqlite`, `httpx`, `starlette` + `uvicorn`, `hypothesis`, `pytest` + `pytest-asyncio`, `mypy --strict`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` — §3 (topology, scheduler), §6 (state), §7 (observability), §8 (token, blind mode), §9 (config, layout), §10 (testing), §11 (Phase 0 shadow, Phase 1 cutover). Ported behaviour comes from `.claude/commands/tick.md` §B–§C and `.claude/agents/session-close.md` §3, quoted inside the tasks.

## Global Constraints

- Python **3.12**; gate after every task: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`. A path argument to pytest drops `asyncio_mode`; focused runs use `pytest -q -c pyproject.toml ../tests/engine/unit/test_x.py`.
- Money is `decimal.Decimal`; caps floor to cents; never `float` for money.
- Every model: `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")`.
- **No rule number is hard-coded** anywhere under `engine/`: every threshold comes through `tc.rules.model.Rules`. The 900.00 reserve comes from `Settings.engine.reserve_usd`.
- **No write-shaped broker call exists in this plan.** `Broker` stays the Phase 0a read-only protocol. `grep -rn "place_order\|replace_order\|cancel_order" engine/` must stay empty.
- No `subprocess`, no shelling out, anywhere under `engine/` or `host/`. `host/healthprobe.py` is **stdlib only** (it runs on the Pi's system Python outside docker).
- Never commit an account number, account hash, order id, token or secret (`CLAUDE.md §7.4`). Test fixtures use `HASH_REDACTED` and synthetic order ids in the 1000000000001 range.
- Work on branch `feat/v3-engine-0b` from `main`. Stage explicit paths; never `git add -A`.
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The engine on the server runs in **shadow** (`engine.shadow: true`): it posts only to the shadow webhook and never to `#llm-yolo`. Phase 1 flips one flag.

## File Structure

```
engine/tc/
  config.py               MODIFY: ScheduleConfig, ShadowConfig, EngineConfig.repo_dir, Secrets.discord_shadow_webhook_url
  scheduler.py            CREATE: ScheduleSpec parsing, Scheduler (due/missed/locks)
  notify.py               CREATE: Notifier (Discord webhook) + Pinger (healthchecks)
  store/db.py             MODIFY: latest_positions, first_seen, ticks_for, session_status_for, position_days_held
  loops/__init__.py       CREATE
  loops/reconcile.py      CREATE: BookView from account+orders (§4.5)
  loops/session.py        CREATE: seed_hwm, close_session (§7.2, §3.6 ratchet)
  loops/tick.py           CREATE: run_tick: phase, BLIND, watches 1–7, TickRow, Trip list
  loops/clocks.py         CREATE: option DTE from OSI symbol, leveraged hold sessions
  loops/token.py          CREATE: token_check: reauth prompt, dead alert, state
  loops/expectations.py   CREATE: declarative checks over the store
  http/__init__.py        CREATE
  http/app.py             CREATE: /health /oauth/callback /api/status /api/ticks
  main.py                 CREATE: Engine: wiring, blind mode, shutdown
  shadow.py               CREATE: diff engine rows vs old status/ticks ledgers
  cli.py                  MODIFY: run, seed-hwm, shadow-diff
engine/pyproject.toml     MODIFY: starlette, uvicorn
config.yml                MODIFY: schedule, shadow, repo_dir
docker/docker-compose.yml MODIFY: engine service
docker/Dockerfile.engine  CREATE
host/healthprobe.py       CREATE (stdlib)
host/tc-healthprobe.service, host/tc-healthprobe.timer  CREATE
tests/engine/unit/        test_scheduler.py test_notify.py test_reconcile.py test_session.py
                          test_tick.py test_clocks.py test_token_loop.py test_expectations.py
                          test_http.py test_main.py test_shadow.py test_healthprobe.py
tests/engine/fixtures/broker/  reused; add quotes-stale.json, orders-partial.json, hours-2026-09-04.json
tests/engine/fixtures/legacy/  status-2026-09-03.md, ticks-2026-09-03.tsv (synthetic)
```

---

### Task 1: Schedule config and `Scheduler`

**Files:**
- Modify: `engine/tc/config.py`
- Create: `engine/tc/scheduler.py`
- Modify: `config.yml`
- Test: `tests/engine/unit/test_scheduler.py`, `tests/engine/unit/test_config.py`

**Interfaces:**
- Consumes: `tc.clock.ET`, `tc.clock.in_window`, `tc.broker.models.MarketWindow`.
- Produces:
  - `ScheduleSpec.parse(text: str) -> ScheduleSpec`; `ScheduleSpec.fires_on(d: date) -> list[datetime]` (aware ET datetimes for that date, empty on non-matching days).
  - `class Scheduler` with `__init__(self, specs: dict[str, ScheduleSpec], trading_day: Callable[[date], bool])`, `due(self, now: datetime) -> list[Fire]`, `mark_missed(self, now: datetime) -> list[Fire]`, `lock(self, job: str) -> asyncio.Lock`.
  - `Fire(job: str, at: datetime)` frozen dataclass.
  - `EngineConfig.repo_dir: Path`, `FileConfig.schedule: dict[str, str]`, `FileConfig.shadow: ShadowConfig(enabled: bool)`, `Secrets.discord_shadow_webhook_url: AnyHttpUrl | None`, exposed on `Settings` as `schedule`, `shadow`, `discord_shadow_webhook_url`.

Schedule grammar (spec §3: "explicit ET times per job … expanded at load"), three forms, all ET:
- `every 15m 09:32-15:47 weekdays` — fires at 09:32, 09:47, … 15:47 on trading days.
- `at 16:04 weekdays` — one fire per trading day.
- `at 07:40 sat` — one fire on Saturdays (`sat`, `sun`, `daily`, `weekdays`).

`weekdays` means trading days (the `trading_day` callable decides; a weekday holiday is not a fire). `daily`, `sat`, `sun` ignore the calendar.

- [ ] **Step 1: Write the failing config test**

Append to `tests/engine/unit/test_config.py`:

```python
def test_schedule_and_shadow_sections(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "schedule: {tick: 'every 15m 09:32-15:47 weekdays', session_close: 'at 16:04 weekdays'}\n"
        "shadow: {enabled: true}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setenv("TC_DISCORD_SHADOW_WEBHOOK_URL", "https://discord.test/shadow")
    from tc.config import load_settings
    s = load_settings(cfg)
    assert s.schedule["tick"] == "every 15m 09:32-15:47 weekdays"
    assert s.shadow.enabled is True
    assert s.engine.repo_dir == Path("/r")
    assert str(s.discord_shadow_webhook_url) == "https://discord.test/shadow"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py -k schedule`
Expected: FAIL — `extra_forbidden` on `schedule`.

- [ ] **Step 3: Extend config.py**

In `engine/tc/config.py`:

```python
class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/New_York"
    data_dir: Path
    repo_dir: Path                      # rules.yml, prompts, the manual (read-only mount)
    http_bind: str = "127.0.0.1:8080"
    reserve_usd: Decimal = Decimal("900.00")


class ShadowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: EngineConfig
    token: TokenConfig
    schedule: dict[str, str] = Field(default_factory=dict)
    shadow: ShadowConfig = Field(default_factory=ShadowConfig)
```

Add to `Secrets`: `discord_shadow_webhook_url: AnyHttpUrl | None = None`.
Add to `Settings`: fields `schedule: dict[str, str]`, `shadow: ShadowConfig`, and property `discord_shadow_webhook_url`. In `load_settings` pass `schedule=file_cfg.schedule, shadow=file_cfg.shadow`.

Update `config.yml`:

```yaml
engine:
  timezone: America/New_York
  data_dir: /data
  repo_dir: /app/repo          # the checkout, read-only: rules.yml, .claude/, CLAUDE.md
  http_bind: 127.0.0.1:8080
  reserve_usd: "900.00"        # CLAUDE.md header: the one fixed dollar quantity
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://REPLACE-ME.ts.net/oauth/callback
shadow:
  enabled: true                # Phase 0: shadow webhook only, no #llm-yolo, no orders
schedule:                      # ET. weekdays = trading days per get_market_hours
  tick:          "every 15m 09:32-15:47 weekdays"
  session_close: "at 16:04 weekdays"
  token_check:   "at 07:05 daily"
  expectations:  "at 07:30 daily"
  backup:        "at 23:30 daily"
```

Fix every existing test/fixture that builds `EngineConfig` without `repo_dir` (grep `data_dir` under `tests/engine`).

- [ ] **Step 4: Run config tests**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py`
Expected: PASS.

- [ ] **Step 5: Write the failing scheduler tests**

Create `tests/engine/unit/test_scheduler.py`:

```python
from datetime import date, datetime, time
import pytest
from tc.clock import ET
from tc.scheduler import Fire, ScheduleSpec, Scheduler


def et(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=ET)


def test_every_expands_inclusive_end():
    s = ScheduleSpec.parse("every 15m 09:32-15:47 weekdays")
    fires = s.fires_on(date(2026, 9, 3))  # Thursday
    assert fires[0] == et(date(2026, 9, 3), 9, 32)
    assert fires[-1] == et(date(2026, 9, 3), 15, 47)
    assert len(fires) == 26


def test_at_single_fire_and_day_filters():
    assert ScheduleSpec.parse("at 16:04 weekdays").fires_on(date(2026, 9, 5)) == []  # Saturday
    assert ScheduleSpec.parse("at 07:40 sat").fires_on(date(2026, 9, 5)) == [et(date(2026, 9, 5), 7, 40)]
    assert ScheduleSpec.parse("at 07:05 daily").fires_on(date(2026, 9, 6)) == [et(date(2026, 9, 6), 7, 5)]


@pytest.mark.parametrize("bad", ["every 15 09:32-15:47", "at 25:00 daily", "at 07:00 monday", "weekly"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        ScheduleSpec.parse(bad)


def test_weekdays_defers_to_trading_day_callable():
    s = ScheduleSpec.parse("at 16:04 weekdays")
    sched = Scheduler({"close": s}, trading_day=lambda d: False)  # holiday
    assert sched.due(et(date(2026, 9, 7), 16, 4)) == []


def test_due_fires_once_and_records_missed():
    s = ScheduleSpec.parse("every 15m 09:32-15:47 weekdays")
    sched = Scheduler({"tick": s}, trading_day=lambda d: True)
    d = date(2026, 9, 3)
    assert sched.due(et(d, 9, 32)) == [Fire("tick", et(d, 9, 32))]
    assert sched.due(et(d, 9, 32)) == []                       # same minute, once
    assert sched.due(et(d, 9, 40)) == []                       # nothing between fires
    # engine was down 09:47..10:17: those are missed, not run late
    missed = sched.mark_missed(et(d, 10, 20))
    assert [f.at for f in missed] == [et(d, 9, 47), et(d, 10, 2), et(d, 10, 17)]
    assert sched.due(et(d, 10, 32)) == [Fire("tick", et(d, 10, 32))]


def test_due_tolerates_late_wakeup_inside_grace():
    s = ScheduleSpec.parse("at 16:04 weekdays")
    sched = Scheduler({"close": s}, trading_day=lambda d: True)
    d = date(2026, 9, 3)
    assert sched.due(et(d, 16, 4).replace(second=40)) == [Fire("close", et(d, 16, 4))]


def test_lock_is_per_job_and_stable():
    sched = Scheduler({}, trading_day=lambda d: True)
    assert sched.lock("a") is sched.lock("a")
    assert sched.lock("a") is not sched.lock("b")
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_scheduler.py`
Expected: FAIL — `ModuleNotFoundError: tc.scheduler`.

- [ ] **Step 7: Implement scheduler.py**

```python
"""Explicit ET schedule, expanded at load (spec §3). No cron syntax.

A fire is due in the minute it names. A fire the engine slept through is
recorded as `missed` by mark_missed and never run late: a 09:47 tick run at
10:20 would write a row claiming a sweep that did not happen.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from tc.clock import ET

Days = Literal["weekdays", "daily", "sat", "sun"]
_EVERY = re.compile(r"^every (\d+)m (\d\d:\d\d)-(\d\d:\d\d) (weekdays|daily|sat|sun)$")
_AT = re.compile(r"^at (\d\d:\d\d) (weekdays|daily|sat|sun)$")
GRACE = timedelta(seconds=59)


@dataclass(frozen=True)
class Fire:
    job: str
    at: datetime


@dataclass(frozen=True)
class ScheduleSpec:
    days: Days
    start: time
    end: time
    every_min: int  # 0 => single fire at `start`

    @classmethod
    def parse(cls, text: str) -> ScheduleSpec:
        t = text.strip()
        if m := _EVERY.match(t):
            n, s, e, d = m.groups()
            start, end = time.fromisoformat(s), time.fromisoformat(e)
            if int(n) <= 0 or end < start:
                raise ValueError(f"bad schedule {text!r}")
            return cls(d, start, end, int(n))  # type: ignore[arg-type]
        if m := _AT.match(t):
            s, d = m.groups()
            at = time.fromisoformat(s)
            return cls(d, at, at, 0)  # type: ignore[arg-type]
        raise ValueError(f"bad schedule {text!r}")

    def day_matches(self, d: date, trading_day: Callable[[date], bool]) -> bool:
        if self.days == "daily":
            return True
        if self.days == "sat":
            return d.weekday() == 5
        if self.days == "sun":
            return d.weekday() == 6
        return d.weekday() < 5 and trading_day(d)

    def fires_on(self, d: date, trading_day: Callable[[date], bool] | None = None) -> list[datetime]:
        if not self.day_matches(d, trading_day or (lambda x: True)):
            return []
        first = datetime.combine(d, self.start, tzinfo=ET)
        if self.every_min == 0:
            return [first]
        last = datetime.combine(d, self.end, tzinfo=ET)
        out, t = [], first
        while t <= last:
            out.append(t)
            t += timedelta(minutes=self.every_min)
        return out


class Scheduler:
    def __init__(self, specs: dict[str, ScheduleSpec], trading_day: Callable[[date], bool]) -> None:
        self.specs = specs
        self._trading_day = trading_day
        self._fired: set[Fire] = set()
        self._missed: set[Fire] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def _fires(self, d: date) -> list[Fire]:
        return [Fire(j, at) for j, s in self.specs.items() for at in s.fires_on(d, self._trading_day)]

    def due(self, now: datetime) -> list[Fire]:
        now = now.astimezone(ET)
        out = []
        for f in self._fires(now.date()):
            if f.at <= now <= f.at + GRACE and f not in self._fired and f not in self._missed:
                self._fired.add(f)
                out.append(f)
        return out

    def mark_missed(self, now: datetime) -> list[Fire]:
        now = now.astimezone(ET)
        out = []
        for f in self._fires(now.date()):
            if f.at + GRACE < now and f not in self._fired and f not in self._missed:
                self._missed.add(f)
                out.append(f)
        return out

    def lock(self, job: str) -> asyncio.Lock:
        return self._locks.setdefault(job, asyncio.Lock())
```

- [ ] **Step 8: Run the gate**

Run: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`
Expected: all green. If mypy rejects the `# type: ignore[arg-type]` on the Literal, replace with `days: Days = d  # noqa` after `assert d in ("weekdays","daily","sat","sun")` and a `cast(Days, d)`.

- [ ] **Step 9: Commit**

```bash
git add engine/tc/config.py engine/tc/scheduler.py config.yml tests/engine/unit/test_config.py tests/engine/unit/test_scheduler.py
git commit -m "engine: explicit ET schedule as data; a slept-through fire is missed, never late"
```

---

### Task 2: `Notifier` (Discord webhook) and `Pinger` (healthchecks)

**Files:**
- Create: `engine/tc/notify.py`
- Test: `tests/engine/unit/test_notify.py`

**Interfaces:**
- Produces:
  - `class Notifier(webhook: str | None, client: httpx.AsyncClient)`; `async post(text: str) -> bool` (True when a 2xx came back; False and never raises otherwise; no-op False when webhook is None). Messages over 1900 chars are truncated with `…`.
  - `class Pinger(base_url: str | None, client: httpx.AsyncClient)`; `async start(job)`, `async ok(job, verdict: str)`, `async fail(job, verdict: str)`; every method swallows errors and returns `bool`. URLs: `{base}/{job}/start`, `{base}/{job}`, `{base}/{job}/fail`; body is the verdict word only (spec §7: never account data).

- [ ] **Step 1: Failing tests**

```python
import httpx
import pytest
from tc.notify import Notifier, Pinger


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_notifier_posts_content_and_truncates():
    seen = {}
    def h(req: httpx.Request) -> httpx.Response:
        seen["json"] = req.read()
        return httpx.Response(204)
    n = Notifier("https://discord.test/hook", _client(h))
    assert await n.post("x" * 2500) is True
    body = seen["json"].decode()
    assert '"content"' in body and "…" in body and len(body) < 2100


async def test_notifier_never_raises():
    def h(req):
        raise httpx.ConnectError("down")
    assert await Notifier("https://discord.test/hook", _client(h)).post("hi") is False


async def test_notifier_disabled_without_webhook():
    assert await Notifier(None, _client(lambda r: httpx.Response(204))).post("hi") is False


async def test_pinger_paths_and_bodies():
    calls = []
    def h(req):
        calls.append((str(req.url), req.read().decode()))
        return httpx.Response(200)
    p = Pinger("https://hc.test/abc", _client(h))
    await p.start("tick"); await p.ok("tick", "done"); await p.fail("tick", "timeout")
    assert calls == [
        ("https://hc.test/abc/tick/start", ""),
        ("https://hc.test/abc/tick", "done"),
        ("https://hc.test/abc/tick/fail", "timeout"),
    ]


async def test_pinger_disabled_without_base():
    assert await Pinger(None, _client(lambda r: httpx.Response(200))).ok("tick", "done") is False
```

- [ ] **Step 2: Run, expect ModuleNotFoundError.**

- [ ] **Step 3: Implement**

```python
"""Outbound-only side channels. Neither may raise into a loop: a missed
notification must never take down the sweep it was reporting on."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)
DISCORD_MAX = 1900


class Notifier:
    def __init__(self, webhook: str | None, client: httpx.AsyncClient) -> None:
        self._url = webhook
        self._c = client

    async def post(self, text: str) -> bool:
        if not self._url:
            return False
        if len(text) > DISCORD_MAX:
            text = text[: DISCORD_MAX - 1] + "…"
        try:
            r = await self._c.post(self._url, json={"content": text}, timeout=10)
            return 200 <= r.status_code < 300
        except httpx.HTTPError as e:
            log.warning("discord post failed: %s", e)
            return False


class Pinger:
    def __init__(self, base_url: str | None, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/") if base_url else None
        self._c = client

    async def _hit(self, path: str, body: str) -> bool:
        if not self._base:
            return False
        try:
            r = await self._c.post(f"{self._base}/{path}", content=body, timeout=10)
            return 200 <= r.status_code < 300
        except httpx.HTTPError as e:
            log.warning("healthcheck ping failed: %s", e)
            return False

    async def start(self, job: str) -> bool:
        return await self._hit(f"{job}/start", "")

    async def ok(self, job: str, verdict: str) -> bool:
        return await self._hit(job, verdict)

    async def fail(self, job: str, verdict: str) -> bool:
        return await self._hit(f"{job}/fail", verdict)
```

- [ ] **Step 4: Gate, then commit** `engine/tc/notify.py tests/engine/unit/test_notify.py` — "engine: Discord and healthchecks are outbound-only and never raise into a loop".

---

### Task 3: `BookView` — reconciliation (`CLAUDE.md §4.5`)

**Files:**
- Create: `engine/tc/loops/__init__.py`, `engine/tc/loops/reconcile.py`
- Modify: `engine/tc/store/db.py` (add `latest_positions`)
- Test: `tests/engine/unit/test_reconcile.py`; fixture `tests/engine/fixtures/broker/orders-partial.json`

**Interfaces:**
- Consumes: `Broker` (Phase 0a), `Store.record_account`, `Store.record_orders`, `AccountSnapshot`, `OrderRow`.
- Produces:
  - `BookView` (pydantic): `account: AccountSnapshot`, `orders: list[OrderRow]`, `resting_stops: dict[str, OrderRow]` (symbol → the resting SELL stop), `naked: list[str]`, `partial: list[tuple[str, int, int]]` (symbol, position qty, stop qty), `orphaned_stops: list[OrderRow]`, `open_entries: list[OrderRow]` (resting BUY limits), `restricted: bool`, `read_at: datetime`.
  - `async reconcile(broker, store, account_hash, now, orders_from: date) -> BookView` — reads account + orders, records both snapshots, returns the view.
  - `Store.latest_positions() -> dict[str, int]` (symbol → qty from the newest account snapshot; `{}` if none).

Rules ported from tick.md §B3/§C: `orders_from` must cover every order that could still rest (config: `engine.orders_from: date`, default `2026-08-14`); `to` is tomorrow. A stop "matches" a position when it is resting, its single leg is `SELL` on that symbol, and quantities are equal; unequal → `partial`; resting SELL stop with no position → `orphaned`.

- [ ] **Step 1: Fixture** `orders-partial.json` — copy `orders.json`, change the AMH stop leg quantity and order quantity to 20 (position is 29 in `account.json`), keep synthetic ids.

- [ ] **Step 2: Failing tests**

```python
from datetime import UTC, date, datetime
from pathlib import Path
import pytest
from tc.broker.fake import FakeBroker
from tc.loops.reconcile import reconcile
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "e.db"); await s.open(); yield s; await s.close()


async def test_view_matches_stops_to_positions(store):
    v = await reconcile(FakeBroker(FIX, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert set(v.resting_stops) == {p.symbol for p in v.account.positions}
    assert v.naked == [] and v.partial == [] and v.orphaned_stops == []
    assert v.restricted is False
    assert await store.latest_positions() == {p.symbol: p.quantity for p in v.account.positions}


async def test_partial_and_naked(store, tmp_path):
    import shutil
    d = tmp_path / "fx"; shutil.copytree(FIX, d)
    (d / "orders.json").write_text((FIX / "orders-partial.json").read_text())
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert ("AMH", 29, 20) in v.partial
    (d / "orders.json").write_text("[]")
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert sorted(v.naked) == sorted(p.symbol for p in v.account.positions)


async def test_orphaned_stop_without_position(store, tmp_path):
    import json, shutil
    d = tmp_path / "fx"; shutil.copytree(FIX, d)
    acct = json.loads((d / "account.json").read_text())
    acct["securitiesAccount"]["positions"] = []
    (d / "account.json").write_text(json.dumps(acct))
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert len(v.orphaned_stops) == len(v.resting_stops) and v.naked == []
```

- [ ] **Step 3: Run, expect ModuleNotFoundError.**

- [ ] **Step 4: Implement `reconcile.py`**

```python
"""CLAUDE.md §4.5: never begin from an assumed state. One account read, one
orders read, both recorded, one typed view for every loop that follows."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker
from tc.broker.models import AccountSnapshot, OrderRow
from tc.store.db import Store


class BookView(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    account: AccountSnapshot
    orders: list[OrderRow]
    resting_stops: dict[str, OrderRow]
    naked: list[str]
    partial: list[tuple[str, int, int]]
    orphaned_stops: list[OrderRow]
    open_entries: list[OrderRow]
    restricted: bool
    read_at: datetime


def _is_sell_stop(o: OrderRow) -> bool:
    return o.is_resting_stop and len(o.legs) == 1 and o.legs[0].instruction.startswith("SELL")


def build_view(account: AccountSnapshot, orders: list[OrderRow], read_at: datetime) -> BookView:
    held = {p.symbol: p.quantity for p in account.positions if p.quantity > 0}
    stops = {o.symbol: o for o in orders if _is_sell_stop(o)}
    naked = [s for s in held if s not in stops]
    partial = [(s, q, stops[s].quantity) for s, q in held.items() if s in stops and stops[s].quantity != q]
    orphaned = [o for s, o in stops.items() if s not in held]
    entries = [o for o in orders if o.status in {"WORKING", "QUEUED", "ACCEPTED"}
               and o.order_type == "LIMIT" and o.legs and o.legs[0].instruction.startswith("BUY")]
    return BookView(
        account=account, orders=orders, resting_stops=stops, naked=sorted(naked),
        partial=partial, orphaned_stops=orphaned, open_entries=entries,
        restricted=account.is_closing_only_restricted or account.cash_call != 0, read_at=read_at,
    )


async def reconcile(
    broker: Broker, store: Store, account_hash: str, now: datetime, orders_from: date
) -> BookView:
    account = await broker.account(account_hash)
    frm = datetime.combine(orders_from, datetime.min.time(), tzinfo=now.tzinfo)
    orders = await broker.orders(account_hash, frm, now + timedelta(days=1))
    await store.record_account(account)
    await store.record_orders(account_hash, orders, now)
    return build_view(account, orders, now)
```

Add to `Store`:

```python
    async def latest_positions(self) -> dict[str, int]:
        rows = await self.fetchall(
            "SELECT symbol, quantity FROM position_snapshots WHERE snapshot_id ="
            " (SELECT id FROM account_snapshots ORDER BY id DESC LIMIT 1)"
        )
        return {r["symbol"]: int(r["quantity"]) for r in rows}
```

Add `orders_from: date = date(2026, 8, 14)` to `EngineConfig` with the comment from tick.md §B3 (coverage, not the date).

- [ ] **Step 5: Gate; commit** — "engine: reconciliation is one typed view, and a stop either matches its position or it does not".

---

### Task 4: Session close and the high-water mark (`CLAUDE.md §7.2`, `§3.6`)

**Files:**
- Create: `engine/tc/loops/session.py`
- Modify: `engine/tc/store/db.py` (`ticks_for`, `session_status_for`), `engine/tc/cli.py` (`seed-hwm`)
- Test: `tests/engine/unit/test_session.py`

**Interfaces:**
- Consumes: `arith.ratchet_hwm`, `arith.halt_threshold`, `arith.drawdown_pct`, `arith.is_halted`, `arith.legacy_hwm_to_account_basis`, `Store.write_session_status`, `Store.latest_session_status`, `TickRow`.
- Produces:
  - `async seed_hwm(store, recorded: Decimal, recorded_on: date, reserve: Decimal) -> SessionStatusRow` — writes the first `session_status` row from a legacy status-file mark, converting basis via `legacy_hwm_to_account_basis`. Refuses (raises `ValueError`) if any row already exists.
  - `async close_session(store, rules, d: date, close_value: Decimal) -> SessionStatusRow` — ratchets from the latest row (`ValueError` if none: "seed first"), records `intraday_high` = max account_value in today's ticks when it exceeds the new mark, else None; writes the row.
  - `Store.ticks_for(at_et_prefix: str) -> list[TickRow]`, `Store.session_status_for(d: date) -> SessionStatusRow | None`.
  - CLI `tc seed-hwm --value 3800.00 --recorded-on 2026-09-03`.

- [ ] **Step 1: Failing tests**

```python
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import pytest
from tc.loops.session import close_session, seed_hwm
from tc.rules.model import Rules
from tc.store.db import Store, TickRow

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "e.db"); await s.open(); yield s; await s.close()


async def test_seed_converts_pre_amendment_basis(store):
    r = await seed_hwm(store, D("2900.00"), date(2026, 8, 25), D("900.00"))
    assert r.hwm == D("3800.00") and r.prior_hwm == D("3800.00") and r.ratcheted is False


async def test_seed_refuses_twice(store):
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    with pytest.raises(ValueError):
        await seed_hwm(store, D("1.00"), date(2026, 9, 3), D("900.00"))


async def test_close_ratchets_up_only(store):
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    r = await close_session(store, RULES, date(2026, 9, 4), D("3728.71"))
    assert r.hwm == D("3800.00") and r.ratcheted is False and r.level == "OK"
    assert r.halt == D("3040.00") and r.drawdown_pct == D("-1.88")
    r2 = await close_session(store, RULES, date(2026, 9, 8), D("3900.00"))
    assert r2.hwm == D("3900.00") and r2.ratcheted is True and r2.prior_hwm == D("3800.00")


async def test_close_records_intraday_high_above_mark_without_ratcheting(store):
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    row = TickRow(at_et="2026-09-04 11:32", state="RTH", account_value=D("3850.00"), comp_capital=D("2950.00"),
                  hwm=D("3800.00"), drawdown_pct=D("1.32"), level="OK", positions=3, stops=3, orders=0,
                  settled=D("1"), unsettled=D("0"), reserve=D("1"), flags="-", note="-")
    await store.append_tick(row)
    r = await close_session(store, RULES, date(2026, 9, 4), D("3728.71"))
    assert r.hwm == D("3800.00") and r.intraday_high == D("3850.00")


async def test_close_without_seed_refuses(store):
    with pytest.raises(ValueError):
        await close_session(store, RULES, date(2026, 9, 4), D("1"))


async def test_halt_level(store):
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    r = await close_session(store, RULES, date(2026, 9, 4), D("3040.00"))
    assert r.level == "HALT"
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement**

```python
"""Session close (CLAUDE.md §7.2) and the one irreversible number: the
high-water mark ratchets only here, only up, only from a CLOSE
(session-close.md §3). An intraday high is recorded, never adopted."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tc.rules import arith
from tc.rules.model import Rules
from tc.store.db import SessionStatusRow, Store


async def seed_hwm(store: Store, recorded: Decimal, recorded_on: date, reserve: Decimal) -> SessionStatusRow:
    if await store.latest_session_status() is not None:
        raise ValueError("session_status already seeded; refusing to overwrite the mark")
    hwm = arith.legacy_hwm_to_account_basis(recorded, recorded_on, reserve)
    row = SessionStatusRow(date=recorded_on, close_value=hwm, hwm=hwm, halt=Decimal("0"),
                           drawdown_pct=Decimal("0"), level="OK", prior_hwm=hwm,
                           ratcheted=False, intraday_high=None)
    await store.write_session_status(row)
    return row


async def close_session(store: Store, rules: Rules, d: date, close_value: Decimal) -> SessionStatusRow:
    prior = await store.latest_session_status()
    if prior is None:
        raise ValueError("no high-water mark on record: run `tc seed-hwm` first")
    hwm = arith.ratchet_hwm(prior.hwm, close_value)
    ticks = await store.ticks_for(d.isoformat())
    highs = [t.account_value for t in ticks if t.state == "RTH"]
    intraday = max(highs) if highs and max(highs) > hwm else None
    row = SessionStatusRow(
        date=d, close_value=close_value, hwm=hwm, halt=arith.halt_threshold(hwm, rules),
        drawdown_pct=arith.drawdown_pct(close_value, hwm),
        level="HALT" if arith.is_halted(close_value, hwm, rules) else "OK",
        prior_hwm=prior.hwm, ratcheted=hwm > prior.hwm, intraday_high=intraday,
    )
    await store.write_session_status(row)
    return row
```

Store additions:

```python
    async def ticks_for(self, at_et_prefix: str) -> list[TickRow]:
        rows = await self.fetchall("SELECT * FROM ticks WHERE at_et LIKE ? ORDER BY id", (at_et_prefix + "%",))
        return [TickRow(**{k: r[k] for k in TickRow.model_fields}) for r in rows]

    async def session_status_for(self, d: date) -> SessionStatusRow | None:
        row = await self.fetchone("SELECT * FROM session_status WHERE date=?", (d.isoformat(),))
        return None if row is None else self._status_row(row)
```

Refactor `latest_session_status` to share `_status_row(row)`. `TickRow(**…)` needs Decimal coercion from TEXT — pydantic coerces strings to Decimal; `positions` etc. are ints already.

CLI: `seed-hwm` subparser with `--value` (Decimal) and `--recorded-on` (date); opens `Store(s.engine.data_dir / "engine.db")`, calls `seed_hwm(..., s.engine.reserve_usd)`, prints `hwm=<value> basis=account`. Test in `test_cli.py`: seeding a pre-amendment date prints the converted mark; seeding twice exits 4.

- [ ] **Step 4: Gate; commit** — "engine: the high-water mark ratchets only at close, only up, and is seeded exactly once".

---

### Task 5: Clocks (`CLAUDE.md §3.3`, `§3.5`)

**Files:**
- Create: `engine/tc/loops/clocks.py`
- Modify: `engine/tc/store/db.py` (`first_seen`)
- Test: `tests/engine/unit/test_clocks.py`

**Interfaces:**
- Produces:
  - `parse_osi(symbol: str) -> OptionRef | None` — `OptionRef(underlying: str, expiry: date, kind: Literal["C","P"], strike: Decimal)`; Schwab positions carry OSI like `"AAPL  261016C00230000"` (6-char padded root, YYMMDD, C/P, strike×1000).
  - `dte(expiry: date, today: date) -> int` (calendar days).
  - `ClockAlert(symbol: str, kind: Literal["option_close", "option_warn", "leveraged_close", "leveraged_warn"], detail: str)`.
  - `async run_clocks(store, rules, positions: list[Position], today: date, trading_days_between: Callable[[date, date], int]) -> list[ClockAlert]`:
    - option position with `dte <= rules.option_close_at_dte` → `option_close`; `dte <= option_close_at_dte + 2` → `option_warn` (tick.md watch 7 warns at 7 DTE with the close at 5: warn = close + 2).
    - leveraged ETF held ≥ `leveraged_max_hold_sessions` sessions → `leveraged_close`; ≥ `leveraged_max_hold_sessions - 2` → `leveraged_warn` (watch 7: "≥ day 3 of 5").
  - `Store.first_seen(symbol) -> datetime | None` — `read_at` of the earliest account snapshot containing the symbol.
  - Leveraged detection: `Position.asset_type == "COLLECTIVE_INVESTMENT"` **and** symbol in `rules.get_list("strategy", "leveraged_etf_symbols")`? No such key exists — instead take a `leveraged: set[str]` parameter supplied by the caller (Phase 0c's universe table knows leverage; until then `config.yml` `engine.leveraged_symbols: []`). Document that in the docstring.

- [ ] **Step 1: Failing tests** (`test_clocks.py`): parse `"AMH   261016P00030000"` → underlying AMH, 2026-10-16, P, 30.000; `parse_osi("AMH") is None`; `dte(date(2026,10,16), date(2026,10,11)) == 5`; with `RULES` from rules.yml: an option position at close_at_dte → `option_close`; at close_at_dte+2 → `option_warn`; at close_at_dte+3 → no alert; leveraged symbol first seen 5 sessions ago → `leveraged_close`; 3 → `leveraged_warn`; a non-leveraged equity never alerts. Use a fake `trading_days_between` that counts weekdays.

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement** — OSI regex `^(?P<root>[A-Z.]{1,6})\s*(?P<yy>\d\d)(?P<mm>\d\d)(?P<dd>\d\d)(?P<k>[CP])(?P<strike>\d{8})$` on the symbol with spaces collapsed; strike = `Decimal(strike) / 1000`. `first_seen` SQL: `SELECT a.read_at FROM position_snapshots p JOIN account_snapshots a ON a.id=p.snapshot_id WHERE p.symbol=? ORDER BY a.id LIMIT 1`.

- [ ] **Step 4: Gate; commit** — "engine: the §3.3 and §3.5 clocks are arithmetic on the ledger, not a model reading a calendar".

---

### Task 6: The tick — phase, BLIND/STALE, watches 1–7, ledger row, trips

**Files:**
- Create: `engine/tc/loops/tick.py`
- Test: `tests/engine/unit/test_tick.py`; fixtures `quotes-stale.json`, `hours-2026-09-04.json`

**Interfaces:**
- Consumes: `reconcile`, `run_clocks`, `phase_for`, `fallback_window`, `arith`, `Store.append_tick`, `Store.latest_positions`, `Store.latest_session_status`, `Notifier`.
- Produces:
  - `Trip(watch: int, name: str, detail: str)`; `TickResult(row: TickRow, trips: list[Trip], view: BookView | None)`.
  - `async run_tick(*, broker, store, rules, reserve: Decimal, account_hash: str | None, now: datetime, window: MarketWindow | None, leveraged: set[str], trading_days_between) -> TickResult`.
  - Behaviour, ported from tick.md §B/§C:
    - `BrokerUnauthorized` on the account read → state `BLIND`, row written with zeros and note `"BLIND: token dead or absent"`, trips `[Trip(0,"blind",…)]`, `view=None`. Never raises.
    - Phase from `phase_for(now, window)`; `window=None` → `fallback_window(now.date())` and flag `F` (fallback calendar).
    - Quotes for held symbols only in RTH; a quote older than 3 minutes is re-fetched once, still old → state `STALE` and watches 3 is still evaluated (uses account value, not quotes) but the note says `STALE`.
    - `account_value = liquidation_value`; `comp_capital = account_value - reserve` (display column only); `hwm` from `latest_session_status()` (ValueError "seed first" if none → note and trip 0); `halt`, `drawdown_pct`, `level` from `arith`.
    - Watches in priority order — 1 restriction (`view.restricted`), 2 reserve invariant (`account.reserve_cash < reserve`), 3 drawdown (`is_halted`), 4 naked (`view.naked`), 5 partial (`view.partial`), 6 stop fill (symbol in `latest_positions()` before this read but absent now, or a stop consumed: prior positions minus current), 7 clocks (`run_clocks` alerts). Watch 8 (correlation) is a job, deferred to Plan 0c; note `flags` carries `C` when `today` is the first trading day of the week to remind the diff.
    - `flags`: letters, `-` when none: `R` restricted, `V` reserve, `H` halt, `N` naked, `P` partial, `X` stop fill, `K` clock, `S` stale, `F` fallback calendar, `B` blind.
    - The row is appended exactly once per call, before returning, even on trips.

- [ ] **Step 1: Fixtures** — `hours-2026-09-04.json` (copy of `hours-2026-09-02.json` with the date strings changed); `quotes-stale.json` (copy of `quotes.json` with every `quoteTime` set to `1787000000000`).

- [ ] **Step 2: Failing tests** covering: clean RTH tick → flags `-`, `positions==stops==3`, `level OK`, row stored; BLIND on `unauthorized` marker file; naked → flag `N` and a `Trip(4,…)`; partial → `P`; restricted (edit `isClosingOnlyRestricted` in a copied account.json) → `R` first in flags and trips ordered `[1, …]`; reserve breach (edit balances) → `V`; halt (seed hwm 10000) → `H` and level `HALT`; stop fill (prior snapshot had `CSX`, current does not) → `X`; PRE phase (now 09:00 ET) → state `PRE`, no quotes read (FakeBroker counts `quotes` calls via a subclass); stale quotes → state `STALE`, flag `S`; no session_status → trip 0 and note contains `seed`.

- [ ] **Step 3: Run, expect failure.**

- [ ] **Step 4: Implement `tick.py`** (≈150 lines). Skeleton:

```python
async def run_tick(*, broker, store, rules, reserve, account_hash, now, window, leveraged, trading_days_between) -> TickResult:
    now_et = now.astimezone(ET)
    at_et = now_et.strftime("%Y-%m-%d %H:%M")
    win = window or fallback_window(now_et.date())
    flags: list[str] = [] if window else ["F"]
    trips: list[Trip] = []
    try:
        h = account_hash or (await broker.account_hashes())[0]
        prior = await store.latest_positions()
        view = await reconcile(broker, store, h, now, ORDERS_FROM)
    except BrokerUnauthorized:
        row = _blind_row(at_et, reserve)
        await store.append_tick(row)
        return TickResult(row=row, trips=[Trip(0, "blind", "token dead or absent")], view=None)
    ...
```

Write `_blind_row`, `_quotes_fresh(broker, symbols, now)` (two attempts, 3-minute rule), `_evaluate(view, prior, …) -> tuple[list[Trip], list[str]]`, and the row assembly. Keep watch evaluation in a pure function `evaluate_watches(view, prior_positions, hwm, rules, reserve, clock_alerts) -> list[Trip]` so it is unit-testable without I/O.

- [ ] **Step 5: Gate; commit** — "engine: the tick is code — seven watches in priority order, one row per sweep, BLIND is a state not a crash".

---

### Task 7: Token loop and blind mode (spec §8)

**Files:**
- Create: `engine/tc/loops/token.py`
- Test: `tests/engine/unit/test_token_loop.py`

**Interfaces:**
- Consumes: `TokenStore.state/age_days/days_until_dead/begin_auth`, `Store.record_token_event`, `Store.open_alerts/open_alert`, `Notifier`.
- Produces: `TokenReport(state, age_days, days_until_dead, action: str, auth_url: str | None)` and `async token_check(token: TokenStore, store: Store, notifier: Notifier, now: datetime) -> TokenReport`:
  - `fresh` → record event `checked`, no post.
  - `reauth_due` → `begin_auth()` once per calendar day (dedupe on a `token_events` row of kind `auth_url_posted` dated today), post `"🔑 Schwab re-auth due — {days_until_dead:.1f} days until dead. Open on your phone (Tailscale on): {url}"`, record `auth_url_posted`.
  - `dead`/`absent` → post `"⛔ Schwab token {state}: the engine is BLIND … {url}"` once per day, open an alert of kind `token_dead` if none open.
  - Wording rule (spec §7): every message carries `days_until_dead` and the action; the word "healthy" never appears. Test asserts `"healthy" not in text.lower()`.

- [ ] **Step 1: Failing tests** using a `TokenStore` with a fake clock and a written token file at ages 1, 5.5 and 8 days; a `Notifier` over `MockTransport` capturing posts; assert dedupe within a day and a new post the next day; assert `begin_auth` is monkeypatched to return `"https://auth.test/x"` (no network).

- [ ] **Step 2: Run, expect failure. Step 3: Implement. Step 4: Gate; commit** — "engine: a dead token is a state the engine reports daily with the URL that fixes it, never a crash loop".

---

### Task 8: Expectations (spec §7 layer 3)

**Files:**
- Create: `engine/tc/loops/expectations.py`
- Modify: `config.yml` (`expectations:` list), `engine/tc/config.py` (`FileConfig.expectations: list[Expectation]`)
- Test: `tests/engine/unit/test_expectations.py`

**Interfaces:**
- `Expectation(name: str, check: Literal["ticks_per_session_min", "session_status_present", "job_verdict_not", "token_days_until_dead_min"], arg: int | str, window_sessions: int = 1)`.
- `async run_expectations(store, token, specs, today: date) -> list[ExpectationResult(name, ok, detail)]`; every result is written to `expectations_log`; a weekly digest string `digest(results) -> str` always non-empty ("N checks, M breached").
- Checks (SQL over the store, no Claude):
  - `ticks_per_session_min` N: yesterday's session (latest date with ticks) has ≥ N `RTH` rows.
  - `session_status_present`: a `session_status` row exists for the latest tick date.
  - `job_verdict_not` `content_failed`: no `job_runs` row in the last `window_sessions` days with that verdict.
  - `token_days_until_dead_min` N: `token.days_until_dead() >= N`.

Default `config.yml` rows: `ticks_per_session_min: 20`, `session_status_present`, `job_verdict_not: content_failed` (window 5), `token_days_until_dead_min: 2`.

- [ ] Steps: failing tests (seed ticks/session rows, assert ok/breach and the log rows), implement, gate, commit — "engine: expectations are queries with names, and the digest posts even when everything passed".

---

### Task 9: HTTP app — `/health`, `/oauth/callback`, `/api`

**Files:**
- Create: `engine/tc/http/__init__.py`, `engine/tc/http/app.py`
- Modify: `engine/pyproject.toml` (add `starlette>=0.40`, `uvicorn>=0.30`)
- Test: `tests/engine/unit/test_http.py`

**Interfaces:**
- `build_app(state: EngineState) -> Starlette` where `EngineState` (dataclass in `tc/http/app.py`, filled by `main.py`) has: `token: TokenStore`, `store: Store`, `started_at: datetime`, `last_broker_read_ok_at: datetime | None`, `last_tick: TickResult | None`, `blind: bool`, `shadow: bool`, `version: str`, `now: Callable[[], datetime]`.
- `GET /health` → 200 JSON:

```json
{"ok": true, "version": "0.2.0", "shadow": true, "blind": false,
 "token_state": "fresh", "token_days_until_dead": 4.1,
 "last_broker_read_ok_at": "...", "last_broker_read_age_s": 41,
 "positions_without_stop": 0, "pending_approval_age_s": null,
 "runner_ok": null, "db_ok": true, "in_flight_proposal": false,
 "action": "none"}
```

  `ok` is false when `blind` or `db_ok` false. `action` mirrors the token action wording. Fields the host probe reads (spec §7 layer 2) are exactly these names.
- `GET /oauth/callback?code=…&state=…` → calls `token.complete_auth(str(request.url))` in a thread (`starlette.concurrency.run_in_threadpool`; schwab-py's exchange is sync); 200 `"Token installed — you can close this tab."` on success; 400 with the error class name on `NoAuthInProgress`/`ValueError`/authlib errors; records `token_events` `installed`/`install_failed`; sets `state.blind=False` on success.
- `GET /api/status` → latest `session_status` row + `last_tick.row`; `GET /api/ticks?date=YYYY-MM-DD` → rows. JSON with Decimals as strings.
- Binding to `0.0.0.0` is refused at startup (`main.py`); the app itself is host-agnostic.

- [ ] **Step 1: Failing tests** with `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`: health shape and `ok=false` when blind; callback success path with `complete_auth` monkeypatched; callback 400 on `NoAuthInProgress`; `/api/ticks` returns the seeded row with Decimal strings.
- [ ] **Steps 2–4:** implement, gate, commit — "engine: /health says days_until_dead and the action, and the phone lands on /oauth/callback".

---

### Task 10: `Engine` wiring, `tc run`, graceful shutdown

**Files:**
- Create: `engine/tc/main.py`
- Modify: `engine/tc/cli.py` (`run`), `engine/tc/__init__.py` (`__version__ = "0.2.0"`)
- Test: `tests/engine/unit/test_main.py`

**Interfaces:**
- `class Engine(settings: Settings, *, broker: Broker, store: Store, token: TokenStore, notifier: Notifier, pinger: Pinger, clock: Callable[[], datetime])`; `async start()` (opens store, seeds the scheduler from `settings.schedule`, resolves the account hash, caches today's `MarketWindow` via `broker.market_window` with `fallback_window` on `BrokerError`), `async run_forever()` (1 s loop: `scheduler.due(now)` → dispatch each fire under its lock as a task; `mark_missed` → `record_job_run(job, at, at, "missed", {})`), `async stop()`.
- Job dispatch `_dispatch(fire)`: `pinger.start(job)`; run the loop function; on success `record_job_run(... "done"|"noop", detail)`, `pinger.ok`; on exception `record_job_run("failed", {"error": repr})`, `pinger.fail`, `notifier.post` (shadow webhook when `shadow.enabled`).
- Job table: `tick` → `run_tick` + notify on trips (`"🚨 TICK TRIP …"` + one line per trip); `session_close` → `close_session(close_value=view.account.liquidation_value)` after a fresh reconcile, notify `"📒 close …"` (always; one per day is signal); `token_check` → `token_check`; `expectations` → `run_expectations` + digest post; `backup` → `VACUUM INTO data_dir/backup/engine-DATE.db`, keep 14.
- `tc run [--once JOB]`: builds the real broker (`SchwabBroker`) unless `TC_MODE=paper` (then `FakeBroker` on `TC_FIXTURES`), `httpx.AsyncClient`, `Notifier(shadow webhook if shadow else webhook)`, uvicorn on `engine.http_bind` (refuse `0.0.0.0`), and `Engine.run_forever()`; SIGTERM/SIGINT → `stop()`. `--once tick` runs one job and exits (operator smoke).
- Blind mode: `BrokerUnauthorized` from `start()`'s hash resolution sets `state.blind=True`; loops still fire; `run_tick` writes BLIND rows; `token_check` keeps posting.

- [ ] **Step 1: Failing tests**: with `FakeBroker`, a frozen clock stepping through 09:32 and 09:47, `Engine.run_forever()` driven by `asyncio.wait_for(...)` for a few loop iterations records two `done` tick runs and two ticks rows; a clock jump to 10:20 records `missed` rows; `unauthorized` fixture → engine starts, `blind` true, health `ok` false; `--once tick` exit code 0 and one row.

- [ ] **Steps 2–4:** implement, gate, commit — "engine: one process, one loop, one lock per job; down is recorded as missed, never as a late sweep".

---

### Task 11: Shadow diff against the old ledgers (Phase 0 exit criterion)

**Files:**
- Create: `engine/tc/shadow.py`; fixtures `tests/engine/fixtures/legacy/status-2026-09-03.md`, `ticks-2026-09-03.tsv` (synthetic, `HASH_REDACTED`, synthetic order ids)
- Modify: `engine/tc/cli.py` (`shadow-diff`)
- Test: `tests/engine/unit/test_shadow.py`

**Interfaces:**
- `parse_legacy_status(path) -> LegacyStatus(hwm: Decimal, account_value: Decimal, positions: dict[str, int], stops: dict[str, tuple[Decimal, Decimal]])` — the "State recorded — current" block only (port of `latest-status.sh` awk: from that exact heading to the next `#` line; first `High-water mark:` inside; `$1,234.56` money regex).
- `parse_legacy_ticks(path) -> list[LegacyTick(time_et, state, comp_capital, hwm, dd_pct, level, positions, stops)]` — 14-column TSV.
- `async diff_day(store, d: date, status_path, ticks_path, reserve) -> ShadowDiff(hwm_match: bool, value_diffs: list[(time, engine, legacy)], stop_map_match: bool, missing_engine_ticks: list[str], missing_legacy_ticks: list[str], ok: bool)` — matches ticks by `HH:MM`; value equal when engine `account_value` == legacy `comp_capital + reserve` within `0.01`; HWM identical.
- CLI `tc shadow-diff DATE --store-dir /path/to/trade-challenge-store` prints one line per mismatch and `SHADOW OK`/`SHADOW DIFF` with exit 0/1.

- [ ] Steps: failing tests (parse the synthetic files; a matching day → ok; a $0.02 value drift → listed; a stop map difference → `stop_map_match False`), implement, gate, commit — "engine: the shadow diff is what ends Phase 0, so it is code with tests, not a glance at two files".

---

### Task 12: Docker service and the host probe

**Files:**
- Create: `docker/Dockerfile.engine`, `host/healthprobe.py`, `host/tc-healthprobe.service`, `host/tc-healthprobe.timer`, `host/README.md`
- Modify: `docker/docker-compose.yml` (add `engine`)
- Test: `tests/engine/unit/test_healthprobe.py` (imports `host/healthprobe.py` by path via `importlib`)

**Dockerfile.engine:**

```dockerfile
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/New_York
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 -s /usr/sbin/nologin engine
WORKDIR /srv/engine
COPY engine/pyproject.toml ./
COPY engine/tc ./tc
RUN pip install --no-cache-dir . && rm -rf /root/.cache
USER engine
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)"
CMD ["tc", "--config", "/app/repo/config.yml", "--env", "/srv/tc/.env", "run"]
```

**compose service** (add under `services:`; leave broker/scheduler untouched for Phase 0):

```yaml
  engine:
    build: {context: .., dockerfile: docker/Dockerfile.engine}
    image: ${TC_ENGINE_IMAGE:-ghcr.io/kilowhisky/trade-challenge-engine:latest}
    container_name: tc-engine
    restart: unless-stopped
    environment: {TZ: America/New_York}
    env_file: [/srv/tc/.env]
    volumes:
      - ..:/app/repo:ro
      - /srv/tc/data:/data
      - /srv/tc/.env:/srv/tc/.env:ro
    network_mode: host          # binds 127.0.0.1:8080 only; tailscale serve proxies to it
    mem_limit: 512m
    oom_score_adj: 300
```

`/srv/tc/.env` holds `TC_SCHWAB_APP_KEY`, `TC_SCHWAB_APP_SECRET`, `TC_DISCORD_WEBHOOK_URL`, `TC_DISCORD_SHADOW_WEBHOOK_URL`, `TC_HEALTHCHECKS_BASE_URL` (Chris creates it; document in `host/README.md`).

**healthprobe.py** (stdlib): GET `http://127.0.0.1:8080/health` with 5 s timeout; POST to the webhook in `/etc/tc/probe.env` (`PROBE_WEBHOOK=…`, read by the unit's `EnvironmentFile`) when: unreachable/non-200; `ok` false; `token_days_until_dead <= 2`; `last_broker_read_age_s > 1200` during 09:30–16:00 ET on a weekday; `positions_without_stop > 0`; `pending_approval_age_s` not null and `> 600`; `runner_ok is False`; `db_ok is False`. Dedupe: write the last message hash to `/var/lib/tc/probe.last` and skip an identical message inside 30 minutes. Pure function `evaluate(health: dict | None, now_et: datetime) -> list[str]` is what the test covers.

Timer: `OnBootSec=2min`, `OnUnitActiveSec=2min`; service `Type=oneshot`, `ExecStart=/usr/bin/python3 /opt/tc/host/healthprobe.py`.

- [ ] Steps: failing tests for `evaluate` (each breach, the RTH-only rule, `None` health → "unreachable"), implement, gate. Build check on the laptop if docker is present: `docker build -f docker/Dockerfile.engine -t tc-engine:dev . && docker run --rm tc-engine:dev tc --help`. Commit — "engine: a container of its own, and a probe outside docker that posts through a webhook the engine does not own".

---

### Task 13: Deploy runbook and Phase 0 checklist

**Files:**
- Modify: `HANDOFF.md`, `host/README.md`
- Create: `docs/superpowers/plans/2026-09-04-v3-phase0-runbook.md`

Contents (no code): the exact commands to bring the engine up beside the old stack on the Pi — create `/srv/tc/data`, `/srv/tc/.env`, `docker compose build engine`, `docker compose up -d engine`, `tc seed-hwm` from the latest status file's mark, `tc shadow-diff` nightly command, `tailscale serve --bg --https=443 http://127.0.0.1:8080`, install the probe timer; the Phase 0 exit checklist from spec §11 as checkboxes; rollback `docker compose down engine`. Commit — "docs: Phase 0 runbook".

---

## Self-review

**Spec coverage.** §3 scheduler (T1), topology/engine container (T12), §6 tables used: account/position/order snapshots, ticks, session_status, job_runs, token_events, alerts, expectations_log (T3–T8); `store/export.py`, `orders`, `stops`, `proposals`, `approvals`, research ledgers → Plans 0c/1 by design. §7 layers: pings (T2, T10), host probe (T12), expectations (T8). §8 token flow: T7 + T9 callback + blind mode in T6/T10. §9 config (T1), deploy.py → Plan 1 (the bash `deploy.sh` keeps deploying the old stack until Phase 3; the engine image is pulled by hand in Phase 0 per T13). §10 replay items covered here: dead token no crash-loop (T7/T10), old-basis HWM converted not maxed (T4), old-format status parses (T11). §11 Phase 0 tooling: shadow diff (T11), runbook (T13).

**Placeholders.** None remain; every task carries test code and the implementation shape. Tasks 7, 8, 11, 12 give the interface and test list rather than full bodies because each is under 120 lines and fully specified by its interface block.

**Type consistency.** `TickRow.state` gains no new values (BLIND/STALE already exist). `SessionStatusRow` unchanged. `BookView.partial` is `list[tuple[str,int,int]]` in T3 and consumed as such in T6. `Trip(watch:int, name:str, detail:str)` used in T6 and T10. `EngineState` field names equal the `/health` JSON keys the probe reads in T12.
