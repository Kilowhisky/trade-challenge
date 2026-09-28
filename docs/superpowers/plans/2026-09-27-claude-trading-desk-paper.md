# Claude Trading Desk (paper) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the analyst/PM trading desk on the v3 engine, on paper. Four
analysts file pitches. A PM makes up to five calls a day and funds some of
them into a paper book behind a Discord veto window. Engine code scores every
pitch and every call against SPY and runs the paper book's fills and exits.

**Architecture:**

- **Logic and state:** a new package `engine/tc/desk/` holds pure logic plus
  the store operations for new SQLite tables.
- **Tools Claude calls:** they live in `engine/tc/mcp/tools_desk.py`. Analyst
  tools go on the existing `research` role and PM tools on the existing
  `decide` role.
- **Engine jobs:** new jobs are wired in `engine/tc/main.py`, and new agent
  prompts are added under `.claude/agents/`.
- **Who does what:** Claude decides only through typed tools. The engine
  validates, sizes, stamps prices, fills the paper book, exits and scores.
- **No real orders:** nothing reaches Schwab. The order path is Plan 1,
  revised as a separate plan (spec §11, §16).

**Tech Stack:** Python 3.12, pydantic 2, aiosqlite, `mcp==1.30.0` FastMCP,
httpx, pytest + pytest-asyncio (auto mode) + hypothesis, mypy `--strict`,
ruff.

**Spec:** `docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md`.
Read it before starting. Every section reference below (`spec §N`) is to it,
and `CLAUDE.md §N` is the trading manual.

**How to run things** (from the repo root):

| What | Command |
|---|---|
| Tests | `cd engine && .venv/bin/pytest -q` (baseline 2026-09-27: 682 passed) |
| One file | `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_store.py` |
| Types | `cd engine && .venv/bin/mypy` |
| Lint | `cd engine && .venv/bin/ruff check tc ../tests/engine` |
| Consistency | `cd engine && .venv/bin/tc check-consistency --repo ..` and `scripts/check-consistency.sh` |

`tests/engine/unit/test_consistency.py::test_real_repo_is_consistent` runs
the same Python checks inside the suite; the bash checker is separate and is
run by hand.

## Global Constraints

Every task's requirements include these. The values are copied from the spec.

- **Money:** always `Decimal`, never `float`. Prices cross the MCP boundary
  as strings, both ways.
- **Engine-stamped prices:** the model never types a price. Reference
  prices, fills and benchmark levels come from broker reads or stored bars
  (spec §3.3).
- **Tool names:** no MCP tool name may match `place|cancel|replace|order`
  (`tc/mcp/registry.py` `FORBIDDEN`).
- **No broker writes:** the broker stays read-only, and nothing in this plan
  imports or calls an order method.
- **Analysts:**
  - At most 5 pitches per evening run and 2 new per pre-open run.
  - Horizon 2–20 trading days.
  - Target and invalidation within 30% of the price.
  - Conviction 1–5.
  - Benchmark must be SPY or one of the 11 sector SPDRs.
  - One open pitch per analyst per symbol and direction.
- **PM:** at most 5 new calls per day, and one open call per symbol and
  direction.
- **Sizing:**
  - Conviction 3/4/5 means shares at 10/15/20% of equity and option premium
    at 5/7.5/10%.
  - Conviction 1–2 is never funded.
  - At most 8 funded positions.
  - Total cash ≥ $900.00.
  - Open option premium ≤ 30%.
  - §3.8: correlated cluster ≤ 50%.
  - Share funding requires daily ATR ≤ 6%.
- **Options:**
  - DTE ≥ max(18, horizon in calendar days + 5 + 7) and ≤ 60.
  - Delta 0.45–0.75, ranked toward 0.60.
  - OI ≥ 500; spread ≤ 10% of mid; premium within the cap.
  - Close at 5 DTE.
- **Veto window:** 10 minutes. Proposals go out only between 10:00 and
  15:00 ET.
- **Share stop:** trigger = max(§3.4 formula trigger, invalidation), with the
  limit 5% below the trigger.
- **Checkpoint:**
  - 2026-12-31, or later once ≥ 40 PM calls have resolved.
  - Analysts are reviewed at ≥ 15 resolved pitches.
  - Bootstrap CI is shown once a group has ≥ 20 resolved.
- **Schedule (ET):**

  | Job | When |
  |---|---|
  | `bars_refresh` | 16:10 |
  | `desk_evening` (analyst chain) | 16:30 |
  | `desk_preopen` | 08:00 |
  | `pm` | 09:50 |
  | `pm_midday` | 12:30 |
  | `desk_watch` | every 5m 09:55–15:55 |
  | `scorecard_weekly` | Sat 08:30 |

- **Models:** analyst agents use `model: sonnet`; the PM agent uses
  `model: opus`.
- **Build timing:** the server shares Chris's Claude subscription, so do the
  build and any heavy session **outside 09:30–16:30 ET**.
- **Public repo:** no account number, `accountHash`, token or personal
  identifier in any diff (`CLAUDE.md §7.4`).
- **Git:** stage explicit paths and never `git add -A`. Commit messages end
  with the `Co-Authored-By` line from the session's system reminder.

## Deviations from the spec (decided while planning; flag any you dislike)

1. **Non-numeric desk settings live in `config.yml` under `desk:`.** These
   are the ETF list, context symbols, checkpoint date, entry window and bars
   sizing. `Rules.load` accepts numbers only, so they cannot go in
   `rules.yml`. Numeric rules go in `rules.yml` as spec §12.2 lists, plus
   one new key, `max_entry_chase_pct`.
2. **The spec's "analyst" and "pm" roles are the existing `research` and
   `decide` MCP roles.** The bearers already installed on the Pi therefore
   keep working unchanged.
3. **Two PM tools the spec implied but did not list:** `paper_book` (the
   desk's book during the paper phase) and `call_tighten` (spec §6 "tighten").
4. **The paper veto is processed by `desk_watch` every 5 minutes.** A paper
   entry fills at the ask on the first run after the window closes, so within
   5 minutes of it, rather than at the exact instant.
5. **A call's intraday touches are scored from its second session.** Its
   first daily bar partly predates the 09:50 reference.
6. **A pitch whose opening print is already through a level** resolves as
   `open_through` with a 0% return.
7. **`strategy.option_min_delta` becomes 0.45** (the manual floor) rather
   than being deleted. Both consistency checkers keep their delta checks.
8. **The CLAUDE.md header's description of the playbook is left alone.** It
   is manual text, and it belongs in the §12.3 amendment, which needs
   Chris's own words.

## Review Focus

These are the five inputs most likely to bite that the spec implies but no
feature test would naturally hit. Each has a pinning test in the task named.

1. **A pitch filed the evening before a market holiday** gets a weekday
   reference session with no bars. It must reference the next real session
   and not sit open forever (Task 6,
   `test_a_pitch_stamped_on_a_holiday_references_the_next_session`).
2. **A name that stops trading mid-horizon** (halt or delisting) has missing
   bars. It must stay open, not resolve, and be named as stuck after 3
   sessions (Task 6, `test_missing_bars_keep_an_item_open_and_are_named_after_3_sessions`).
3. **The token dying halfway through `bars_refresh`** must stop the sweep,
   keep what was fetched, and report `failed`, not `done` (Task 4,
   `test_a_dead_token_mid_sweep_stops_and_says_blind`).
4. **A model writing a price as `"$48.25"`, `"48,25"`, `"NaN"` or a bare
   number** must be refused with a sentence it can act on (Task 5,
   `test_prices_the_model_might_write_are_refused_with_a_reason`).
5. **Option symbols with Schwab's space padding versus the model's unpadded
   copy**, and expiry parsing from the OSI, must match and parse either way
   (Task 13, `test_call_submit_accepts_an_option_symbol_without_padding`;
   Task 10, `test_osi_expiry_parses_padded_and_unpadded_symbols`).

---

## File Structure

**Create:**

| Path | Responsibility |
|---|---|
| `engine/tc/desk/__init__.py` | Package docstring only |
| `engine/tc/desk/models.py` | Shared literals (`Analyst`, `Direction`, `Funding`, `Instrument`), `BENCHMARKS`, job→analyst map, `DeskRefused`, `ActiveJob` |
| `engine/tc/desk/indicators.py` | Pure bar math: ATR%, SMA, % change, relative strength, 52w high/low, volume ratio, gap, log-return correlation |
| `engine/tc/desk/bars.py` | Which symbols get bars; the rate-paced fetch into `bars` |
| `engine/tc/desk/pitches.py` | Pitch input model, validation, insert/withdraw/read, reference session |
| `engine/tc/desk/scoring.py` | Resolution of pitches and calls from bars; the scoring pass; records |
| `engine/tc/desk/briefing.py` | Per-analyst briefings computed from bars and movers |
| `engine/tc/desk/sizing.py` | Conviction sizing, book caps, §3.8 cluster, stop trigger |
| `engine/tc/desk/options.py` | Contract selection against §3.2 floors; OSI expiry parsing |
| `engine/tc/desk/calls.py` | Call rows: insert/read, extensions, tightenings |
| `engine/tc/desk/paper.py` | Paper book: start row, proposals, outcomes, fills, positions, cash, marks, exit requests, `book_state` |
| `engine/tc/desk/scorecard.py` | Group statistics, bootstrap CI, checkpoint verdict, Discord rendering |
| `engine/tc/desk/pm.py` | `submit_call`, `extend_call`, `tighten_call`, `request_exit_for`, and the PM's views |
| `engine/tc/desk/approval.py` | Reading ✅/❌ reactions off a Discord message |
| `engine/tc/desk/watch.py` | `desk_watch`: veto processing, paper fills, paper exits |
| `engine/tc/desk/post.py` | Proposal and chain-summary Discord text; posting proposals |
| `engine/tc/mcp/tools_desk.py` | The analyst and PM MCP tools |
| `.claude/agents/analyst-{technical,earnings,news,macro}.md` | Analyst prompts (`model: sonnet`) |
| `.claude/agents/pm.md` | PM prompt (`model: opus`) |
| `tests/engine/unit/desk_fixtures.py` | Shared test builders (bars, store, settings) |
| `tests/engine/unit/test_desk_*.py` | One test file per desk module |

**Modify:**

| Path | Change |
|---|---|
| `engine/tc/broker/models.py` | `DailyBar.volume` |
| `engine/tc/store/schema.sql` | Desk tables |
| `engine/tc/store/db.py` | `transaction()`, bars ops |
| `engine/tc/config.py` | `DeskConfig`, `discord_approver_id` |
| `engine/tc/notify.py` | `post_message` returning the message id |
| `engine/tc/mcp/registry.py` | `ANALYST_TOOLS`, `PM_TOOLS`; later drop `RESEARCH_TOOLS` |
| `engine/tc/mcp/server.py` | `McpDeps.active`, `McpDeps.trading_day` |
| `engine/tc/jobs/spec.py` | Desk job specs and verdicts; later drop the old six |
| `engine/tc/jobs/dispatch.py` | Per-role bearers, busy retry, one PM retry, desk relays |
| `engine/tc/main.py` | Desk jobs, chains, proposal posting, reactions |
| `engine/tc/http/app.py` | `/api/scorecard` |
| `engine/tc/rules/consistency.py` | Tightness rows, chained jobs, dead strategy keys, no research cadence |
| `config.yml`, `rules.yml`, `strategy.md`, `CHANGELOG.md` | Settings, rules and playbook |
| `scripts/check-consistency.sh` | Remove the v2-crontab check 5 only |

**Delete (Task 18):**

- `engine/tc/mcp/tools_research.py`
- `engine/tc/research/cohort.py`
- `tests/engine/unit/test_tools_research.py`
- `tests/engine/unit/test_cohort.py`

The retired prompts are **tombstoned**, not deleted.

---

### Task 1: Desk schema and the bars cache

**Files:**
- Modify: `engine/tc/broker/models.py` (class `DailyBar`)
- Modify: `engine/tc/store/schema.sql` (append)
- Modify: `engine/tc/store/db.py` (imports; new methods after `record_artifact`)
- Create: `tests/engine/unit/desk_fixtures.py` (plain builders)
- Create: `tests/engine/unit/conftest.py` (the `desk_store` fixture; there is no conftest in `tests/engine/` today)
- Test: `tests/engine/unit/test_desk_store.py`

**Interfaces:**
- Produces:
  - `DailyBar.volume: int`, which defaults to 0.
  - `Store.transaction() -> AbstractAsyncContextManager[aiosqlite.Connection]`.
  - `Store.upsert_bars(symbol: str, bars: Sequence[DailyBar]) -> int`.
  - `Store.bars_for(symbol: str, *, since: date | None = None, limit: int | None = None) -> list[DailyBar]`, oldest first.
  - `Store.bar_counts() -> dict[str, int]`.
  - Every desk table used by later tasks (listed in Step 3).
  - `desk_fixtures`:
    - `REPO`, `RULES`
    - `sessions(start, n)`, `bar(d, o, h, lo, c, v=...)`
    - `flat_bars(start, n, price="100", volume=1_000_000)`
    - `trend_bars(start, n, first="50", step="0.5", spread="1", volume=1_000_000)`
  - `conftest.py`: the fixture `desk_store`, an opened in-memory `Store`.

- [ ] **Step 1: Write the shared test builders**

Create `tests/engine/unit/conftest.py`:

```python
"""Fixtures shared by the desk tests. The only conftest in tests/engine:
every older test file builds its own store, and nothing here shadows a name
they use."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tc.store.db import Store


@pytest.fixture
async def desk_store() -> AsyncIterator[Store]:
    # ":memory:" -- nothing in the desk tests asserts durability, and an
    # in-memory database leaves no WAL behind for the next test.
    s = Store(Path(":memory:"))
    await s.open()
    yield s
    await s.close()
```

Create `tests/engine/unit/desk_fixtures.py`:

```python
"""Builders shared by the desk tests (tc/desk/). Plain functions; each test
file imports what it uses by name, so a reader can see where a helper comes
from. The store fixture is in conftest.py."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tc.broker.models import DailyBar
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
RULES = Rules.load(REPO / "rules.yml")


def sessions(start: date, n: int) -> list[date]:
    """`n` weekdays from `start` inclusive. Holidays are not modelled; a test
    that needs one drops the date from its bars on purpose."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


Px = str | float | Decimal


def bar(d: date, o: Px, h: Px, lo: Px, c: Px, v: int = 1_000_000) -> DailyBar:
    return DailyBar(date=d, open=Decimal(str(o)), high=Decimal(str(h)),
                    low=Decimal(str(lo)), close=Decimal(str(c)), volume=v)


def flat_bars(start: date, n: int, price: str = "100", volume: int = 1_000_000) -> list[DailyBar]:
    p = Decimal(price)
    return [DailyBar(date=d, open=p, high=p, low=p, close=p, volume=volume)
            for d in sessions(start, n)]


def trend_bars(start: date, n: int, first: str = "50", step: str = "0.5",
               spread: str = "1", volume: int = 1_000_000) -> list[DailyBar]:
    """Opens at `first`, closes `step` higher each session, ranges `spread`
    either side of the open; each open is the previous close."""
    out: list[DailyBar] = []
    p, s, sp = Decimal(first), Decimal(step), Decimal(spread)
    for d in sessions(start, n):
        out.append(DailyBar(date=d, open=p, high=max(p + sp, p + s), low=p - sp,
                            close=p + s, volume=volume))
        p += s
    return out
```

- [ ] **Step 2: Write the failing tests**

Create `tests/engine/unit/test_desk_store.py`:

```python
"""Task 1: the bars cache and the desk tables' append-only guarantees."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from desk_fixtures import bar, flat_bars

from tc.broker.models import DailyBar
from tc.store.db import Store

PITCH_SQL = (
    "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
    " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
    " VALUES ('news','2026-09-28T20:00:00+00:00','2026-09-29','AAA','up','t','[]',"
    "'11','9',5,3,'SPY')"
)


def test_daily_bar_reads_volume_and_defaults_it_to_zero() -> None:
    raw = {"datetime": 1756771200000, "open": 1, "high": 2, "low": 0.5, "close": 1.5}
    assert DailyBar.from_payload({**raw, "volume": 1234}).volume == 1234
    assert DailyBar.from_payload(raw).volume == 0


async def test_upsert_bars_replaces_a_corrected_bar(desk_store: Store) -> None:
    d = date(2026, 9, 21)
    await desk_store.upsert_bars("AAA", [bar(d, 10, 11, 9, 10.5)])
    await desk_store.upsert_bars("AAA", [bar(d, 10, 11, 9, 10.75)])
    got = await desk_store.bars_for("AAA")
    assert [b.close for b in got] == [Decimal("10.75")]


async def test_bars_for_is_oldest_first_and_limit_keeps_the_newest(desk_store: Store) -> None:
    bars = flat_bars(date(2026, 9, 1), 5)
    await desk_store.upsert_bars("AAA", bars)
    assert [b.date for b in await desk_store.bars_for("AAA", limit=2)] == [
        bars[3].date, bars[4].date,
    ]
    since = await desk_store.bars_for("AAA", since=bars[2].date)
    assert [b.date for b in since] == [b.date for b in bars[2:]]
    assert (await desk_store.bars_for("AAA"))[0].volume == 1_000_000


async def test_bar_counts_per_symbol(desk_store: Store) -> None:
    await desk_store.upsert_bars("AAA", flat_bars(date(2026, 9, 1), 3))
    await desk_store.upsert_bars("BBB", flat_bars(date(2026, 9, 1), 5))
    assert await desk_store.bar_counts() == {"AAA": 3, "BBB": 5}


@pytest.mark.parametrize("table_sql,update", [
    (PITCH_SQL, "UPDATE pitches SET target='12'"),
])
async def test_desk_ledgers_are_append_only(desk_store: Store, table_sql: str, update: str) -> None:
    await desk_store.execute(table_sql)
    with pytest.raises(Exception, match="append-only"):
        await desk_store.execute(update)
    with pytest.raises(Exception, match="append-only"):
        await desk_store.execute("DELETE FROM pitches")


async def test_transaction_rolls_back_on_error(desk_store: Store) -> None:
    with pytest.raises(RuntimeError):
        async with desk_store.transaction() as c:
            await c.execute(PITCH_SQL)
            raise RuntimeError("boom")
    assert await desk_store.fetchall("SELECT id FROM pitches") == []
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_store.py`
Expected: FAIL (`DailyBar` has no field `volume` / no such table: pitches).

- [ ] **Step 3: Implement**

In `engine/tc/broker/models.py`, class `DailyBar`, add the field after
`close` and read it in `from_payload`:

```python
    close: Decimal
    # Share volume. 0 when the payload carries none (older fixtures do not);
    # a volume screen reads 0 as "no data", never as "no trading".
    volume: int = 0
```

```python
            close=_dec(c["close"]),
            volume=_int(c.get("volume")),
        )
```

Append to `engine/tc/store/schema.sql`:

```sql
-- === The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md §13) ===
-- Daily bars are a cache of the broker's own history, not a ledger: a
-- re-fetch may correct a bar, so a row is replaced on conflict.
CREATE TABLE IF NOT EXISTS bars (
  symbol TEXT NOT NULL, date TEXT NOT NULL,
  open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
  volume INTEGER NOT NULL,
  PRIMARY KEY (symbol, date)
);
-- A pitch is an analyst's prediction (§5), filed once and never edited.
CREATE TABLE IF NOT EXISTS pitches (
  id INTEGER PRIMARY KEY,
  analyst TEXT NOT NULL CHECK (analyst IN ('technical','earnings','news','macro')),
  filed_at TEXT NOT NULL,
  session TEXT NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up','down')),
  thesis TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  target TEXT NOT NULL,
  invalidation TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,
  conviction INTEGER NOT NULL CHECK (conviction BETWEEN 1 AND 5),
  benchmark TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pitch_withdrawals (
  pitch_id INTEGER PRIMARY KEY REFERENCES pitches(id), at TEXT NOT NULL
);
-- A call is the PM's prediction (§6). ref/spy_ref/bench_ref are stamped by
-- the engine from live quotes; the model never types a price.
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY,
  made_at TEXT NOT NULL,
  session TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('pitch','pm','legacy')),
  pitch_id INTEGER REFERENCES pitches(id),
  extends_call_id INTEGER REFERENCES calls(id),
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up','down')),
  thesis TEXT NOT NULL,
  target TEXT NOT NULL,
  invalidation TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,
  conviction INTEGER NOT NULL CHECK (conviction BETWEEN 1 AND 5),
  benchmark TEXT NOT NULL,
  ref_price TEXT NOT NULL,
  spy_ref TEXT NOT NULL,
  bench_ref TEXT NOT NULL,
  funding TEXT NOT NULL CHECK (funding IN ('none','shares','call','put'))
);
CREATE TABLE IF NOT EXISTS call_tightenings (
  id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id),
  at TEXT NOT NULL, invalidation TEXT NOT NULL
);
-- One resolution per item, written once (§7). UNIQUE is the idempotency
-- guard: a second scoring pass over the same day skips, never rewrites.
CREATE TABLE IF NOT EXISTS resolutions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('pitch','call')),
  item_id INTEGER NOT NULL,
  ref_date TEXT NOT NULL, ref_price TEXT NOT NULL,
  resolved_on TEXT NOT NULL,
  how TEXT NOT NULL CHECK (how IN ('target','invalidation','horizon','gap_target','gap_invalidation','open_through')),
  exit_price TEXT NOT NULL,
  ret_pct TEXT NOT NULL, spy_ret_pct TEXT NOT NULL, bench_ret_pct TEXT NOT NULL,
  written_at TEXT NOT NULL,
  UNIQUE (kind, item_id)
);
-- The paper book (§10). One row: when it started and with what.
CREATE TABLE IF NOT EXISTS paper_book (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  start_date TEXT NOT NULL, start_equity TEXT NOT NULL, created_at TEXT NOT NULL
);
-- Operational rows, not ledgers: posting stamps posted_at/veto_deadline/
-- message_id onto a proposal after it is created.
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  call_id INTEGER NOT NULL REFERENCES calls(id),
  created_at TEXT NOT NULL,
  instrument TEXT NOT NULL CHECK (instrument IN ('shares','call','put')),
  symbol TEXT NOT NULL,
  underlying TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  max_entry_price TEXT NOT NULL,
  atr_pct TEXT,
  posted_at TEXT, veto_deadline TEXT, message_id TEXT
);
CREATE TABLE IF NOT EXISTS proposal_outcomes (
  proposal_id INTEGER PRIMARY KEY REFERENCES proposals(id),
  at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('filled','skipped_price','skipped_invalid','skipped_blind','expired')),
  vetoed INTEGER NOT NULL, approved INTEGER NOT NULL, detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fills (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  at TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  price TEXT NOT NULL,
  reason TEXT NOT NULL,
  stop_trigger TEXT, stop_limit TEXT
);
CREATE TABLE IF NOT EXISTS paper_marks (
  symbol TEXT PRIMARY KEY, price TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exit_requests (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, symbol TEXT NOT NULL,
  proposal_id INTEGER REFERENCES proposals(id), reason TEXT NOT NULL, done_at TEXT
);
CREATE TRIGGER IF NOT EXISTS pitches_no_update BEFORE UPDATE ON pitches BEGIN SELECT RAISE(ABORT, 'pitches is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitches_no_delete BEFORE DELETE ON pitches BEGIN SELECT RAISE(ABORT, 'pitches is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitch_withdrawals_no_update BEFORE UPDATE ON pitch_withdrawals BEGIN SELECT RAISE(ABORT, 'pitch_withdrawals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitch_withdrawals_no_delete BEFORE DELETE ON pitch_withdrawals BEGIN SELECT RAISE(ABORT, 'pitch_withdrawals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS calls_no_update BEFORE UPDATE ON calls BEGIN SELECT RAISE(ABORT, 'calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS calls_no_delete BEFORE DELETE ON calls BEGIN SELECT RAISE(ABORT, 'calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS call_tightenings_no_update BEFORE UPDATE ON call_tightenings BEGIN SELECT RAISE(ABORT, 'call_tightenings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS call_tightenings_no_delete BEFORE DELETE ON call_tightenings BEGIN SELECT RAISE(ABORT, 'call_tightenings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_update BEFORE UPDATE ON resolutions BEGIN SELECT RAISE(ABORT, 'resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_delete BEFORE DELETE ON resolutions BEGIN SELECT RAISE(ABORT, 'resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposal_outcomes_no_update BEFORE UPDATE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposal_outcomes_no_delete BEFORE DELETE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_fills_no_update BEFORE UPDATE ON paper_fills BEGIN SELECT RAISE(ABORT, 'paper_fills is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_fills_no_delete BEFORE DELETE ON paper_fills BEGIN SELECT RAISE(ABORT, 'paper_fills is append-only'); END;
```

In `engine/tc/store/db.py`, change the imports:

```python
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
```

```python
from tc.broker.models import (
    RESTING, STOP_TYPES, AccountSnapshot, DailyBar, OrderRow, Position,
)
```

Add these methods to `Store` after `record_artifact`:

```python
    def transaction(self) -> AbstractAsyncContextManager[aiosqlite.Connection]:
        """`_transaction`, public, for the desk modules that own their tables
        (tc/desk/). Same contract: the caller must not already hold the lock."""
        return self._transaction()

    # --- desk: daily bars (trading-desk design §13) -------------------------
    async def upsert_bars(self, symbol: str, bars: Sequence[DailyBar]) -> int:
        async with self._transaction() as c:
            await c.executemany(
                "INSERT INTO bars(symbol, date, open, high, low, close, volume)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol, date) DO UPDATE SET"
                " open=excluded.open, high=excluded.high, low=excluded.low,"
                " close=excluded.close, volume=excluded.volume",
                [
                    (symbol, b.date.isoformat(), str(b.open), str(b.high), str(b.low),
                     str(b.close), b.volume)
                    for b in bars
                ],
            )
        return len(bars)

    async def bars_for(
        self, symbol: str, *, since: date | None = None, limit: int | None = None
    ) -> list[DailyBar]:
        """Oldest first. `limit` keeps the NEWEST `limit` bars."""
        sql = "SELECT date, open, high, low, close, volume FROM bars WHERE symbol=?"
        params: list[Any] = [symbol]
        if since is not None:
            sql += " AND date >= ?"
            params.append(since.isoformat())
        sql += " ORDER BY date DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = await self.fetchall(sql, tuple(params))
        return [
            DailyBar(
                date=date.fromisoformat(r["date"]), open=Decimal(r["open"]),
                high=Decimal(r["high"]), low=Decimal(r["low"]), close=Decimal(r["close"]),
                volume=int(r["volume"]),
            )
            for r in reversed(rows)
        ]

    async def bar_counts(self) -> dict[str, int]:
        rows = await self.fetchall("SELECT symbol, COUNT(*) AS n FROM bars GROUP BY symbol")
        return {r["symbol"]: int(r["n"]) for r in rows}
```

- [ ] **Step 4: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: all pass (682 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add engine/tc/broker/models.py engine/tc/store/schema.sql engine/tc/store/db.py \
  tests/engine/unit/conftest.py tests/engine/unit/desk_fixtures.py tests/engine/unit/test_desk_store.py
git commit -m "desk: the schema, and a bars cache that carries volume"
```

---

### Task 2: Desk settings and rule keys

**Files:**
- Modify: `engine/tc/config.py`
- Modify: `config.yml` (new `desk:` block)
- Modify: `rules.yml` (strategy section)
- Modify: `strategy.md` (the one `strategy_option_min_delta` line)
- Modify: `engine/tc/rules/consistency.py` (`TIGHTNESS`)
- Test: `tests/engine/unit/test_desk_config.py`

**Interfaces:**
- Produces:
  - `DeskConfig`, with fields:
    - `etf_list: list[str]`, `context_symbols: list[str]`
    - `checkpoint_date: date`
    - `entry_window_start: time`, `entry_window_end: time`
    - `bars_universe_size: int`, `bars_history_days: int`, `bars_request_spacing_s: float`
  - `Settings.desk: DeskConfig`.
  - Strategy rule keys (read with `rules.get("strategy", key)`):

    | Key | Value |
    |---|---|
    | `desk_max_pitches_per_analyst_run` | 5 |
    | `desk_preopen_max_new_pitches` | 2 |
    | `desk_max_new_calls_per_day` | 5 |
    | `pitch_horizon_min_days` | 2 |
    | `pitch_horizon_max_days` | 20 |
    | `pitch_level_max_distance_pct` | 30 |
    | `min_fundable_conviction` | 3 |
    | `size_shares_pct_conviction_3` / `_4` / `_5` | 10 / 15 / 20 |
    | `size_option_premium_pct_conviction_3` / `_4` / `_5` | 5 / 7.5 / 10 |
    | `max_funded_positions` | 8 |
    | `option_max_dte` | 60 |
    | `option_target_delta` | 0.60 |
    | `option_exit_reprice_minutes` | 15 |
    | `veto_window_minutes` | 10 |
    | `max_entry_chase_pct` | 5 |
    | `checkpoint_min_pm_calls` | 40 |
    | `analyst_review_min_pitches` | 15 |
    | `scorecard_ci_min_n` | 20 |
    | `option_min_delta` | 0.45 |

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_config.py`:

```python
"""Task 2: the desk's settings block and rule keys."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

from desk_fixtures import REPO, RULES

from tc.config import Settings, load_settings
from tc.rules.consistency import run_checks

BASE = """
engine:
  data_dir: {p}
  repo_dir: {repo}
  research_dir: {p}/research
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n"


def _load(tmp_path: Path, extra: str = "") -> Settings:
    (tmp_path / "config.yml").write_text(BASE.format(p=tmp_path, repo=REPO) + extra)
    (tmp_path / ".env").write_text(ENV)
    return load_settings(tmp_path / "config.yml", tmp_path / ".env")


def test_desk_block_is_optional_with_safe_defaults(tmp_path: Path) -> None:
    s = _load(tmp_path)
    assert s.desk.etf_list == []
    assert s.desk.checkpoint_date == date(2026, 12, 31)
    assert (s.desk.entry_window_start, s.desk.entry_window_end) == (time(10, 0), time(15, 0))


def test_desk_symbols_are_normalised_to_upper_case(tmp_path: Path) -> None:
    s = _load(tmp_path, "desk:\n  etf_list: [spy, ' xlk ']\n  context_symbols: [uup]\n")
    assert s.desk.etf_list == ["SPY", "XLK"]
    assert s.desk.context_symbols == ["UUP"]


def test_repo_config_carries_the_spec_etf_list() -> None:
    import yaml

    cfg = yaml.safe_load((REPO / "config.yml").read_text())
    etfs = cfg["desk"]["etf_list"]
    for sym in ("SPY", "QQQ", "XLK", "XLE", "SMH", "GLD", "TLT", "USO"):
        assert sym in etfs


def test_desk_rule_keys_exist_with_spec_values() -> None:
    g = lambda k: RULES.get("strategy", k)  # noqa: E731
    assert g("desk_max_pitches_per_analyst_run") == 5
    assert g("desk_preopen_max_new_pitches") == 2
    assert g("desk_max_new_calls_per_day") == 5
    assert (g("pitch_horizon_min_days"), g("pitch_horizon_max_days")) == (2, 20)
    assert g("pitch_level_max_distance_pct") == 30
    assert [g(f"size_shares_pct_conviction_{c}") for c in (3, 4, 5)] == [10, 15, 20]
    assert [g(f"size_option_premium_pct_conviction_{c}") for c in (3, 4, 5)] == [
        5, Decimal("7.5"), 10,
    ]
    assert g("max_funded_positions") == 8
    assert g("option_target_delta") == Decimal("0.60")
    assert g("option_min_delta") == Decimal("0.45")
    assert g("veto_window_minutes") == 10


def test_the_repo_stays_consistent_with_the_new_keys() -> None:
    rep = run_checks(REPO)
    assert rep.ok, [f.message for f in rep.findings]
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_config.py`
Expected: FAIL (`Settings` has no attribute `desk`; `no such rule`).

- [ ] **Step 2: Implement `DeskConfig`**

In `engine/tc/config.py`, extend the imports:

```python
from datetime import date, time
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
```

Add the model after `Expectation`:

```python
class DeskConfig(BaseModel):
    """The desk's non-numeric settings (trading-desk design §12.2).

    rules.yml's loader accepts numbers only, so a list of tickers, a calendar
    date and a clock window live here. Every numeric desk rule is in
    rules.yml's strategy section and read through `Rules`."""

    model_config = ConfigDict(extra="forbid")
    etf_list: list[str] = Field(default_factory=list)
    context_symbols: list[str] = Field(default_factory=list)
    checkpoint_date: date = date(2026, 12, 31)
    entry_window_start: time = time(10, 0)
    entry_window_end: time = time(15, 0)
    bars_universe_size: int = Field(default=400, ge=0)
    bars_history_days: int = Field(default=260, ge=60)
    bars_request_spacing_s: float = Field(default=0.5, ge=0)

    @field_validator("etf_list", "context_symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]
```

Add `desk: DeskConfig = Field(default_factory=DeskConfig)` to `FileConfig`
and to `Settings`, after `expectations`. In `load_settings`, pass
`desk=file_cfg.desk` into the `Settings(...)` call.

- [ ] **Step 3: Add the desk block to `config.yml`**

Append to `config.yml`:

```yaml
desk:                          # trading-desk design §4.2, §8, §9.4 — non-numeric settings
  etf_list: [SPY, QQQ, IWM, DIA, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY,
             SMH, XBI, KRE, XHB, ITB, JETS, GLD, SLV, GDX, TLT, IEF, HYG, EEM, FXI, EWZ, USO]
  context_symbols: [UUP]       # macro context rows that are not on the tradeable list
  checkpoint_date: 2026-12-31  # §8, pre-registered; changes only by a recorded amendment
  entry_window_start: "10:00"
  entry_window_end: "15:00"
  bars_universe_size: 400      # the liquid universe's top N by dollar volume get daily bars
  bars_history_days: 260       # one year plus margin, for 52-week and 200-day statistics
  bars_request_spacing_s: 0.5  # ~120 price-history calls a minute, under Schwab's limit
```

- [ ] **Step 4: Add the rule keys to `rules.yml`**

In `rules.yml`'s `strategy:` section, replace the `option_min_delta` line:

```yaml
  option_min_delta: 0.45               # = the manual band floor (2026-09-27, trading-desk design §9.3: selection ranks toward option_target_delta instead of flooring above the manual)
```

Append at the end of the `strategy:` section:

```yaml
  # Trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md).
  # §5 pitches
  desk_max_pitches_per_analyst_run: 5
  desk_preopen_max_new_pitches: 2
  pitch_horizon_min_days: 2
  pitch_horizon_max_days: 20
  pitch_level_max_distance_pct: 30
  # §6 calls
  desk_max_new_calls_per_day: 5
  # §9.2 sizing, % of account value (paper equity in the paper phase)
  min_fundable_conviction: 3
  size_shares_pct_conviction_3: 10
  size_shares_pct_conviction_4: 15
  size_shares_pct_conviction_5: 20
  size_option_premium_pct_conviction_3: 5
  size_option_premium_pct_conviction_4: 7.5
  size_option_premium_pct_conviction_5: 10   # = manual option_single_position_pct
  max_funded_positions: 8
  # §9.3/§9.5 options
  option_max_dte: 60
  option_target_delta: 0.60
  option_exit_reprice_minutes: 15
  # §9.4 entries. max_entry_chase_pct bounds how far above the last price a
  # PM may set max_entry_price (plan deviation: the spec names the field, not a bound).
  veto_window_minutes: 10
  max_entry_chase_pct: 5
  # §8 checkpoint and §7.3 scorecard
  checkpoint_min_pm_calls: 40
  analyst_review_min_pitches: 15
  scorecard_ci_min_n: 20
```

In `strategy.md`, find the bullet in §6 whose rule marker names the key
`strategy_option_min_delta` (it reads "Δ ≥ 0.50 … for long premium"), and
change the bold number in front of that marker from `0.50` to `0.45`. Then
append this sentence at the end of that bullet: *(Lowered to the manual floor
2026-09-27: the trading desk ranks contracts toward Δ 0.60 inside the
manual band instead of flooring above it — trading-desk design §9.3.)*
Task 19 rewrites the whole file; this keeps the annotation check green
until then.

**This plan never spells a rule marker out literally.** Both consistency
checkers scan every `.md` in the repo, this plan included, for a bold number
followed by an HTML comment `rule:KEY`. A marker quoted here with a stale
value, or naming a key that does not exist yet, would fail the check
`CLAUDE.md §4.5` runs at session open. Where this plan shows one (Task 19),
it writes `<!-- rule:KEY -->` **with spaces**. In the real file, write it
with no spaces.

- [ ] **Step 5: Tighten the consistency checker for the new sizing keys**

In `engine/tc/rules/consistency.py`, extend `TIGHTNESS`:

```python
TIGHTNESS = (  # strategy_key, manual_key, direction, label
    ("option_min_delta", "option_min_delta", "ge", "delta-floor"),
    ("leveraged_exit_session", "leveraged_max_hold_sessions", "le", "leveraged-hold"),
    ("sleeve_options_open_pct", "option_open_premium_pct", "le", "options-open"),
    ("sleeve_leveraged_pct", "leveraged_aggregate_pct", "le", "leveraged-aggregate"),
    ("size_shares_pct_conviction_5", "single_position_pct", "le", "shares-conviction-5"),
    ("size_option_premium_pct_conviction_5", "option_single_position_pct", "le",
     "option-conviction-5"),
)
```

- [ ] **Step 6: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine && cd .. && scripts/check-consistency.sh | tail -3`
Expected: all pass; the bash checker ends `CONSISTENT`.

- [ ] **Step 7: Commit**

```bash
git add engine/tc/config.py config.yml rules.yml strategy.md engine/tc/rules/consistency.py \
  tests/engine/unit/test_desk_config.py
git commit -m "desk: settings and rule keys; the strategy delta floor meets the manual's"
```

---

### Task 3: Indicators

**Files:**
- Create: `engine/tc/desk/__init__.py`, `engine/tc/desk/indicators.py`
- Test: `tests/engine/unit/test_desk_indicators.py`

**Interfaces:**
- Consumes: `DailyBar` (Task 1).
- Produces:
  - `atr_pct(bars, n=14) -> Decimal | None`
  - `sma(bars, n) -> Decimal | None`
  - `pct_from(value, ref) -> Decimal | None`
  - `pct_change(bars, n) -> Decimal | None`
  - `rel_strength(bars, bench, n) -> Decimal | None`
  - `high_low(bars, n=252) -> tuple[Decimal, Decimal] | None`
  - `prior_high_low(bars, n=252) -> tuple[Decimal, Decimal] | None`
  - `volume_ratio(bars, n=20) -> Decimal | None`
  - `gap_pct(bars) -> Decimal | None`
  - `log_return_corr(a, b, n=60) -> Decimal | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_indicators.py`:

```python
"""Task 3: bar math. Pure functions, so most of this is properties."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from desk_fixtures import bar, flat_bars, sessions, trend_bars
from hypothesis import given
from hypothesis import strategies as st

from tc.broker.models import DailyBar
from tc.desk import indicators as ind

D0 = date(2026, 1, 5)


def test_atr_of_a_flat_series_is_zero_and_short_history_is_none() -> None:
    assert ind.atr_pct(flat_bars(D0, 20)) == 0
    assert ind.atr_pct(flat_bars(D0, 14)) is None


def test_atr_uses_the_true_range_across_a_gap() -> None:
    days = sessions(D0, 15)
    bars = [bar(d, 100, 101, 99, 100) for d in days[:14]] + [bar(days[14], 110, 111, 109, 110)]
    # 13 ranges of 2, then one true range of 111-100=11, over 14 sessions, / 110.
    expected = (Decimal(2) * 13 + 11) / 14 / 110 * 100
    assert ind.atr_pct(bars) == expected


def test_sma_pct_change_and_pct_from() -> None:
    bars = trend_bars(D0, 30, first="50", step="1")
    assert ind.sma(bars, 3) == (bars[-1].close + bars[-2].close + bars[-3].close) / 3
    assert ind.pct_change(bars, 1) == (bars[-1].close - bars[-2].close) / bars[-2].close * 100
    assert ind.pct_from(Decimal(110), Decimal(100)) == 10
    assert ind.pct_from(Decimal(110), None) is None


def test_relative_strength_of_a_series_against_itself_is_zero() -> None:
    bars = trend_bars(D0, 70)
    assert ind.rel_strength(bars, bars, 63) == 0


def test_relative_strength_needs_the_benchmark_on_the_same_dates() -> None:
    bars = trend_bars(D0, 70)
    assert ind.rel_strength(bars, bars[:-1], 63) is None


def test_high_low_and_prior_high_low_exclude_today_for_breakouts() -> None:
    days = sessions(D0, 3)
    bars = [bar(days[0], 10, 12, 9, 11), bar(days[1], 11, 13, 10, 12), bar(days[2], 12, 20, 11, 19)]
    assert ind.high_low(bars) == (Decimal(20), Decimal(9))
    assert ind.prior_high_low(bars) == (Decimal(13), Decimal(9))


def test_volume_ratio_and_gap() -> None:
    days = sessions(D0, 21)
    bars = [bar(d, 100, 101, 99, 100, v=1000) for d in days[:20]] + [
        bar(days[20], 104, 105, 103, 104, v=3000)
    ]
    assert ind.volume_ratio(bars) == 3
    assert ind.gap_pct(bars) == 4
    assert ind.volume_ratio([bar(d, 1, 1, 1, 1, v=0) for d in days]) is None


def test_correlation_of_a_series_with_itself_is_one_and_with_its_inverse_minus_one() -> None:
    a = [bar(d, 1, 1, 1, Decimal(100) + (i % 7) * 3) for i, d in enumerate(sessions(D0, 70))]
    inverse = [DailyBar(date=b.date, open=b.open, high=b.high, low=b.low,
                        close=Decimal(1) / b.close) for b in a]
    one = ind.log_return_corr(a, a)
    minus_one = ind.log_return_corr(a, inverse)
    assert one is not None and minus_one is not None
    assert abs(one - 1) < Decimal("1e-20")
    assert abs(minus_one + 1) < Decimal("1e-20")
    assert ind.log_return_corr(a[:30], a[:30]) is None


@given(st.lists(st.integers(min_value=1, max_value=10_000), min_size=61, max_size=80),
       st.lists(st.integers(min_value=1, max_value=10_000), min_size=61, max_size=80))
def test_correlation_is_always_within_minus_one_and_one(xs: list[int], ys: list[int]) -> None:
    n = min(len(xs), len(ys))
    days = sessions(D0, n)
    a = [bar(d, 1, 1, 1, x) for d, x in zip(days, xs, strict=False)]
    b = [bar(d, 1, 1, 1, y) for d, y in zip(days, ys, strict=False)]
    r = ind.log_return_corr(a, b)
    assert r is None or Decimal(-1) - Decimal("1e-20") <= r <= Decimal(1) + Decimal("1e-20")
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_indicators.py`
Expected: FAIL (`No module named 'tc.desk'`).

- [ ] **Step 2: Implement**

Create `engine/tc/desk/__init__.py`:

```python
"""The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md).

Analysts pitch, a portfolio manager calls and funds, the engine validates,
fills the paper book, exits and scores. Everything here is engine code: the
model reaches it only through the typed tools in tc/mcp/tools_desk.py.
"""
```

Create `engine/tc/desk/indicators.py`:

```python
"""Numbers the desk computes from stored daily bars. Pure: no I/O, no clock.

Every function answers None rather than guessing when the history is too
short -- a briefing row with an unknown ATR says "unknown", and a screen that
needs the number skips the name.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext

from tc.broker.models import DailyBar

HUNDRED = Decimal(100)


def _mean(xs: Sequence[Decimal]) -> Decimal:
    return sum(xs, Decimal(0)) / len(xs)


def atr_pct(bars: Sequence[DailyBar], n: int = 14) -> Decimal | None:
    """Mean true range over the last `n` sessions, as % of the last close."""
    if len(bars) < n + 1 or bars[-1].close <= 0:
        return None
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[-n - 1:-1], bars[-n:], strict=True)
    ]
    return _mean(trs) / bars[-1].close * HUNDRED


def sma(bars: Sequence[DailyBar], n: int) -> Decimal | None:
    if len(bars) < n:
        return None
    return _mean([b.close for b in bars[-n:]])


def pct_from(value: Decimal, ref: Decimal | None) -> Decimal | None:
    if ref is None or ref == 0:
        return None
    return (value - ref) / ref * HUNDRED


def pct_change(bars: Sequence[DailyBar], n: int) -> Decimal | None:
    if len(bars) < n + 1 or bars[-1 - n].close == 0:
        return None
    return (bars[-1].close - bars[-1 - n].close) / bars[-1 - n].close * HUNDRED


def rel_strength(
    bars: Sequence[DailyBar], bench: Sequence[DailyBar], n: int
) -> Decimal | None:
    """The name's n-session change minus the benchmark's over the SAME dates."""
    own = pct_change(bars, n)
    if own is None:
        return None
    start, end = bars[-1 - n].date, bars[-1].date
    closes = {b.date: b.close for b in bench}
    if start not in closes or end not in closes or closes[start] == 0:
        return None
    return own - (closes[end] - closes[start]) / closes[start] * HUNDRED


def high_low(bars: Sequence[DailyBar], n: int = 252) -> tuple[Decimal, Decimal] | None:
    if not bars:
        return None
    window = bars[-n:]
    return max(b.high for b in window), min(b.low for b in window)


def prior_high_low(bars: Sequence[DailyBar], n: int = 252) -> tuple[Decimal, Decimal] | None:
    """The range BEFORE the last bar: a breakout is today's close above it."""
    return high_low(bars[:-1], n) if len(bars) > 1 else None


def volume_ratio(bars: Sequence[DailyBar], n: int = 20) -> Decimal | None:
    """The last session's volume over the mean of the `n` before it."""
    if len(bars) < n + 1:
        return None
    avg = _mean([Decimal(b.volume) for b in bars[-n - 1:-1]])
    if avg == 0:
        return None
    return Decimal(bars[-1].volume) / avg


def gap_pct(bars: Sequence[DailyBar]) -> Decimal | None:
    if len(bars) < 2 or bars[-2].close == 0:
        return None
    return (bars[-1].open - bars[-2].close) / bars[-2].close * HUNDRED


def log_return_corr(
    a: Sequence[DailyBar], b: Sequence[DailyBar], n: int = 60
) -> Decimal | None:
    """Pearson correlation of daily log returns over the last `n` common dates
    (CLAUDE.md §3.8's measure: trailing 60-day daily-return correlation)."""
    ca = {x.date: x.close for x in a}
    cb = {x.date: x.close for x in b}
    dates = sorted(set(ca) & set(cb))[-(n + 1):]
    if len(dates) < n + 1 or any(ca[d] <= 0 or cb[d] <= 0 for d in dates):
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        ra = [(ca[d1] / ca[d0]).ln() for d0, d1 in zip(dates, dates[1:], strict=False)]
        rb = [(cb[d1] / cb[d0]).ln() for d0, d1 in zip(dates, dates[1:], strict=False)]
        ma, mb = _mean(ra), _mean(rb)
        cov = sum(((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True)), Decimal(0))
        va = sum(((x - ma) ** 2 for x in ra), Decimal(0))
        vb = sum(((y - mb) ** 2 for y in rb), Decimal(0))
        if va == 0 or vb == 0:
            return None
        return cov / (va.sqrt() * vb.sqrt())
```

- [ ] **Step 3: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_indicators.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/tc/desk/__init__.py engine/tc/desk/indicators.py tests/engine/unit/test_desk_indicators.py
git commit -m "desk: bar indicators, each honest about a history too short to answer"
```

---

### Task 4: `bars_refresh` — fetch the desk's price history each evening

**Files:**
- Create: `engine/tc/desk/bars.py`
- Modify: `engine/tc/main.py` (`JOBS`, `_execute`, new `_job_bars_refresh`)
- Modify: `config.yml` (`schedule.bars_refresh`)
- Test: `tests/engine/unit/test_desk_bars.py`

**Interfaces:**
- Consumes:
  - `Store.upsert_bars`, `Store.bar_counts`, `Store.universe_rows` (Task 1).
  - `DeskConfig` (Task 2).
- Produces:
  - `carried_symbols(store: Store) -> set[str]`
  - `bars_symbols(store: Store, desk: DeskConfig, extra: Iterable[str]) -> list[str]`
  - `BarsReport(requested: int, fetched: int, failed: list[str], blind: bool)`
  - `refresh_bars(broker, store, symbols, desk, sleep=asyncio.sleep) -> BarsReport`
  - Engine job `bars_refresh`. Task 6 adds scoring to it.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_bars.py`:

```python
"""Task 4: which symbols get bars, and the paced fetch that fills them."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk.bars import bars_symbols, carried_symbols, refresh_bars
from tc.store.db import Store

NOW = datetime(2026, 9, 28, 20, 10, tzinfo=UTC)


def _candles(n: int) -> dict[str, list[dict[str, float | int]]]:
    base = 1_756_771_200_000  # 2025-09-02
    return {"candles": [
        {"datetime": base + i * 86_400_000, "open": 10, "high": 11, "low": 9, "close": 10,
         "volume": 1000}
        for i in range(n)
    ]}


def _fx(tmp_path: Path, symbols: Sequence[str], n: int = 5) -> Path:
    for s in symbols:
        (tmp_path / f"bars-{s}.json").write_text(json.dumps(_candles(n)))
    return tmp_path


def _row(symbol: str, dollar_vol: int) -> dict[str, Any]:
    return {
        "symbol": symbol, "price": Decimal("50"), "adv10": Decimal("100000"),
        "dollar_vol": Decimal(dollar_vol), "pct_from_52wk_high": Decimal("1"),
        "optionable": True, "leverage": Decimal("0"), "last_earnings": "",
        "is_etf": False, "session_range_pct": Decimal("1.5"), "description": symbol,
        "qualified": True,
    }


async def test_symbols_are_the_most_liquid_names_plus_etfs_context_and_carried(
    desk_store: Store,
) -> None:
    await desk_store.replace_universe(date(2026, 9, 26), [
        _row("LOW", 10), _row("MID", 20), _row("TOP", 30),
    ])
    desk = DeskConfig(etf_list=["XLK"], context_symbols=["UUP"], bars_universe_size=2)
    got = await bars_symbols(desk_store, desk, {"HELD"})
    assert got == sorted({"TOP", "MID", "XLK", "UUP", "SPY", "HELD"})


async def test_carried_symbols_is_empty_on_a_fresh_store(desk_store: Store) -> None:
    assert await carried_symbols(desk_store) == set()


async def test_refresh_fetches_a_year_for_a_new_symbol_and_ten_days_after(
    tmp_path: Path, desk_store: Store,
) -> None:
    asked: list[tuple[str, int]] = []

    class Spy(FakeBroker):
        async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
            asked.append((symbol, days))
            return await super().daily_bars(symbol, days)

    broker = Spy(_fx(tmp_path, ["AAA"], n=300), NOW)
    desk = DeskConfig(bars_request_spacing_s=0)
    rep = await refresh_bars(broker, desk_store, ["AAA"], desk)
    assert (rep.fetched, rep.failed, rep.blind) == (1, [], False)
    rep = await refresh_bars(broker, desk_store, ["AAA"], desk)
    assert asked == [("AAA", 260), ("AAA", 10)]


async def test_a_symbol_the_broker_cannot_answer_is_named_not_fatal(
    tmp_path: Path, desk_store: Store,
) -> None:
    broker = FakeBroker(_fx(tmp_path, ["AAA"]), NOW)  # no bars-BBB.json
    rep = await refresh_bars(broker, desk_store, ["AAA", "BBB"], DeskConfig(bars_request_spacing_s=0))
    assert (rep.fetched, rep.failed, rep.blind) == (1, ["BBB"], False)


async def test_a_dead_token_mid_sweep_stops_and_says_blind(
    tmp_path: Path, desk_store: Store,
) -> None:
    class DiesAfterOne(FakeBroker):
        calls = 0

        async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
            DiesAfterOne.calls += 1
            if DiesAfterOne.calls > 1:
                raise BrokerUnauthorized("401")
            return await super().daily_bars(symbol, days)

    broker = DiesAfterOne(_fx(tmp_path, ["AAA", "BBB", "CCC"]), NOW)
    rep = await refresh_bars(broker, desk_store, ["AAA", "BBB", "CCC"],
                             DeskConfig(bars_request_spacing_s=0))
    assert rep.blind is True and rep.fetched == 1
    assert await desk_store.bar_counts() == {"AAA": 5}   # what was fetched is kept


async def test_requests_are_paced(tmp_path: Path, desk_store: Store) -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    broker = FakeBroker(_fx(tmp_path, ["AAA", "BBB"]), NOW)
    await refresh_bars(broker, desk_store, ["AAA", "BBB"], DeskConfig(bars_request_spacing_s=0.5),
                       sleep=sleep)
    assert slept == [0.5]  # between calls, never after the last
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_bars.py`
Expected: FAIL (`No module named 'tc.desk.bars'`).

- [ ] **Step 2: Implement `tc/desk/bars.py`**

```python
"""The desk's price history: one paced fetch per symbol per evening, into
the `bars` table every briefing, screen and score is computed from.

Which symbols: the liquid universe's head by DOLLAR VOLUME (not the weekly
sweep's proximity rank, which only holds names near their highs -- a desk
that looks for breakdowns needs the rest), the ETF list, macro context rows,
SPY always, and every symbol the desk is already carrying.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.config import DeskConfig
from tc.store.db import Store

TOP_UP_DAYS = 10


async def carried_symbols(store: Store) -> set[str]:
    """Symbols of open pitches and calls, their benchmarks, and every paper
    proposal's underlying: a name the desk is scoring must keep its bars
    even after it drops out of the liquid head."""
    rows = await store.fetchall(
        "SELECT symbol FROM pitches"
        " WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')"
        " UNION SELECT symbol FROM calls"
        " WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
        " UNION SELECT benchmark FROM pitches UNION SELECT benchmark FROM calls"
        " UNION SELECT underlying FROM proposals"
    )
    return {str(r[0]) for r in rows}


async def bars_symbols(store: Store, desk: DeskConfig, extra: Iterable[str]) -> list[str]:
    rows = await store.universe_rows(qualified_only=True)
    rows.sort(key=lambda r: r["dollar_vol"], reverse=True)
    top = [str(r["symbol"]) for r in rows[: desk.bars_universe_size]]
    return sorted({*top, *desk.etf_list, *desk.context_symbols, "SPY", *extra})


@dataclass(frozen=True)
class BarsReport:
    requested: int
    fetched: int
    failed: list[str] = field(default_factory=list)
    blind: bool = False


async def refresh_bars(
    broker: Broker,
    store: Store,
    symbols: Sequence[str],
    desk: DeskConfig,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> BarsReport:
    """A year for a symbol with little history, ten days to top one up.

    A dead token stops the sweep at once -- every further call would fail the
    same way -- and keeps what was already written; the job reports `failed`
    so the deadman sees a partial evening. Any other per-symbol failure (an
    unknown ticker, a 500, a fixture broker with no file) is named and
    skipped: one bad symbol must not cost the evening's scoring.
    """
    counts = await store.bar_counts()
    fetched = 0
    failed: list[str] = []
    for i, sym in enumerate(symbols):
        have = counts.get(sym, 0)
        days = desk.bars_history_days if have < desk.bars_history_days - TOP_UP_DAYS else TOP_UP_DAYS
        try:
            bars = await broker.daily_bars(sym, days)
        except BrokerUnauthorized:
            return BarsReport(len(symbols), fetched, failed, blind=True)
        except (BrokerError, OSError, ValueError):
            failed.append(sym)
        else:
            await store.upsert_bars(sym, bars)
            fetched += 1
        if desk.bars_request_spacing_s and i + 1 < len(symbols):
            await sleep(desk.bars_request_spacing_s)
    return BarsReport(len(symbols), fetched, failed, blind=False)
```

- [ ] **Step 3: Wire the job into the engine**

In `engine/tc/main.py`:
- Add the import `from tc.desk.bars import bars_symbols, carried_symbols, refresh_bars`.
- Add `"bars_refresh"` to `JOBS`, after `"weekly_universe"`.
- In `_execute`, before the `CLAUDE_JOBS` branch, add:

```python
        if job == "bars_refresh":
            return await self._job_bars_refresh(now)
```

Add the method after `_sweep_universe`:

```python
    async def _job_bars_refresh(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        """The desk's evening price history (trading-desk design §4). Scoring
        runs on what this wrote (Task 6 extends this method)."""
        symbols = await bars_symbols(
            self._store, self._s.desk, await carried_symbols(self._store)
        )
        rep = await refresh_bars(self._broker, self._store, symbols, self._s.desk)
        detail: dict[str, Any] = {
            "requested": rep.requested,
            "fetched": rep.fetched,
            "failed_n": len(rep.failed),
            "failed": rep.failed[:20],
        }
        if rep.blind:
            await self.notifier.post(
                f"⚠️ bars_refresh: token died after {rep.fetched} of {rep.requested}"
            )
            return "failed", {**detail, "error": "BrokerUnauthorized"}
        return "done", detail
```

Add to `config.yml` `schedule:`:

```yaml
  bars_refresh: "at 16:10 weekdays"   # trading-desk design §4: bars, then scoring
```

- [ ] **Step 4: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS. `test_every_scheduled_job_in_the_repo_config_is_a_known_job`
stays green because `bars_refresh` is in `JOBS`.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/bars.py engine/tc/main.py config.yml tests/engine/unit/test_desk_bars.py
git commit -m "desk: bars_refresh keeps a year of history for the liquid head and every carried name"
```

---
### Task 5: Pitches

**Files:**
- Create: `engine/tc/desk/models.py`, `engine/tc/desk/pitches.py`
- Test: `tests/engine/unit/test_desk_pitches.py`

**Interfaces:**
- Consumes: `Store` (Task 1), `Rules` (Task 2 keys), `DeskConfig`.
- Produces:
  - `tc.desk.models`:
    - Types: `Analyst`, `ANALYSTS`, `Direction`, `Funding`, `Instrument`
    - `BENCHMARKS: tuple[str, ...]`
    - `JOB_ANALYST: dict[str, Analyst]`, `PREOPEN_JOBS`, `PM_JOBS`
    - `DeskRefused(Exception)`
    - `ActiveJob(name: str | None)`
    - `utc_iso(dt) -> str`
  - `tc.desk.pitches`:
    - Models: `EvidenceItem`, `PitchIn`, `Pitch`
    - Checks: `parse_price(value, field) -> Decimal`, `check_levels(direction, last, target, invalidation, rules)`, `check_horizon(h, rules)`, `check_benchmark(b) -> str`
    - `reference_session(filed_at, is_trading_day) -> date`
    - `tradeable_symbols(store, desk) -> set[str]`
    - `last_close(store, symbol) -> Decimal | None`
    - `submit_pitch(store, *, analyst, pin, now, last, tradeable, rules, is_trading_day) -> Pitch`
    - `withdraw_pitch(store, *, analyst, pitch_id, now) -> None`
    - `get_pitch(store, pitch_id) -> Pitch | None`
    - `open_pitches(store) -> list[Pitch]`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_pitches.py`:

```python
"""Task 5: filing, refusing and withdrawing pitches (spec §5)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from desk_fixtures import RULES

from tc.desk.models import Analyst, DeskRefused
from tc.desk.pitches import (
    EvidenceItem,
    Pitch,
    PitchIn,
    open_pitches,
    parse_price,
    reference_session,
    submit_pitch,
    withdraw_pitch,
)
from tc.store.db import Store

EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)   # Mon 16:45 ET
PREOPEN = datetime(2026, 9, 29, 12, 5, tzinfo=UTC)    # Tue 08:05 ET
MIDDAY = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)     # Tue 12:00 ET
LAST = Decimal("100")
TRADEABLE = {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "XLK"}


def weekday(d: date) -> bool:
    return d.weekday() < 5


def _pin(**kw: object) -> PitchIn:
    base: dict[str, object] = {
        "symbol": "AAA", "direction": "up", "thesis": "demand is inflecting, see the channel checks",
        "evidence": [EvidenceItem(url="https://example.com/a", claim="a claim", date="2026-09-28")],
        "target": "110", "invalidation": "95", "horizon_days": 5, "conviction": 3,
        "benchmark": "XLK",
    }
    base.update(kw)
    return PitchIn.model_validate(base)


async def _submit(
    store: Store, now: datetime = EVENING, analyst: Analyst = "news", **kw: object
) -> Pitch:
    return await submit_pitch(
        store, analyst=analyst, pin=_pin(**kw), now=now, last=LAST,
        tradeable=TRADEABLE, rules=RULES, is_trading_day=weekday,
    )


async def test_an_evening_pitch_references_the_next_session(desk_store: Store) -> None:
    p = await _submit(desk_store)
    assert (p.session, p.analyst, p.target) == (date(2026, 9, 29), "news", Decimal("110"))


async def test_a_preopen_pitch_references_today(desk_store: Store) -> None:
    assert (await _submit(desk_store, now=PREOPEN)).session == date(2026, 9, 29)


def test_reference_session_skips_weekends() -> None:
    friday_evening = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)
    assert reference_session(friday_evening, weekday) == date(2026, 10, 5)


async def test_filing_during_the_session_is_refused(desk_store: Store) -> None:
    with pytest.raises(DeskRefused, match="outside the regular session"):
        await _submit(desk_store, now=MIDDAY)


@pytest.mark.parametrize("kw,match", [
    ({"target": "99"}, "target > last price > invalidation"),
    ({"direction": "down"}, "target < last price < invalidation"),
    ({"target": "140"}, "more than 30"),
    ({"horizon_days": 1}, "horizon_days must be 2-20"),
    ({"horizon_days": 21}, "horizon_days must be 2-20"),
    ({"benchmark": "QQQ"}, "benchmark must be one of"),
    ({"symbol": "ZZZ"}, "not in the qualified universe"),
])
async def test_bad_pitches_are_refused_with_a_reason(
    desk_store: Store, kw: dict[str, object], match: str,
) -> None:
    with pytest.raises(DeskRefused, match=match):
        await _submit(desk_store, **kw)


@pytest.mark.parametrize("raw", ["$48.25", "48,25", "NaN", "-1", "", "Infinity", "0"])
def test_prices_the_model_might_write_are_refused_with_a_reason(raw: str) -> None:
    with pytest.raises(DeskRefused, match="target must be"):
        parse_price(raw, "target")


def test_a_clean_price_string_parses_exactly() -> None:
    assert parse_price(" 48.25 ", "target") == Decimal("48.25")


async def test_the_evening_cap_is_five_per_analyst(desk_store: Store) -> None:
    for sym in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        await _submit(desk_store, symbol=sym)
    with pytest.raises(DeskRefused, match="limit \\(5\\)"):
        await _submit(desk_store, symbol="FFF")
    await _submit(desk_store, symbol="FFF", analyst="macro")  # another analyst's budget


async def test_the_preopen_cap_is_two_new(desk_store: Store) -> None:
    for sym in ("AAA", "BBB"):
        await _submit(desk_store, now=PREOPEN, symbol=sym)
    with pytest.raises(DeskRefused, match="limit \\(2\\)"):
        await _submit(desk_store, now=PREOPEN, symbol="CCC")


async def test_an_open_duplicate_is_refused_but_another_analyst_may_pitch_it(
    desk_store: Store,
) -> None:
    first = await _submit(desk_store)
    with pytest.raises(DeskRefused, match=f"open pitch {first.id}"):
        await _submit(desk_store)
    await _submit(desk_store, analyst="technical")


async def test_withdrawal_only_before_the_open_and_only_your_own(desk_store: Store) -> None:
    p = await _submit(desk_store)
    with pytest.raises(DeskRefused, match="no open pitch"):
        await withdraw_pitch(desk_store, analyst="macro", pitch_id=p.id, now=PREOPEN)
    after_open = datetime(2026, 9, 29, 13, 31, tzinfo=UTC)  # 09:31 ET
    with pytest.raises(DeskRefused, match="its session opened"):
        await withdraw_pitch(desk_store, analyst="news", pitch_id=p.id, now=after_open)
    await withdraw_pitch(desk_store, analyst="news", pitch_id=p.id, now=PREOPEN)
    assert await open_pitches(desk_store) == []
    # Withdrawn frees the symbol for a fresh pitch.
    await _submit(desk_store, now=PREOPEN)


async def test_open_pitches_excludes_resolved(desk_store: Store) -> None:
    p = await _submit(desk_store)
    await desk_store.execute(
        "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
        " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
        " VALUES ('pitch', ?, '2026-09-29', '100', '2026-09-30', 'target', '110', '10', '0', '0', 'x')",
        (p.id,),
    )
    assert await open_pitches(desk_store) == []
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_pitches.py`
Expected: FAIL (`No module named 'tc.desk.models'`).

- [ ] **Step 2: Implement `tc/desk/models.py`**

```python
"""Names every desk module shares, stated once."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

Analyst = Literal["technical", "earnings", "news", "macro"]
ANALYSTS: tuple[Analyst, ...] = get_args(Analyst)
Direction = Literal["up", "down"]
Funding = Literal["none", "shares", "call", "put"]
Instrument = Literal["shares", "call", "put"]

# Spec §5: a pitch's benchmark is SPY or one of the 11 sector SPDRs.
BENCHMARKS: tuple[str, ...] = (
    "SPY", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)

# Who is calling a desk tool is decided by the ENGINE from the running job,
# never supplied by the model (spec §5, `analyst`: "Set by the engine").
JOB_ANALYST: dict[str, Analyst] = {
    "analyst_technical": "technical",
    "analyst_earnings": "earnings",
    "analyst_news": "news",
    "analyst_macro": "macro",
    "preopen_news": "news",
    "preopen_earnings": "earnings",
}
PREOPEN_JOBS: frozenset[str] = frozenset({"preopen_news", "preopen_earnings"})
PM_JOBS: frozenset[str] = frozenset({"pm", "pm_midday"})


class DeskRefused(Exception):
    """A desk rule refused the model's input. The message is written for the
    model to read and act on; tc/mcp/tools_desk.py delivers it as a ToolError."""


@dataclass
class ActiveJob:
    """Which Claude job is running, set by the engine around every dispatch.

    The runner is one job at a time, so this is well-defined. A desk tool
    reached with no active job (someone poking the MCP mount by hand) is
    refused rather than attributed to anyone."""

    name: str | None = None


def utc_iso(dt: datetime) -> str:
    """One spelling for every stored timestamp, so text order is time order."""
    return dt.astimezone(UTC).isoformat()
```

- [ ] **Step 3: Implement `tc/desk/pitches.py`**

```python
"""Pitches: an analyst's dated, falsifiable prediction (spec §5).

A pitch is filed once and never edited. Its reference price is NOT here:
it is the opening print of its session, read from bars at scoring time
(tc/desk/scoring.py), so no price the model typed is ever the reference.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.models import BENCHMARKS, Analyst, Direction, DeskRefused, utc_iso
from tc.rules.model import Rules
from tc.store.db import Store

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    claim: str = Field(min_length=3, max_length=300)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class PitchIn(BaseModel):
    """What the model submits. Prices are strings; `parse_price` decides."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=10)
    direction: Direction
    thesis: str = Field(min_length=10, max_length=400)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=5)
    target: str
    invalidation: str
    horizon_days: int
    conviction: int = Field(ge=1, le=5)
    benchmark: str


class Pitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    analyst: Analyst
    filed_at: datetime
    session: date
    symbol: str
    direction: Direction
    thesis: str
    evidence: list[EvidenceItem]
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    conviction: int
    benchmark: str
    withdrawn: bool = False


def parse_price(value: str, field: str) -> Decimal:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise DeskRefused(
            f'{field} must be a plain decimal price string like "48.25", got {value!r}'
        ) from None
    if not d.is_finite() or d <= 0:
        raise DeskRefused(f"{field} must be a positive finite price, got {value!r}")
    return d


def check_levels(
    direction: Direction, last: Decimal, target: Decimal, invalidation: Decimal, rules: Rules
) -> None:
    if direction == "up" and not target > last > invalidation:
        raise DeskRefused(
            f"an up call needs target > last price > invalidation; the last price is {last}"
        )
    if direction == "down" and not target < last < invalidation:
        raise DeskRefused(
            f"a down call needs target < last price < invalidation; the last price is {last}"
        )
    max_dist = rules.get("strategy", "pitch_level_max_distance_pct")
    for name, level in (("target", target), ("invalidation", invalidation)):
        if abs(level - last) / last * 100 > max_dist:
            raise DeskRefused(
                f"{name} {level} is more than {max_dist}% from the last price {last}"
            )


def check_horizon(h: int, rules: Rules) -> None:
    lo = int(rules.get("strategy", "pitch_horizon_min_days"))
    hi = int(rules.get("strategy", "pitch_horizon_max_days"))
    if not lo <= h <= hi:
        raise DeskRefused(f"horizon_days must be {lo}-{hi} trading days, got {h}")


def check_benchmark(b: str) -> str:
    up = b.strip().upper()
    if up not in BENCHMARKS:
        raise DeskRefused(f"benchmark must be one of {', '.join(BENCHMARKS)}, got {b!r}")
    return up


def reference_session(filed_at: datetime, is_trading_day: Callable[[date], bool]) -> date:
    """The first regular session whose OPEN comes after the filing. A holiday
    this cannot see is healed at scoring time (the first SPY session on or
    after this date is used)."""
    et = filed_at.astimezone(ET)
    d = et.date()
    if et.time() < SESSION_OPEN and is_trading_day(d):
        return d
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def _run_window(filed_at: datetime) -> tuple[datetime, str]:
    """The run a pitch belongs to, for the per-run cap: evening runs start at
    16:00, pre-open runs are the pre-09:30 part of the day. Analysts do not
    run during the session, so a filing then is refused outright."""
    et = filed_at.astimezone(ET)
    if et.time() >= SESSION_CLOSE:
        return datetime.combine(et.date(), SESSION_CLOSE, tzinfo=ET), "evening"
    if et.time() < SESSION_OPEN:
        return datetime.combine(et.date(), time(0, 0), tzinfo=ET), "preopen"
    raise DeskRefused(
        "analysts file pitches outside the regular session (before 09:30 or after 16:00 ET)"
    )


async def tradeable_symbols(store: Store, desk: DeskConfig) -> set[str]:
    rows = await store.universe_rows(qualified_only=True)
    return {str(r["symbol"]) for r in rows} | set(desk.etf_list)


async def last_close(store: Store, symbol: str) -> Decimal | None:
    bars = await store.bars_for(symbol, limit=1)
    return bars[-1].close if bars else None


def _pitch(r: sqlite3.Row, withdrawn: bool) -> Pitch:
    return Pitch(
        id=r["id"], analyst=r["analyst"], filed_at=datetime.fromisoformat(r["filed_at"]),
        session=date.fromisoformat(r["session"]), symbol=r["symbol"],
        direction=r["direction"], thesis=r["thesis"],
        evidence=[EvidenceItem.model_validate(e) for e in json.loads(r["evidence_json"])],
        target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
        horizon_days=r["horizon_days"], conviction=r["conviction"],
        benchmark=r["benchmark"], withdrawn=withdrawn,
    )


async def get_pitch(store: Store, pitch_id: int) -> Pitch | None:
    row = await store.fetchone(
        "SELECT p.*, (w.pitch_id IS NOT NULL) AS withdrawn FROM pitches p"
        " LEFT JOIN pitch_withdrawals w ON w.pitch_id = p.id WHERE p.id=?",
        (pitch_id,),
    )
    return None if row is None else _pitch(row, bool(row["withdrawn"]))


async def open_pitches(store: Store) -> list[Pitch]:
    rows = await store.fetchall(
        "SELECT * FROM pitches WHERE id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch') ORDER BY id"
    )
    return [_pitch(r, False) for r in rows]


async def submit_pitch(
    store: Store,
    *,
    analyst: Analyst,
    pin: PitchIn,
    now: datetime,
    last: Decimal,
    tradeable: set[str],
    rules: Rules,
    is_trading_day: Callable[[date], bool],
) -> Pitch:
    symbol = pin.symbol.strip().upper()
    if symbol not in tradeable:
        raise DeskRefused(f"{symbol} is not in the qualified universe or on the ETF list")
    benchmark = check_benchmark(pin.benchmark)
    check_horizon(pin.horizon_days, rules)
    target = parse_price(pin.target, "target")
    invalidation = parse_price(pin.invalidation, "invalidation")
    check_levels(pin.direction, last, target, invalidation, rules)
    start, mode = _run_window(now)
    key = "desk_max_pitches_per_analyst_run" if mode == "evening" else "desk_preopen_max_new_pitches"
    cap = int(rules.get("strategy", key))
    filed = await store.fetchone(
        "SELECT COUNT(*) AS n FROM pitches WHERE analyst=? AND filed_at >= ?",
        (analyst, utc_iso(start)),
    )
    if filed is not None and int(filed["n"]) >= cap:
        raise DeskRefused(f"this run's pitch limit ({cap}) is spent; rank harder rather than file more")
    dup = await store.fetchone(
        "SELECT id FROM pitches WHERE analyst=? AND symbol=? AND direction=?"
        " AND id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')",
        (analyst, symbol, pin.direction),
    )
    if dup is not None:
        raise DeskRefused(
            f"you already have open pitch {dup['id']} on {symbol} {pin.direction};"
            " it stays open until it resolves"
        )
    session = reference_session(now, is_trading_day)
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
            " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                analyst, utc_iso(now), session.isoformat(), symbol, pin.direction,
                pin.thesis, json.dumps([e.model_dump() for e in pin.evidence]),
                str(target), str(invalidation), pin.horizon_days, pin.conviction, benchmark,
            ),
        )
        pid = int(cur.lastrowid or 0)
    return Pitch(
        id=pid, analyst=analyst, filed_at=now.astimezone(UTC), session=session, symbol=symbol,
        direction=pin.direction, thesis=pin.thesis, evidence=pin.evidence, target=target,
        invalidation=invalidation, horizon_days=pin.horizon_days, conviction=pin.conviction,
        benchmark=benchmark,
    )


async def withdraw_pitch(store: Store, *, analyst: Analyst, pitch_id: int, now: datetime) -> None:
    p = await get_pitch(store, pitch_id)
    if p is None or p.analyst != analyst or p.withdrawn:
        raise DeskRefused(f"there is no open pitch {pitch_id} of yours to withdraw")
    opens = datetime.combine(p.session, SESSION_OPEN, tzinfo=ET)
    if now >= opens:
        raise DeskRefused(
            f"pitch {pitch_id} has a reference price already (its session opened); it can only resolve"
        )
    await store.execute(
        "INSERT INTO pitch_withdrawals(pitch_id, at) VALUES (?,?)", (pitch_id, utc_iso(now))
    )
```

- [ ] **Step 4: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_pitches.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/models.py engine/tc/desk/pitches.py tests/engine/unit/test_desk_pitches.py
git commit -m "desk: pitches -- filed once, refused with a reason, withdrawable only before their open"
```

---

### Task 6: Scoring — resolve every pitch and call from bars

**Files:**
- Create: `engine/tc/desk/scoring.py`
- Modify: `engine/tc/store/db.py` (public `now_iso()`)
- Modify: `engine/tc/main.py` (`_job_bars_refresh`: score after bars)
- Test: `tests/engine/unit/test_desk_scoring.py`

**Interfaces:**
- Consumes:
  - `Store.bars_for` (Task 1).
  - The `pitches`, `calls`, `pitch_withdrawals` and `resolutions` tables.
- Produces:
  - `How` (a Literal).
  - `Resolution`, a model with properties `excess_spy`, `excess_bench` and `hit`.
  - `Item`, a frozen dataclass.
  - `resolve(item, bars, spy, bench) -> Resolution | None`
  - `missing_bar(item, bars, spy) -> bool`
  - `ScoreReport(resolved: list[Resolution], stuck: list[str])`
  - `load_open_items(store) -> list[Item]`
  - `score(store) -> ScoreReport`
  - `insert_resolution(store, r) -> bool`
  - `resolution_for(store, kind, item_id) -> Resolution | None`
  - `ResolvedRow(resolution, analyst, origin, conviction)`
  - `resolved_rows(store) -> list[ResolvedRow]`
  - `RecordRow`
  - `analyst_record(store, analyst, limit=10) -> list[RecordRow]`

**Rules this task implements (spec §7.1–§7.2):**

- **Reference:**
  - A pitch's reference is the opening print of the first SPY session on or
    after its stamped session.
  - A call's reference is its stamped quote (`ref_price`, `spy_ref`,
    `bench_ref`).
- **Resolution** happens at the first of: target touched (resolves at the
  target), invalidation touched (at the invalidation), or the close of
  session N. Both levels touched in the same bar counts as an invalidation.
- **Gaps:**
  - From session 2 on, an open beyond a level resolves at that open
    (`gap_target` / `gap_invalidation`).
  - A pitch whose own opening print is already beyond a level resolves
    `open_through` at that open, with a 0% return.
- **A call's first session** is not checked for touches: its bar partly
  predates the 09:50 reference (plan deviation 5).
- **Returns:**
  - `ret_pct` is signed by the called direction.
  - `spy_ret_pct` is SPY's plain move over the same window. Holding SPY is
    the opportunity cost for either direction.
  - `bench_ret_pct` is the benchmark's move signed by the called direction.
- **Timing:** a level resolution uses that session's close for SPY and the
  benchmark; an at-open resolution uses their opens.
- **Missing bars:** the item stays open, and it is reported as stuck once a
  session with a missing bar is 3 or more SPY sessions old.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_scoring.py`:

```python
"""Task 6: resolution arithmetic and the scoring pass (spec §7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from desk_fixtures import bar, flat_bars, sessions

from tc.desk.scoring import Item, analyst_record, missing_bar, resolve, score
from tc.store.db import Store

S = sessions(date(2026, 9, 28), 12)   # Mon 9/28 onward
SPY = [bar(d, 500, 500, 500, 500) for d in S]
_BASE = Item(kind="pitch", item_id=1, symbol="AAA", direction="up", target=Decimal(110),
             invalidation=Decimal(95), horizon_days=5, benchmark="XLK", session=S[0])


def _pitch_item(**kw: Any) -> Item:
    return replace(_BASE, **kw)


def test_a_pitch_hits_its_target_at_the_target_price() -> None:
    bars = [bar(S[0], 100, 104, 99, 103), bar(S[1], 103, 111, 102, 108)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None
    assert (r.how, r.resolved_on, r.exit_price, r.ret_pct) == ("target", S[1], 110, 10)
    assert r.hit and r.excess_spy == 10


def test_a_same_day_double_touch_is_an_invalidation() -> None:
    bars = [bar(S[0], 100, 111, 94, 100)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.exit_price) == ("invalidation", 95)
    assert r.ret_pct == -5 and not r.hit


def test_a_gap_through_the_target_resolves_at_the_open() -> None:
    bars = [bar(S[0], 100, 104, 99, 103), bar(S[1], 115, 116, 112, 113)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.exit_price) == ("gap_target", 115)


def test_a_pitch_whose_open_is_already_through_a_level_resolves_at_zero() -> None:
    bars = [bar(S[0], 112, 113, 111, 112)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.ret_pct, r.spy_ret_pct) == ("open_through", 0, 0)


def test_the_horizon_resolves_at_that_close() -> None:
    bars = [bar(d, 100, 101, 99, 100) for d in S[:4]] + [bar(S[4], 100, 102, 99, 102)]
    r = resolve(_pitch_item(), bars, SPY, SPY)
    assert r is not None and (r.how, r.resolved_on, r.exit_price) == ("horizon", S[4], 102)


def test_a_down_call_is_signed_and_its_benchmark_too() -> None:
    item = _pitch_item(direction="down", target=Decimal(90), invalidation=Decimal(105))
    bars = [bar(S[0], 100, 101, 95, 96), bar(S[1], 96, 97, 89, 91)]
    bench = [bar(S[0], 50, 50, 50, 50), bar(S[1], 50, 51, 49, 51)]
    spy = [bar(S[0], 500, 500, 500, 500), bar(S[1], 500, 510, 500, 505)]
    r = resolve(item, bars, spy, bench)
    assert r is not None and r.how == "target"
    assert r.ret_pct == 10                      # 100 -> 90, called down
    assert r.spy_ret_pct == 1                   # SPY 500 -> 505, unsigned
    assert r.bench_ret_pct == -2                # bench 50 -> 51, signed down
    assert r.excess_spy == 9 and r.excess_bench == 12


def test_a_calls_first_session_touch_is_not_counted() -> None:
    item = _pitch_item(kind="call", ref_price=Decimal(100), spy_ref=Decimal(500),
                       bench_ref=Decimal(500), horizon_days=2)
    bars = [bar(S[0], 99, 115, 90, 100), bar(S[1], 100, 101, 99, 100)]
    r = resolve(item, bars, SPY, SPY)
    assert r is not None and r.how == "horizon" and r.ret_pct == 0


def test_a_pitch_stamped_on_a_holiday_references_the_next_session() -> None:
    # 9/28 is a "holiday": no SPY bar, no symbol bar. The pitch was stamped 9/28.
    spy = SPY[1:]
    bars = [bar(S[1], 100, 111, 99, 108)]
    r = resolve(_pitch_item(), bars, spy, spy)
    assert r is not None and (r.ref_date, r.how) == (S[1], "target")


def test_missing_bars_keep_an_item_open_and_are_named_after_3_sessions() -> None:
    bars = [bar(S[0], 100, 101, 99, 100)]           # nothing after the first session
    assert resolve(_pitch_item(), bars, SPY[:3], SPY[:3]) is None
    assert missing_bar(_pitch_item(), bars, SPY[:3]) is False   # S[1] is only 1 session old
    assert missing_bar(_pitch_item(), bars, SPY[:5]) is True    # S[1] now 3 sessions old


async def test_score_writes_each_resolution_once(desk_store: Store) -> None:
    await desk_store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
        " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
        " VALUES ('technical','2026-09-25T20:45:00+00:00',?,'AAA','up','t','[]','110','95',5,3,'XLK')",
        (S[0].isoformat(),),
    )
    await desk_store.upsert_bars("SPY", SPY)
    await desk_store.upsert_bars("XLK", flat_bars(S[0], 12, "50"))
    await desk_store.upsert_bars("AAA", [bar(S[0], 100, 104, 99, 103), bar(S[1], 103, 111, 102, 108)])
    first = await score(desk_store)
    second = await score(desk_store)
    assert len(first.resolved) == 1 and second.resolved == []
    rows = await desk_store.fetchall("SELECT how FROM resolutions")
    assert [r["how"] for r in rows] == ["target"]
    rec = await analyst_record(desk_store, "technical")
    assert [(x.symbol, x.how, x.ret_pct) for x in rec] == [("AAA", "target", "10.00")]
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_scoring.py`
Expected: FAIL (`No module named 'tc.desk.scoring'`).

- [ ] **Step 2: Implement `tc/desk/scoring.py`**

```python
"""Scoring (spec §7): every pitch and every call resolves from stored bars,
once, into `resolutions`. Engine code only; append-only; recomputable from
bars at any time by running `resolve` over the same inputs."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.broker.models import DailyBar
from tc.desk.models import Analyst, Direction
from tc.store.db import Store, now_iso

How = Literal["target", "invalidation", "horizon", "gap_target", "gap_invalidation", "open_through"]
HUNDRED = Decimal(100)
STUCK_AFTER_SESSIONS = 3
CENT = Decimal("0.01")


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pitch", "call"]
    item_id: int
    ref_date: date
    ref_price: Decimal
    resolved_on: date
    how: How
    exit_price: Decimal
    ret_pct: Decimal
    spy_ret_pct: Decimal
    bench_ret_pct: Decimal

    @property
    def excess_spy(self) -> Decimal:
        return self.ret_pct - self.spy_ret_pct

    @property
    def excess_bench(self) -> Decimal:
        return self.ret_pct - self.bench_ret_pct

    @property
    def hit(self) -> bool:
        return self.how in ("target", "gap_target")


@dataclass(frozen=True)
class Item:
    kind: Literal["pitch", "call"]
    item_id: int
    symbol: str
    direction: Direction
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    benchmark: str
    session: date
    # None for a pitch: its reference is its session's opening print.
    ref_price: Decimal | None = None
    spy_ref: Decimal | None = None
    bench_ref: Decimal | None = None


def _move(frm: Decimal, to: Decimal) -> Decimal:
    return (to - frm) / frm * HUNDRED


def resolve(
    item: Item, bars: Sequence[DailyBar], spy: Sequence[DailyBar], bench: Sequence[DailyBar]
) -> Resolution | None:
    by = {b.date: b for b in bars}
    spy_by = {b.date: b for b in spy}
    bench_by = {b.date: b for b in bench}
    days = [b.date for b in spy if b.date >= item.session]   # market sessions
    if not days:
        return None
    start = days[0]
    first = by.get(start)
    if first is None or start not in bench_by:
        return None
    if item.ref_price is None:
        ref, spy_ref, bench_ref = first.open, spy_by[start].open, bench_by[start].open
    else:
        assert item.spy_ref is not None and item.bench_ref is not None
        ref, spy_ref, bench_ref = item.ref_price, item.spy_ref, item.bench_ref
    up = item.direction == "up"

    def through_target(p: Decimal) -> bool:
        return p >= item.target if up else p <= item.target

    def through_inval(p: Decimal) -> bool:
        return p <= item.invalidation if up else p >= item.invalidation

    def done(day: date, how: How, exit_price: Decimal, at_open: bool) -> Resolution | None:
        s, bb = spy_by.get(day), bench_by.get(day)
        if s is None or bb is None:
            return None
        spy_exit = s.open if at_open else s.close
        bench_exit = bb.open if at_open else bb.close
        ret = _move(ref, exit_price)
        bench_move = _move(bench_ref, bench_exit)
        return Resolution(
            kind=item.kind, item_id=item.item_id, ref_date=start, ref_price=ref,
            resolved_on=day, how=how, exit_price=exit_price,
            ret_pct=ret if up else -ret,
            spy_ret_pct=_move(spy_ref, spy_exit),
            bench_ret_pct=bench_move if up else -bench_move,
        )

    for k, day in enumerate(days[: item.horizon_days], start=1):
        b = by.get(day)
        if b is None:
            return None
        if k == 1 and item.ref_price is None:
            if through_target(b.open) or through_inval(b.open):
                return done(day, "open_through", b.open, at_open=True)
        if k > 1:
            if through_target(b.open):
                return done(day, "gap_target", b.open, at_open=True)
            if through_inval(b.open):
                return done(day, "gap_invalidation", b.open, at_open=True)
        if k > 1 or item.ref_price is None:
            inval_hit = b.low <= item.invalidation if up else b.high >= item.invalidation
            target_hit = b.high >= item.target if up else b.low <= item.target
            if inval_hit:
                return done(day, "invalidation", item.invalidation, at_open=False)
            if target_hit:
                return done(day, "target", item.target, at_open=False)
        if k == item.horizon_days:
            return done(day, "horizon", b.close, at_open=False)
    return None


def missing_bar(item: Item, bars: Sequence[DailyBar], spy: Sequence[DailyBar]) -> bool:
    """True when a session inside the item's window has no bar for the symbol
    and is at least STUCK_AFTER_SESSIONS SPY sessions old: a halt, a delisting,
    or a symbol that fell out of the bars set -- a human should look."""
    have = {b.date for b in bars}
    days = [b.date for b in spy if b.date >= item.session][: item.horizon_days]
    all_days = [b.date for b in spy]
    for d in days:
        if d not in have and len(all_days) - 1 - all_days.index(d) >= STUCK_AFTER_SESSIONS:
            return True
    return False


@dataclass(frozen=True)
class ScoreReport:
    resolved: list[Resolution] = field(default_factory=list)
    stuck: list[str] = field(default_factory=list)


async def load_open_items(store: Store) -> list[Item]:
    items: list[Item] = []
    for r in await store.fetchall(
        "SELECT * FROM pitches WHERE id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')"
    ):
        items.append(Item(
            kind="pitch", item_id=r["id"], symbol=r["symbol"], direction=r["direction"],
            target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
            horizon_days=r["horizon_days"], benchmark=r["benchmark"],
            session=date.fromisoformat(r["session"]),
        ))
    for r in await store.fetchall(
        "SELECT * FROM calls WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
    ):
        items.append(Item(
            kind="call", item_id=r["id"], symbol=r["symbol"], direction=r["direction"],
            target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
            horizon_days=r["horizon_days"], benchmark=r["benchmark"],
            session=date.fromisoformat(r["session"]), ref_price=Decimal(r["ref_price"]),
            spy_ref=Decimal(r["spy_ref"]), bench_ref=Decimal(r["bench_ref"]),
        ))
    return items


async def insert_resolution(store: Store, r: Resolution) -> bool:
    """True when this pass wrote it; False when it already existed."""
    before = await store.fetchone(
        "SELECT 1 FROM resolutions WHERE kind=? AND item_id=?", (r.kind, r.item_id)
    )
    if before is not None:
        return False
    await store.execute(
        "INSERT OR IGNORE INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on,"
        " how, exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (r.kind, r.item_id, r.ref_date.isoformat(), str(r.ref_price), r.resolved_on.isoformat(),
         r.how, str(r.exit_price), str(r.ret_pct), str(r.spy_ret_pct), str(r.bench_ret_pct),
         now_iso()),
    )
    return True


def _resolution(row: sqlite3.Row) -> Resolution:
    return Resolution(
        kind=row["kind"], item_id=row["item_id"], ref_date=date.fromisoformat(row["ref_date"]),
        ref_price=Decimal(row["ref_price"]), resolved_on=date.fromisoformat(row["resolved_on"]),
        how=row["how"], exit_price=Decimal(row["exit_price"]), ret_pct=Decimal(row["ret_pct"]),
        spy_ret_pct=Decimal(row["spy_ret_pct"]), bench_ret_pct=Decimal(row["bench_ret_pct"]),
    )


async def resolution_for(store: Store, kind: str, item_id: int) -> Resolution | None:
    row = await store.fetchone(
        "SELECT * FROM resolutions WHERE kind=? AND item_id=?", (kind, item_id)
    )
    return None if row is None else _resolution(row)


async def score(store: Store) -> ScoreReport:
    items = await load_open_items(store)
    if not items:
        return ScoreReport()
    earliest = min(i.session for i in items)
    cache: dict[str, list[DailyBar]] = {}

    async def series(symbol: str) -> list[DailyBar]:
        if symbol not in cache:
            cache[symbol] = await store.bars_for(symbol, since=earliest)
        return cache[symbol]

    spy = await series("SPY")
    resolved: list[Resolution] = []
    stuck: list[str] = []
    for item in items:
        bars = await series(item.symbol)
        r = resolve(item, bars, spy, await series(item.benchmark))
        if r is None:
            if missing_bar(item, bars, spy):
                stuck.append(f"{item.kind}#{item.item_id} {item.symbol}")
            continue
        if await insert_resolution(store, r):
            resolved.append(r)
    return ScoreReport(resolved=resolved, stuck=stuck)


@dataclass(frozen=True)
class ResolvedRow:
    resolution: Resolution
    analyst: Analyst | None     # pitches
    origin: str | None          # calls: 'pitch' | 'pm' | 'legacy'
    conviction: int


async def resolved_rows(store: Store) -> list[ResolvedRow]:
    rows = await store.fetchall(
        "SELECT r.*, p.analyst AS analyst, c.origin AS origin,"
        " COALESCE(p.conviction, c.conviction) AS conv FROM resolutions r"
        " LEFT JOIN pitches p ON r.kind='pitch' AND p.id=r.item_id"
        " LEFT JOIN calls c ON r.kind='call' AND c.id=r.item_id ORDER BY r.id"
    )
    return [
        ResolvedRow(resolution=_resolution(r), analyst=r["analyst"], origin=r["origin"],
                    conviction=int(r["conv"]))
        for r in rows
    ]


class RecordRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    resolved_on: str
    how: str
    ret_pct: str
    excess_spy_pct: str


async def analyst_record(store: Store, analyst: Analyst, limit: int = 10) -> list[RecordRow]:
    rows = await store.fetchall(
        "SELECT r.*, p.symbol AS symbol, p.direction AS direction FROM resolutions r"
        " JOIN pitches p ON r.kind='pitch' AND p.id=r.item_id WHERE p.analyst=?"
        " ORDER BY r.resolved_on DESC, r.id DESC LIMIT ?",
        (analyst, limit),
    )
    out: list[RecordRow] = []
    for row in rows:
        res = _resolution(row)
        out.append(RecordRow(
            symbol=row["symbol"], direction=row["direction"],
            resolved_on=res.resolved_on.isoformat(), how=res.how,
            ret_pct=str(res.ret_pct.quantize(CENT)),
            excess_spy_pct=str(res.excess_spy.quantize(CENT)),
        ))
    return out
```

In `engine/tc/store/db.py`, add a public name for the stored-timestamp
helper, right after `def _now()`. The desk modules import this rather than
the underscore name:

```python
def now_iso() -> str:
    """The UTC ISO timestamp every ledger row is written with."""
    return _now()
```

- [ ] **Step 3: Score after bars in `bars_refresh`**

In `engine/tc/main.py`, add `from tc.desk.scoring import score`, then extend
`_job_bars_refresh`. Replace its final `return "done", detail` with:

```python
        rep_score = await score(self._store)
        detail["resolved"] = len(rep_score.resolved)
        detail["stuck"] = rep_score.stuck[:10]
        if rep_score.stuck:
            await self.notifier.post(
                f"⚠️ desk: {len(rep_score.stuck)} item(s) missing bars 3+ sessions — "
                + ", ".join(rep_score.stuck[:10])
            )
        return "done", detail
```

- [ ] **Step 4: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/scoring.py engine/tc/store/db.py engine/tc/main.py tests/engine/unit/test_desk_scoring.py
git commit -m "desk: score every pitch and call from bars, once, and name the ones that cannot be"
```

---

### Task 7: Briefings

**Files:**
- Create: `engine/tc/desk/briefing.py`
- Test: `tests/engine/unit/test_desk_briefing.py`

**Interfaces:**
- Consumes:
  - `indicators` (Task 3).
  - `Store.bars_for`, `Store.bar_counts` (Task 1).
  - `open_pitches` (Task 5), `analyst_record` (Task 6).
  - `Broker.movers`.
- Produces:
  - Models: `BriefRow`, `MoverRow`, `OpenPitchRow`, `Briefing`
  - `row_for(symbol, bars, spy, screen, etfs) -> BriefRow`
  - `technical_screen(series, spy, skip) -> list[tuple[str, str]]`
  - `earnings_screen(series, skip) -> list[tuple[str, str]]`
  - `gap_screen(series, skip) -> list[tuple[str, str]]`
  - `build_briefing(store, broker, analyst, desk) -> Briefing`

**Screens (spec §4.1):**

- **technical**
  - Needs ≥ 200 bars; ETFs are excluded (they belong to macro).
  - Breakout: close above the prior 52-week high on volume ≥ 1.5×.
  - Breakdown: close below the prior 52-week low on volume ≥ 1.5×.
  - Volume spike: ≥ 2.5×.
  - Pullback: close > SMA200 and SMA50 > SMA200; within 2% of SMA20 or
    SMA50; 5-session change < 0.
  - Also the top 10 and bottom 10 by 6-month relative strength.
  - Capped at 40 rows.
- **earnings:** a gap of ≥ 3% on ≥ 2× volume in any of the last 3
  sessions. Capped at 30.
- **news:** Schwab movers (EQUITY_ALL, 10 up and 10 down) plus a gap list
  (last-session gap of ≥ 2%, top 20).
- **macro:** every ETF on the list, sorted by 3-month RS, plus context rows
  for SPY and `context_symbols`.

Every briefing also carries the analyst's last 10 resolved pitches and its
own open pitches.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_briefing.py`:

```python
"""Task 7: briefings are computed, not researched (spec §4.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from desk_fixtures import bar, sessions

from tc.broker.fake import FakeBroker
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk.briefing import build_briefing, earnings_screen, technical_screen
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)
DAYS = sessions(date(2025, 9, 1), 230)


def _flat(v: int = 1000) -> list[DailyBar]:
    return [bar(d, 100, 101, 99, 100, v) for d in DAYS[:-1]]


def _with_last(o: float, h: float, lo: float, c: float, v: int) -> list[DailyBar]:
    return _flat() + [bar(DAYS[-1], o, h, lo, c, v)]


def test_breakouts_and_breakdowns_need_volume_and_etfs_are_macros() -> None:
    series = {
        "UPP": _with_last(100, 111, 100, 110, 3000),
        "DWN": _with_last(100, 100, 89, 90, 3000),
        "QUIET": _with_last(100, 111, 100, 110, 1000),   # breakout without volume
        "XLK": _with_last(100, 111, 100, 110, 3000),     # an ETF: macro's, not technical's
        "SHORT": _with_last(100, 111, 100, 110, 3000)[-50:],
    }
    spy = _flat()
    picks = dict(technical_screen(series, spy, {"XLK"}))
    assert picks["UPP"] == "breakout" and picks["DWN"] == "breakdown"
    assert "XLK" not in picks and "SHORT" not in picks
    assert picks.get("QUIET") != "breakout"


def test_an_earnings_gap_two_sessions_back_is_found() -> None:
    bars = [bar(d, 100, 101, 99, 100, 1000) for d in DAYS[:-2]]
    bars += [bar(DAYS[-2], 106, 107, 105, 106, 5000), bar(DAYS[-1], 106, 107, 105, 106, 1000)]
    assert earnings_screen({"ERN": bars}, set()) == [("ERN", "earnings_gap_1d_ago")]


async def _seed(store: Store) -> None:
    await store.upsert_bars("SPY", _flat())
    await store.upsert_bars("UPP", _with_last(100, 111, 100, 110, 3000))
    await store.upsert_bars("XLK", _with_last(100, 101, 99, 101, 1000))
    await store.upsert_bars("UUP", _flat())


async def test_the_technical_briefing_carries_rows_and_asof(desk_store: Store) -> None:
    await _seed(desk_store)
    b = await build_briefing(desk_store, FakeBroker(FIX, NOW), "technical",
                             DeskConfig(etf_list=["XLK"]))
    assert b.asof == DAYS[-2].isoformat()
    assert [r.symbol for r in b.rows] == ["UPP"] and b.rows[0].screen == "breakout"
    assert b.rows[0].option_band is False     # 110 > $100 and not an ETF


async def test_the_news_briefing_reads_movers_and_survives_their_failure(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _seed(desk_store)
    ok = await build_briefing(desk_store, FakeBroker(FIX, NOW), "news", DeskConfig())
    assert ok.movers and ok.movers_error is None
    broken = await build_briefing(desk_store, FakeBroker(tmp_path, NOW), "news", DeskConfig())
    assert broken.movers == [] and broken.movers_error is not None


async def test_the_macro_briefing_is_the_etf_list_plus_context(desk_store: Store) -> None:
    await _seed(desk_store)
    b = await build_briefing(desk_store, FakeBroker(FIX, NOW), "macro",
                             DeskConfig(etf_list=["XLK"], context_symbols=["UUP"]))
    assert [r.symbol for r in b.rows] == ["XLK"]
    assert [r.symbol for r in b.context] == ["SPY", "UUP"]
```

`test_the_technical_briefing_carries_rows_and_asof` compares
`asof == DAYS[-2]`, because `_seed` stores SPY's flat series, which ends one
session before `DAYS[-1]`.

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_briefing.py`
Expected: FAIL (`No module named 'tc.desk.briefing'`).

- [ ] **Step 2: Implement `tc/desk/briefing.py`**

```python
"""Briefings (spec §4.1): the numbers each analyst starts from, computed by
engine code from stored bars and one movers read. The model adds judgement
-- why a move matters, what comes next, where it is wrong -- not measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk import indicators as ind
from tc.desk.models import Analyst
from tc.desk.pitches import open_pitches
from tc.desk.scoring import RecordRow, analyst_record
from tc.store.db import Store

SCREEN_ORDER = ("breakout", "breakdown", "volume_spike", "pullback", "rs_top", "rs_bottom")
MIN_HISTORY = 200
MAX_TECH_ROWS = 40
MAX_EARN_ROWS = 30
MAX_GAP_ROWS = 20
MOVERS_PER_SIDE = 10
RS_MIN_UNIVERSE = 30
RS_PICKS = 10
OPTION_BAND_MAX = Decimal(100)
HISTORY = 260
CENT = Decimal("0.01")


class BriefRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    screen: str
    last: str
    chg_1d_pct: str | None
    atr_pct: str | None
    vs_sma20_pct: str | None
    vs_sma50_pct: str | None
    vs_sma200_pct: str | None
    rs_3m: str | None
    rs_6m: str | None
    from_52w_high_pct: str | None
    from_52w_low_pct: str | None
    vol_ratio: str | None
    gap_pct: str | None
    option_band: bool


class MoverRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    last: str
    net_percent_change: str
    volume: int


class OpenPitchRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    symbol: str
    direction: str
    session: str
    target: str
    invalidation: str
    horizon_days: int
    conviction: int


class Briefing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyst: Analyst
    asof: str | None
    rows: list[BriefRow]
    context: list[BriefRow]
    movers: list[MoverRow]
    movers_error: str | None
    record: list[RecordRow]
    open_pitches: list[OpenPitchRow]
    notes: list[str]


def _q(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(CENT))


def row_for(
    symbol: str, bars: Sequence[DailyBar], spy: Sequence[DailyBar], screen: str,
    etfs: set[str],
) -> BriefRow:
    last = bars[-1].close
    hl = ind.high_low(bars)
    return BriefRow(
        symbol=symbol, screen=screen, last=str(last),
        chg_1d_pct=_q(ind.pct_change(bars, 1)), atr_pct=_q(ind.atr_pct(bars)),
        vs_sma20_pct=_q(ind.pct_from(last, ind.sma(bars, 20))),
        vs_sma50_pct=_q(ind.pct_from(last, ind.sma(bars, 50))),
        vs_sma200_pct=_q(ind.pct_from(last, ind.sma(bars, 200))),
        rs_3m=_q(ind.rel_strength(bars, spy, 63)), rs_6m=_q(ind.rel_strength(bars, spy, 126)),
        from_52w_high_pct=_q(ind.pct_from(last, hl[0])) if hl else None,
        from_52w_low_pct=_q(ind.pct_from(last, hl[1])) if hl else None,
        vol_ratio=_q(ind.volume_ratio(bars)), gap_pct=_q(ind.gap_pct(bars)),
        option_band=symbol in etfs or last <= OPTION_BAND_MAX,
    )


def technical_screen(
    series: Mapping[str, Sequence[DailyBar]], spy: Sequence[DailyBar], skip: set[str]
) -> list[tuple[str, str]]:
    """`skip` is every symbol that is not a single name: the ETF list, SPY and
    the macro context rows. Relative-strength extremes are only picked from a
    universe big enough for "top decile" to mean something."""
    picks: dict[str, str] = {}
    rs: list[tuple[Decimal, str]] = []
    for sym, bars in series.items():
        if sym in skip or len(bars) < MIN_HISTORY:
            continue
        last = bars[-1].close
        r6 = ind.rel_strength(bars, spy, 126)
        if r6 is not None:
            rs.append((r6, sym))
        prior, vr = ind.prior_high_low(bars), ind.volume_ratio(bars)
        if prior is not None and vr is not None and vr >= Decimal("1.5"):
            if last > prior[0]:
                picks[sym] = "breakout"
                continue
            if last < prior[1]:
                picks[sym] = "breakdown"
                continue
        if vr is not None and vr >= Decimal("2.5"):
            picks[sym] = "volume_spike"
            continue
        s20, s50, s200 = ind.sma(bars, 20), ind.sma(bars, 50), ind.sma(bars, 200)
        ch5 = ind.pct_change(bars, 5)
        if (
            s20 and s50 and s200 and ch5 is not None and last > s200 and s50 > s200
            and ch5 < 0
            and (abs(last - s20) / s20 <= Decimal("0.02") or abs(last - s50) / s50 <= Decimal("0.02"))
        ):
            picks[sym] = "pullback"
    if len(rs) >= RS_MIN_UNIVERSE:
        rs.sort()
        n = min(RS_PICKS, len(rs) // 10)
        for _, sym in reversed(rs[-n:]):
            picks.setdefault(sym, "rs_top")
        for _, sym in rs[:n]:
            picks.setdefault(sym, "rs_bottom")
    ordered = sorted(picks.items(), key=lambda kv: (SCREEN_ORDER.index(kv[1]), kv[0]))
    return ordered[:MAX_TECH_ROWS]


def earnings_screen(
    series: Mapping[str, Sequence[DailyBar]], skip: set[str]
) -> list[tuple[str, str]]:
    found: list[tuple[Decimal, str, str]] = []
    for sym, bars in series.items():
        if sym in skip or len(bars) < 25:
            continue
        best: tuple[Decimal, int] | None = None
        for back in range(3):
            window = bars[: len(bars) - back]
            g, vr = ind.gap_pct(window), ind.volume_ratio(window)
            if g is not None and vr is not None and abs(g) >= 3 and vr >= 2:
                if best is None or abs(g) > abs(best[0]):
                    best = (g, back)
        if best is not None:
            found.append((abs(best[0]), sym, f"earnings_gap_{best[1]}d_ago"))
    found.sort(key=lambda t: (-t[0], t[1]))
    return [(sym, screen) for _, sym, screen in found[:MAX_EARN_ROWS]]


def gap_screen(series: Mapping[str, Sequence[DailyBar]], skip: set[str]) -> list[tuple[str, str]]:
    found: list[tuple[Decimal, str]] = []
    for sym, bars in series.items():
        if sym in skip:
            continue
        g = ind.gap_pct(bars)
        if g is not None and abs(g) >= 2:
            found.append((abs(g), sym))
    found.sort(key=lambda t: (-t[0], t[1]))
    return [(sym, "gap") for _, sym in found[:MAX_GAP_ROWS]]


async def build_briefing(
    store: Store, broker: Broker, analyst: Analyst, desk: DeskConfig
) -> Briefing:
    counts = await store.bar_counts()
    series = {s: await store.bars_for(s, limit=HISTORY) for s in counts}
    spy = series.get("SPY", [])
    etfs = set(desk.etf_list)
    skip = etfs | {"SPY"} | set(desk.context_symbols)
    notes: list[str] = []
    if not spy:
        notes.append("no SPY bars yet: relative strength is unknown until bars_refresh has run")
    rows: list[BriefRow] = []
    context: list[BriefRow] = []
    movers: list[MoverRow] = []
    movers_error: str | None = None
    if analyst == "technical":
        rows = [row_for(s, series[s], spy, scr, etfs) for s, scr in technical_screen(series, spy, skip)]
    elif analyst == "earnings":
        rows = [row_for(s, series[s], spy, scr, etfs) for s, scr in earnings_screen(series, skip)]
    elif analyst == "news":
        rows = [row_for(s, series[s], spy, scr, etfs) for s, scr in gap_screen(series, skip)]
        try:
            for direction in ("up", "down"):
                got = await broker.movers("EQUITY_ALL", direction)
                movers.extend(
                    MoverRow(symbol=m.symbol, direction=direction, last=str(m.last),
                             net_percent_change=str(m.net_percent_change.quantize(CENT)),
                             volume=m.volume)
                    for m in got[:MOVERS_PER_SIDE]
                )
        except BrokerUnauthorized:
            movers_error = "broker blind: token absent/dead"
        except (BrokerError, OSError, ValueError) as e:
            movers_error = f"movers read failed: {type(e).__name__}"
    else:
        rows = [row_for(s, series[s], spy, "etf", etfs) for s in desk.etf_list
                if len(series.get(s, [])) >= 2]
        rows.sort(key=lambda r: Decimal(r.rs_3m) if r.rs_3m is not None else Decimal("-1e9"),
                  reverse=True)
        context = [row_for(s, series[s], spy, "context", etfs) for s in ("SPY", *desk.context_symbols)
                   if len(series.get(s, [])) >= 2]
    own = [
        OpenPitchRow(id=p.id, symbol=p.symbol, direction=p.direction, session=p.session.isoformat(),
                     target=str(p.target), invalidation=str(p.invalidation),
                     horizon_days=p.horizon_days, conviction=p.conviction)
        for p in await open_pitches(store) if p.analyst == analyst
    ]
    return Briefing(
        analyst=analyst, asof=spy[-1].date.isoformat() if spy else None, rows=rows,
        context=context, movers=movers, movers_error=movers_error,
        record=await analyst_record(store, analyst), open_pitches=own, notes=notes,
    )
```

The gap screen needs at least two bars; `ind.gap_pct` returns None otherwise.
`row_for` needs at least one bar; every screen guarantees two. The
`movers-EQUITY_ALL.json` fixture ignores direction (see
`FakeBroker.movers`), so both sides return the same rows in tests. That is
fine.

- [ ] **Step 3: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_briefing.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/tc/desk/briefing.py tests/engine/unit/test_desk_briefing.py
git commit -m "desk: briefings the engine computes, so the analysts spend their turns on judgement"
```

---

### Task 8: The analyst tools on the MCP `research` role

**Files:**
- Create: `engine/tc/mcp/tools_desk.py`
- Modify: `engine/tc/mcp/registry.py` (`ANALYST_TOOLS`)
- Modify: `engine/tc/mcp/server.py` (`McpDeps.active`, `McpDeps.trading_day`)
- Modify: `engine/tc/main.py` (the `ActiveJob` holder, set around `_job_claude`; wire `tools_desk`)
- Modify: `tests/engine/contract/test_mcp_no_order_tools.py` (registrars and disjointness)
- Modify: `tests/engine/unit/desk_fixtures.py` (add `desk_settings`, `desk_deps`)
- Test: `tests/engine/unit/test_desk_tools_analyst.py`

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces:
  - Tools `briefing`, `pitch_submit`, `pitch_withdraw` and `my_record`.
  - `tools_desk.register(server, deps, role)`: analyst tools on `research`.
    Task 13 adds the PM half on `decide`.
  - `McpDeps.active: ActiveJob`.
  - `McpDeps.trading_day: Callable[[date], bool]`.

- [ ] **Step 1: Add builders to `desk_fixtures.py`**

Append:

```python
from datetime import datetime

from tc.broker.client import Broker
from tc.config import Settings, load_settings
from tc.desk.models import ActiveJob
from tc.mcp.server import McpDeps
from tc.research.docs import DocStore
from tc.store.db import Store

DESK_CONFIG = """
engine:
  data_dir: {p}
  repo_dir: {repo}
  research_dir: {p}/research
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
desk:
  etf_list: [SPY, XLK, XLF]
  context_symbols: [UUP]
"""


def desk_settings(tmp_path: Path, env_extra: str = "") -> Settings:
    (tmp_path / "config.yml").write_text(DESK_CONFIG.format(p=tmp_path, repo=REPO))
    (tmp_path / ".env").write_text("TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n" + env_extra)
    return load_settings(tmp_path / "config.yml", tmp_path / ".env")


def desk_deps(store: Store, tmp_path: Path, broker: Broker, now: datetime,
              job: str | None = None) -> McpDeps:
    return McpDeps(
        store=store, broker=broker, docs=DocStore(tmp_path / "research", store),
        rules=RULES, settings=desk_settings(tmp_path), clock=lambda: now,
        active=ActiveJob(job),
    )
```

(Move these imports to the top of the module with the others; ruff's
isort rule wants them there.)

- [ ] **Step 2: Write the failing tests**

Create `tests/engine/unit/test_desk_tools_analyst.py`:

```python
"""Task 8: the analyst tools, driven the way the model drives them
(`ToolManager.call_tool`, so the schema validation layer is exercised)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from desk_fixtures import bar, desk_deps, flat_bars
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
from tc.mcp import tools_desk
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)
PITCH: dict[str, Any] = {
    "symbol": "XLK", "direction": "up", "thesis": "semis leadership is broadening out",
    "evidence": [{"url": "https://example.com/x", "claim": "breadth improved", "date": "2026-09-28"}],
    "target": "110", "invalidation": "95", "horizon_days": 5, "conviction": 3, "benchmark": "XLK",
}


async def _server(store: Store, tmp_path: Path, job: str | None) -> FastMCP:
    await store.upsert_bars("XLK", flat_bars(date(2026, 9, 1), 20, "100"))
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(server, desk_deps(store, tmp_path, FakeBroker(FIX, EVENING), EVENING, job),
                        "research")
    return server


async def call(server: FastMCP, tool: str, /, **arguments: Any) -> Any:
    return await server._tool_manager.call_tool(tool, arguments)


async def test_no_running_analyst_job_means_no_desk_tools(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, None)
    with pytest.raises(ToolError, match="no analyst job is running"):
        await call(server, "pitch_submit", **PITCH)


async def test_pitch_submit_files_under_the_running_analyst(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    out = await call(server, "pitch_submit", **PITCH)
    assert out.session == "2026-09-29"
    row = await desk_store.fetchone("SELECT analyst FROM pitches WHERE id=?", (out.id,))
    assert row is not None and row["analyst"] == "macro"


async def test_the_analyst_is_not_an_argument_the_model_can_pass(
    desk_store: Store, tmp_path: Path,
) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    tool = server._tool_manager.get_tool("pitch_submit")
    assert tool is not None and "analyst" not in tool.parameters["properties"]


async def test_a_refusal_reaches_the_model_as_text_it_can_act_on(
    desk_store: Store, tmp_path: Path,
) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    with pytest.raises(ToolError, match="target > last price > invalidation"):
        await call(server, "pitch_submit", **{**PITCH, "target": "99"})


async def test_withdraw_and_record_round_trip(desk_store: Store, tmp_path: Path) -> None:
    server = await _server(desk_store, tmp_path, "analyst_macro")
    out = await call(server, "pitch_submit", **PITCH)
    # still before the 9/29 open, so withdrawable
    got = await call(server, "pitch_withdraw", pitch_id=out.id)
    assert got.withdrawn == out.id
    rec = await call(server, "my_record")
    assert rec.rows == []


async def test_briefing_answers_for_the_running_analyst(desk_store: Store, tmp_path: Path) -> None:
    await desk_store.upsert_bars("SPY", [bar(d, 1, 1, 1, 1) for d in
                                         [date(2026, 9, 28)]])
    server = await _server(desk_store, tmp_path, "analyst_macro")
    b = await call(server, "briefing")
    assert b.analyst == "macro" and [r.symbol for r in b.rows] == ["XLK"]
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_tools_analyst.py`
Expected: FAIL (`cannot import name 'tools_desk'` / `McpDeps` has no `active`).

- [ ] **Step 3: Extend `McpDeps`**

In `engine/tc/mcp/server.py`:

```python
from dataclasses import dataclass, field
from datetime import date, datetime

from tc.desk.models import ActiveJob


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5
```

Add these fields at the end of `McpDeps`:

```python
    # Which Claude job is running (tc/desk/models.ActiveJob), set by the engine
    # around every dispatch: the desk tools read the analyst's identity here,
    # never from the model's arguments.
    active: ActiveJob = field(default_factory=ActiveJob)
    # The engine's trading-day answer (broker calendar for today, weekday
    # otherwise), for a pitch's reference session.
    trading_day: Callable[[date], bool] = _is_weekday
```

`tc.desk.models` imports nothing from `tc.mcp`, so there is no cycle.

- [ ] **Step 4: Declare the tools**

In `engine/tc/mcp/registry.py`, add after `RESEARCH_TOOLS`:

```python
# The trading desk's analyst tools (trading-desk design §5, §13). The desk
# reuses the `research` role for its analysts so the bearer installed on the
# server keeps working; the analyst's identity comes from the running job.
ANALYST_TOOLS: tuple[str, ...] = ("briefing", "pitch_submit", "pitch_withdraw", "my_record")
```

Then change the research entry of `ROLE_TOOLS` to
`"research": COMMON_TOOLS + READ_TOOLS + RESEARCH_TOOLS + ANALYST_TOOLS,`.

- [ ] **Step 5: Implement `tc/mcp/tools_desk.py` (analyst half)**

```python
"""The trading desk's tool surface (trading-desk design §5, §6, §13).

Bodies are thin: every rule lives in tc/desk/, where it is tested without a
server. This layer does three things only:

* resolves WHO is calling from the running job (`deps.active`), never from
  an argument -- an analyst cannot file under another analyst's name, and a
  tool reached outside a scheduled run is refused;
* turns a `DeskRefused` into a `ToolError` whose sentence the model reads;
* turns a broker fault into one line naming its class, never its message.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import BrokerError, BrokerUnauthorized
from tc.desk.briefing import Briefing, build_briefing
from tc.desk.models import JOB_ANALYST, Analyst, Direction, DeskRefused
from tc.desk.pitches import (
    EvidenceItem,
    PitchIn,
    last_close,
    submit_pitch,
    tradeable_symbols,
    withdraw_pitch,
)
from tc.desk.scoring import RecordRow, analyst_record
from tc.mcp.registry import Role

if TYPE_CHECKING:  # pragma: no cover -- import-cycle guard, as in tools_research
    from tc.mcp.server import McpDeps


class PitchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    session: str
    symbol: str
    direction: str
    target: str
    invalidation: str
    horizon_days: int


class Withdrawn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    withdrawn: int


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[RecordRow]


@contextlib.contextmanager
def refusals() -> Iterator[None]:
    try:
        yield
    except DeskRefused as e:
        raise ToolError(str(e)) from e
    except BrokerUnauthorized as e:
        raise ToolError("broker blind: token absent/dead") from e
    except BrokerError as e:
        raise ToolError(f"broker read failed: {type(e).__name__}") from e


def _analyst(deps: McpDeps) -> Analyst:
    a = JOB_ANALYST.get(deps.active.name or "")
    if a is None:
        raise ToolError(
            "no analyst job is running: the desk's analyst tools answer only inside a"
            " scheduled analyst run"
        )
    return a


def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    if role == "research":
        _register_analyst(server, deps)


def _register_analyst(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(
        name="briefing",
        description=(
            "Your briefing: the engine-computed screen rows for your approach, "
            "context rows, movers (news), your own open pitches and your last 10 "
            "resolved pitches with how they turned out."
        ),
    )
    async def briefing() -> Briefing:
        a = _analyst(deps)
        return await build_briefing(deps.store, deps.broker, a, deps.settings.desk)

    @server.tool(
        name="pitch_submit",
        description=(
            "File one pitch: a dated, falsifiable prediction. Prices are decimal "
            "strings. Up: target > last > invalidation; down: the reverse; both "
            "within 30% of the last price. horizon_days 2-20 trading days. The "
            "engine stamps the reference price (next session's open) -- never you."
        ),
    )
    async def pitch_submit(
        symbol: Annotated[str, Field(max_length=10)],
        direction: Direction,
        thesis: Annotated[str, Field(min_length=10, max_length=400)],
        evidence: Annotated[list[EvidenceItem], Field(min_length=1, max_length=5)],
        target: str,
        invalidation: str,
        horizon_days: int,
        conviction: Annotated[int, Field(ge=1, le=5)],
        benchmark: str,
    ) -> PitchOut:
        a = _analyst(deps)
        sym = symbol.strip().upper()
        with refusals():
            last = await last_close(deps.store, sym)
            if last is None:
                quotes = await deps.broker.quotes([sym])
                if sym not in quotes:
                    raise DeskRefused(f"no price for {sym}: not in the bars set and no quote")
                last = quotes[sym].last
            pin = PitchIn(symbol=sym, direction=direction, thesis=thesis, evidence=evidence,
                          target=target, invalidation=invalidation, horizon_days=horizon_days,
                          conviction=conviction, benchmark=benchmark)
            p = await submit_pitch(
                deps.store, analyst=a, pin=pin, now=deps.clock(), last=Decimal(last),
                tradeable=await tradeable_symbols(deps.store, deps.settings.desk),
                rules=deps.rules, is_trading_day=deps.trading_day,
            )
        return PitchOut(id=p.id, session=p.session.isoformat(), symbol=p.symbol,
                        direction=p.direction, target=str(p.target),
                        invalidation=str(p.invalidation), horizon_days=p.horizon_days)

    @server.tool(
        name="pitch_withdraw",
        description="Withdraw one of your own pitches, only before its session opens.",
    )
    async def pitch_withdraw(pitch_id: int) -> Withdrawn:
        a = _analyst(deps)
        with refusals():
            await withdraw_pitch(deps.store, analyst=a, pitch_id=pitch_id, now=deps.clock())
        return Withdrawn(withdrawn=pitch_id)

    @server.tool(name="my_record", description="Your last 10 resolved pitches, newest first.")
    async def my_record() -> Record:
        return Record(rows=await analyst_record(deps.store, _analyst(deps)))
```

- [ ] **Step 6: Wire it into the engine**

In `engine/tc/main.py`:
- Import `tools_desk` alongside `tools_read, tools_research`.
- Import `ActiveJob` from `tc.desk.models`.
- In `_wire_mcp_registrars`, add these two lines before `_mcp_wired = True`:

```python
    mcp_server.register("research", tools_desk.register)
    mcp_server.register("decide", tools_desk.register)
```

- In `Engine.__init__`, add `self._active = ActiveJob()` next to `self._jobs`.
- In `_build_mcp`, pass `active=self._active, trading_day=self._trading_day` into `McpDeps(...)`.
- Replace `_job_claude` with:

```python
    async def _job_claude(
        self, job: str, now: datetime, *, ignore_window: bool = False
    ) -> tuple[Verdict, dict[str, Any]]:
        if self._jobs is None:
            return "noop", {"skipped": "no runner configured"}
        # The desk tools read WHO is calling from here (tc/desk/models.ActiveJob);
        # set for exactly the life of the dispatch, cleared even on a raise.
        self._active.name = job
        try:
            return await self._jobs.execute(job, now, ignore_window=ignore_window)
        finally:
            self._active.name = None
```

- [ ] **Step 7: Keep the contract test honest**

In `tests/engine/contract/test_mcp_no_order_tools.py`:
- Import `tools_desk` alongside `tools_read, tools_research`.
- Set the registrars to:

```python
    monkeypatch.setitem(server_mod._REGISTRARS, "research", [
        tools_read.register, tools_research.register, tools_desk.register,
    ])
    monkeypatch.setitem(server_mod._REGISTRARS, "decide", [
        tools_read.register, tools_desk.register,
    ])
```

- At the end of `test_the_two_roles_have_disjoint_write_surfaces`, add:

```python
    assert "pitch_submit" in research and "pitch_submit" not in decide
```

- [ ] **Step 8: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS. The consistency checker's `tool_registry` check also passes:
no new name matches `place|cancel|replace|order`.

- [ ] **Step 9: Commit**

```bash
git add engine/tc/mcp/tools_desk.py engine/tc/mcp/registry.py engine/tc/mcp/server.py engine/tc/main.py \
  tests/engine/contract/test_mcp_no_order_tools.py tests/engine/unit/desk_fixtures.py \
  tests/engine/unit/test_desk_tools_analyst.py
git commit -m "desk: analyst tools on the research role; who is calling comes from the running job"
```

---
### Task 9: Sizing, book caps and the share stop

**Files:**
- Create: `engine/tc/desk/sizing.py`
- Test: `tests/engine/unit/test_desk_sizing.py`

**Interfaces:**
- Consumes: `tc.rules.arith.cap_dollars`, `stop_geometry`, `StopGeometry`; `Rules`.
- Produces:
  - `SizingRefused(DeskRefused)`
  - `Holding(symbol, market_value, benchmark, is_option, premium_paid=0)`
  - `BookState(equity, cash, holdings, pending)`, with property `open_premium`
  - `conviction_pct(kind, conviction, rules) -> Decimal`
  - `share_quantity(conviction, equity, max_entry, rules) -> int`
  - `option_quantity(conviction, equity, ask, rules) -> int`
  - `check_book(book, *, symbol, benchmark, notional, is_option, correlated, rules, reserve) -> None`
  - `entry_stop(fill, atr_pct, invalidation, rules) -> StopGeometry`
  - `OPTION_MULTIPLIER = 100`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_sizing.py`:

```python
"""Task 9: conviction sizing, the book's caps, and the share stop (spec §9.2, §9.5)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from desk_fixtures import RULES
from hypothesis import given
from hypothesis import strategies as st

from tc.desk.sizing import (
    BookState,
    Holding,
    SizingRefused,
    check_book,
    conviction_pct,
    entry_stop,
    option_quantity,
    share_quantity,
)
from tc.rules.arith import stop_geometry

EQ = Decimal("3700")
RESERVE = Decimal("900.00")


def _book(*holdings: Holding, cash: str = "3700", pending: int = 0) -> BookState:
    return BookState(equity=EQ, cash=Decimal(cash), holdings=holdings, pending=pending)


def test_conviction_below_three_is_never_funded() -> None:
    with pytest.raises(SizingRefused, match="scored, never funded"):
        conviction_pct("shares", 2, RULES)
    assert [conviction_pct("shares", c, RULES) for c in (3, 4, 5)] == [10, 15, 20]
    assert [conviction_pct("option", c, RULES) for c in (3, 4, 5)] == [5, Decimal("7.5"), 10]


def test_share_quantity_floors_to_whole_shares() -> None:
    assert share_quantity(3, EQ, Decimal("48.25"), RULES) == 7        # 370.00 // 48.25
    with pytest.raises(SizingRefused, match="no whole share"):
        share_quantity(3, EQ, Decimal("400"), RULES)


def test_option_quantity_is_whole_contracts_under_the_cap() -> None:
    assert option_quantity(5, EQ, Decimal("3.50"), RULES) == 1        # 370 // 350
    with pytest.raises(SizingRefused, match="over the conviction-5 premium cap"):
        option_quantity(5, EQ, Decimal("4.00"), RULES)


def test_the_position_count_includes_pending_proposals() -> None:
    held = tuple(Holding(f"S{i}", Decimal(100), "XLK", False) for i in range(7))
    with pytest.raises(SizingRefused, match="the limit is 8"):
        check_book(_book(*held, pending=1), symbol="NEW", benchmark="SPY",
                   notional=Decimal(100), is_option=False, correlated=frozenset(),
                   rules=RULES, reserve=RESERVE)


def test_the_reserve_is_a_floor_on_cash() -> None:
    with pytest.raises(SizingRefused, match="reserve"):
        check_book(_book(cash="1000"), symbol="NEW", benchmark="SPY", notional=Decimal(150),
                   is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)


def test_the_single_position_cap_binds() -> None:
    with pytest.raises(SizingRefused, match="§3.1"):
        check_book(_book(), symbol="NEW", benchmark="SPY", notional=Decimal(1300),
                   is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)


def test_open_option_premium_is_capped_at_thirty_percent() -> None:
    opts = tuple(Holding(f"O{i}", Decimal(300), "SPY", True, Decimal(360)) for i in range(3))
    with pytest.raises(SizingRefused, match="§3.2"):
        check_book(_book(*opts), symbol="NEW", benchmark="SPY", notional=Decimal(100),
                   is_option=True, correlated=frozenset(), rules=RULES, reserve=RESERVE)


def test_the_correlation_cluster_is_a_cap_not_a_ban() -> None:
    same_sector = Holding("AAA", Decimal(1500), "XLK", False)
    # 1500 + 300 = 1800 <= 50% of 3700 = 1850: allowed.
    check_book(_book(same_sector), symbol="BBB", benchmark="XLK", notional=Decimal(300),
               is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)
    with pytest.raises(SizingRefused, match="§3.8"):
        check_book(_book(same_sector), symbol="BBB", benchmark="XLK", notional=Decimal(400),
                   is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)
    # SPY as a benchmark is "the market", not a sector: it clusters nothing.
    check_book(_book(Holding("AAA", Decimal(1500), "SPY", False)), symbol="BBB",
               benchmark="SPY", notional=Decimal(400), is_option=False,
               correlated=frozenset(), rules=RULES, reserve=RESERVE)
    # A measured correlation clusters across sectors.
    with pytest.raises(SizingRefused, match="§3.8"):
        check_book(_book(Holding("AAA", Decimal(1500), "XLE", False)), symbol="BBB",
                   benchmark="XLK", notional=Decimal(400), is_option=False,
                   correlated=frozenset({"AAA"}), rules=RULES, reserve=RESERVE)


def test_a_tight_invalidation_raises_the_stop_above_the_formula() -> None:
    g = entry_stop(Decimal("50.00"), Decimal("1.0"), Decimal("48.123"), RULES)
    assert g.trigger == Decimal("48.13")                  # invalidation, rounded UP
    assert g.limit == Decimal("45.72")                    # 5% below, rounded DOWN


def test_a_loose_invalidation_leaves_the_formula_stop() -> None:
    formula = stop_geometry(Decimal("50.00"), Decimal("1.0"), RULES)
    g = entry_stop(Decimal("50.00"), Decimal("1.0"), Decimal("40"), RULES)
    assert g.trigger == formula.trigger


def test_an_invalidation_at_or_above_the_fill_is_refused() -> None:
    with pytest.raises(SizingRefused, match="already broken"):
        entry_stop(Decimal("50.00"), Decimal("1.0"), Decimal("50.00"), RULES)


@given(st.decimals(min_value="5", max_value="1000", places=2),
       st.decimals(min_value="0.2", max_value="6", places=2),
       st.decimals(min_value="0.5", max_value="0.99", places=2))
def test_the_stop_is_never_below_the_formula_and_always_below_the_fill(
    fill: Decimal, atr: Decimal, frac: Decimal,
) -> None:
    inval = (fill * frac).quantize(Decimal("0.01"))
    formula = stop_geometry(fill, atr, RULES)
    try:
        g = entry_stop(fill, atr, inval, RULES)
    except SizingRefused:
        return
    assert g.trigger >= formula.trigger and g.trigger < fill and g.limit < g.trigger
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_sizing.py`
Expected: FAIL (`No module named 'tc.desk.sizing'`).

- [ ] **Step 2: Implement `tc/desk/sizing.py`**

```python
"""How much, and whether the book can take it (spec §9.2), plus the share
stop (§9.5). Pure arithmetic over a `BookState` snapshot; every cap is read
from `Rules`, never typed here."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from tc.desk.models import DeskRefused
from tc.money import CENT, cents, floor_cents
from tc.rules.arith import StopGeometry, cap_dollars, stop_geometry
from tc.rules.model import Rules

HUNDRED = Decimal(100)
OPTION_MULTIPLIER = 100


class SizingRefused(DeskRefused):
    pass


@dataclass(frozen=True)
class Holding:
    symbol: str             # the UNDERLYING, for options too
    market_value: Decimal
    benchmark: str
    is_option: bool
    premium_paid: Decimal = Decimal(0)


@dataclass(frozen=True)
class BookState:
    equity: Decimal
    cash: Decimal
    holdings: tuple[Holding, ...]
    pending: int

    @property
    def open_premium(self) -> Decimal:
        """§3.2 "open premium" = premium PAID on open positions; marks irrelevant."""
        return sum((h.premium_paid for h in self.holdings if h.is_option), Decimal(0))


def conviction_pct(kind: Literal["shares", "option"], conviction: int, rules: Rules) -> Decimal:
    if conviction < int(rules.get("strategy", "min_fundable_conviction")):
        raise SizingRefused(
            f'conviction {conviction} calls are scored, never funded; submit with funding "none"'
        )
    prefix = "size_shares_pct_conviction_" if kind == "shares" else "size_option_premium_pct_conviction_"
    return rules.get("strategy", f"{prefix}{conviction}")


def share_quantity(conviction: int, equity: Decimal, max_entry: Decimal, rules: Rules) -> int:
    budget = cap_dollars(conviction_pct("shares", conviction, rules), equity)
    qty = int(budget // max_entry)
    if qty < 1:
        raise SizingRefused(
            f"the conviction-{conviction} budget {budget} buys no whole share at {max_entry}"
        )
    return qty


def option_quantity(conviction: int, equity: Decimal, ask: Decimal, rules: Rules) -> int:
    budget = cap_dollars(conviction_pct("option", conviction, rules), equity)
    per = ask * OPTION_MULTIPLIER
    qty = int(budget // per)
    if qty < 1:
        raise SizingRefused(
            f"one contract costs {cents(per)}, over the conviction-{conviction} premium cap {budget}"
        )
    return qty


def check_book(
    book: BookState,
    *,
    symbol: str,
    benchmark: str,
    notional: Decimal,
    is_option: bool,
    correlated: frozenset[str],
    rules: Rules,
    reserve: Decimal,
) -> None:
    max_pos = int(rules.get("strategy", "max_funded_positions"))
    if len(book.holdings) + book.pending >= max_pos:
        raise SizingRefused(
            f"the book carries {len(book.holdings)} positions and {book.pending} pending"
            f" proposals; the limit is {max_pos}"
        )
    if book.cash - notional < reserve:
        raise SizingRefused(
            f"cash {book.cash} less {cents(notional)} would breach the {reserve} reserve"
        )
    if notional > cap_dollars(rules.single_position_pct, book.equity):
        raise SizingRefused(
            f"{cents(notional)} is over the §3.1 single-position cap"
            f" ({rules.single_position_pct}% of {book.equity})"
        )
    if is_option:
        if notional > cap_dollars(rules.option_single_position_pct, book.equity):
            raise SizingRefused(
                f"{cents(notional)} premium is over the §3.2 per-position cap"
                f" ({rules.option_single_position_pct}% of {book.equity})"
            )
        if book.open_premium + notional > cap_dollars(rules.option_open_premium_pct, book.equity):
            raise SizingRefused(
                f"open premium {book.open_premium} plus {cents(notional)} is over the §3.2"
                f" {rules.option_open_premium_pct}% open-premium cap"
            )
    cluster = [
        h for h in book.holdings
        if h.symbol == symbol or h.symbol in correlated
        or (benchmark != "SPY" and h.benchmark == benchmark)
    ]
    cap_pct = rules.get("manual", "correlation_cap_pct")
    held = sum((h.market_value for h in cluster), Decimal(0))
    if held + notional > cap_dollars(cap_pct, book.equity):
        names = ", ".join(sorted({h.symbol for h in cluster}))
        raise SizingRefused(
            f"{symbol} clusters with {names} ({held} held); adding {cents(notional)} breaks"
            f" the §3.8 {cap_pct}% correlated-exposure cap"
        )


def entry_stop(fill: Decimal, atr_pct: Decimal, invalidation: Decimal, rules: Rules) -> StopGeometry:
    """Spec §9.5: the trigger is the HIGHER of the §3.4 formula and the call's
    invalidation -- §3.4 lets a trigger be raised, never lowered, so a thesis
    with a tight invalidation gets a tight stop. Trigger rounded up, limit
    rounded down (the trade log's convention since 2026-08-14)."""
    formula = stop_geometry(fill, atr_pct, rules)
    trigger = max(formula.trigger, invalidation.quantize(CENT, rounding=ROUND_CEILING))
    if trigger >= fill:
        raise SizingRefused(
            f"the stop trigger {trigger} is at or above the fill {fill}: the thesis is already broken"
        )
    limit = floor_cents(trigger * (HUNDRED - rules.stop_limit_pct_below_trigger) / HUNDRED)
    return StopGeometry(trigger=trigger, limit=limit, trigger_pct=(fill - trigger) / fill * HUNDRED)
```

- [ ] **Step 3: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_sizing.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/tc/desk/sizing.py tests/engine/unit/test_desk_sizing.py
git commit -m "desk: conviction sizing and every book cap, the correlation rule as the cap it is"
```

---

### Task 10: Option contract selection

**Files:**
- Create: `engine/tc/desk/options.py`
- Test: `tests/engine/unit/test_desk_options.py`

**Interfaces:**
- Consumes:
  - `OptionChainView`, `OptionContract` (broker models).
  - `Broker.option_chain`.
  - `Rules`.
- Produces:
  - `OptionCandidate`, a model whose price fields are strings.
  - `min_dte(horizon_days, rules) -> int`
  - `delta_band(rules) -> tuple[Decimal, Decimal]`
  - `select(chain, direction, horizon_days, premium_cap, rules) -> list[OptionCandidate]`, at most 3.
  - `fetch_candidates(broker, symbol, direction, horizon_days, premium_cap, rules, today) -> list[OptionCandidate]`
  - `osi_expiry(osi) -> date`
  - `same_osi(a, b) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_options.py`:

```python
"""Task 10: only contracts that clear every manual §3.2 floor (spec §9.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from desk_fixtures import RULES

from tc.broker.fake import FakeBroker
from tc.broker.models import OptionChainView, OptionContract
from tc.desk.options import fetch_candidates, min_dte, osi_expiry, same_osi, select

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
CAP = Decimal("370")


def _c(strike: str, delta: str | None, oi: int = 1000, bid: str = "2.40", ask: str = "2.50",
       dte: int = 40, kind: Literal["CALL", "PUT"] = "CALL") -> OptionContract:
    return OptionContract(
        osi=f"AAA   261106{kind[0]}{int(Decimal(strike) * 1000):08d}", expiry=date(2026, 11, 6),
        strike=Decimal(strike), kind=kind, bid=Decimal(bid), ask=Decimal(ask), last=Decimal(ask),
        delta=None if delta is None else Decimal(delta), open_interest=oi, volume=10,
        implied_volatility=Decimal(30), days_to_expiration=dte,
    )


def _chain(*cs: OptionContract) -> OptionChainView:
    return OptionChainView(symbol="AAA", underlying_price=Decimal(50), contracts=list(cs))


def test_min_dte_is_the_manual_floor_or_the_horizon_plus_the_close_plus_a_week() -> None:
    assert min_dte(2, RULES) == 18          # max(18, 3 + 5 + 7 = 15)
    assert min_dte(20, RULES) == 40         # 28 + 5 + 7


@pytest.mark.parametrize("contract", [
    _c("50", "0.44"),                       # under the manual band floor
    _c("45", "0.76"),                       # over the band ceiling
    _c("50", None),                         # Schwab could not price it
    _c("50", "0.60", oi=499),               # open interest
    _c("50", "0.60", bid="2.20", ask="2.50"),   # spread 12.8% of mid
    _c("50", "0.60", bid="3.80", ask="3.90"),   # 390 premium over the 370 cap
    _c("50", "0.60", dte=17),               # under min DTE
    _c("50", "0.60", dte=61),               # over option_max_dte
    _c("50", "0.60", bid="0", ask="0.10"),  # no bid
    _c("50", "-0.60", kind="PUT"),          # wrong side for an up call
])
def test_each_floor_refuses_its_contract(contract: OptionContract) -> None:
    assert select(_chain(contract), "up", 5, CAP, RULES) == []


def test_ranking_prefers_delta_near_0_60_then_the_tighter_spread() -> None:
    far = _c("47", "0.72")
    near_wide = _c("50", "0.61", bid="2.30", ask="2.50")
    near_tight = _c("50.5", "0.59", bid="2.40", ask="2.45")
    got = select(_chain(far, near_wide, near_tight), "up", 5, CAP, RULES)
    assert [c.strike for c in got] == ["50.5", "50", "47"]


def test_puts_are_judged_on_absolute_delta() -> None:
    put = _c("50", "-0.58", kind="PUT")
    got = select(_chain(put), "down", 5, CAP, RULES)
    assert len(got) == 1 and got[0].kind == "PUT" and got[0].delta == "0.58"


def test_osi_expiry_parses_padded_and_unpadded_symbols() -> None:
    assert osi_expiry("CSX   261016C00047500") == date(2026, 10, 16)
    assert osi_expiry("CSX261016C00047500") == date(2026, 10, 16)
    assert same_osi("CSX   261016C00047500", "csx261016c00047500")
    with pytest.raises(ValueError, match="not an OSI option symbol"):
        osi_expiry("CSX")


async def test_fetch_candidates_reads_the_live_chain() -> None:
    broker = FakeBroker(FIX, datetime(2026, 9, 7, 17, 31, tzinfo=UTC))
    got = await fetch_candidates(broker, "CSX", "up", 5, CAP, RULES, date(2026, 9, 7))
    assert got, "the recorded CSX chain has at least one contract inside every floor"
    for c in got:
        assert Decimal("0.45") <= Decimal(c.delta) <= Decimal("0.75")
        assert c.open_interest >= 500 and Decimal(c.spread_pct) <= 10
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_options.py`
Expected: FAIL (`No module named 'tc.desk.options'`).

- [ ] **Step 2: Implement `tc/desk/options.py`**

```python
"""Which contract (spec §9.3): only ones that clear every manual §3.2 floor,
ranked toward the strategy's target delta. The engine builds the symbol --
the model picks from this list and nothing else."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker
from tc.broker.models import OptionChainView
from tc.desk.models import Direction
from tc.rules.model import Rules

OSI = re.compile(r"(\d{6})([CP])(\d{8})$")
STRIKE_COUNT = 30
CENT = Decimal("0.01")
EXTRA_WEEK = 7


def _plain(d: Decimal) -> str:
    """50 -> "50", 50.50 -> "50.5": no exponent, no trailing zeros."""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


class OptionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    osi: str
    expiry: str
    strike: str
    kind: str
    bid: str
    ask: str
    delta: str
    open_interest: int
    spread_pct: str
    dte: int
    premium_per_contract: str


def min_dte(horizon_days: int, rules: Rules) -> int:
    """Long enough to outlast the horizon AND the §3.3 5-DTE close, plus a
    week, never under the manual's own floor."""
    calendar = -(-horizon_days * 7 // 5)
    return max(rules.option_min_dte, calendar + rules.option_close_at_dte + EXTRA_WEEK)


def delta_band(rules: Rules) -> tuple[Decimal, Decimal]:
    lo = max(rules.get("manual", "option_min_delta"), rules.get("strategy", "option_min_delta"))
    return lo, rules.get("manual", "option_max_delta")


def select(
    chain: OptionChainView, direction: Direction, horizon_days: int, premium_cap: Decimal,
    rules: Rules,
) -> list[OptionCandidate]:
    kind = "CALL" if direction == "up" else "PUT"
    lo_dte, hi_dte = min_dte(horizon_days, rules), int(rules.get("strategy", "option_max_dte"))
    d_lo, d_hi = delta_band(rules)
    target = rules.get("strategy", "option_target_delta")
    min_oi = int(rules.get("manual", "option_min_open_interest"))
    max_spread = rules.get("manual", "option_max_spread_pct_of_mid")
    ranked: list[tuple[Decimal, Decimal, int, OptionCandidate]] = []
    for c in chain.contracts:
        if c.kind != kind or c.delta is None:
            continue
        if not lo_dte <= c.days_to_expiration <= hi_dte:
            continue
        d = abs(c.delta)
        if not d_lo <= d <= d_hi:
            continue
        if c.open_interest < min_oi or c.bid <= 0 or c.ask <= 0 or c.ask < c.bid:
            continue
        mid = (c.bid + c.ask) / 2
        spread = (c.ask - c.bid) / mid * 100
        premium = c.ask * 100
        if spread > max_spread or premium > premium_cap:
            continue
        ranked.append((abs(d - target), spread, -c.open_interest, OptionCandidate(
            osi=c.osi, expiry=c.expiry.isoformat(), strike=_plain(c.strike),
            kind=c.kind, bid=str(c.bid), ask=str(c.ask), delta=str(d),
            open_interest=c.open_interest, spread_pct=str(spread.quantize(CENT)),
            dte=c.days_to_expiration, premium_per_contract=str(premium.quantize(CENT)),
        )))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in ranked[:3]]


async def fetch_candidates(
    broker: Broker, symbol: str, direction: Direction, horizon_days: int,
    premium_cap: Decimal, rules: Rules, today: date,
) -> list[OptionCandidate]:
    lo = min_dte(horizon_days, rules)
    hi = int(rules.get("strategy", "option_max_dte"))
    chain = await broker.option_chain(
        symbol, today + timedelta(days=lo), today + timedelta(days=hi), STRIKE_COUNT,
        "CALL" if direction == "up" else "PUT",
    )
    return select(chain, direction, horizon_days, premium_cap, rules)


def osi_expiry(osi: str) -> date:
    m = OSI.search(osi.replace(" ", "").upper())
    if m is None:
        raise ValueError(f"not an OSI option symbol: {osi!r}")
    return datetime.strptime(m.group(1), "%y%m%d").date()


def same_osi(a: str, b: str) -> bool:
    """Schwab pads the root to six characters; a model copying the symbol
    usually does not. Compare with the padding removed."""
    return a.replace(" ", "").upper() == b.replace(" ", "").upper()
```

- [ ] **Step 3: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_options.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add engine/tc/desk/options.py tests/engine/unit/test_desk_options.py
git commit -m "desk: option candidates are only contracts that clear every manual floor"
```

---
### Task 11: Calls and the paper book (store operations)

**Files:**
- Create: `engine/tc/desk/calls.py`, `engine/tc/desk/paper.py`
- Test: `tests/engine/unit/test_desk_calls_paper.py`

**Interfaces:**
- Consumes: the Task 1 tables; `Holding`, `BookState` (Task 9).
- Produces:
  - `tc.desk.calls`:
    - Models: `NewCall`; `Call(NewCall)` with `id`.
    - `insert_call(store, c) -> Call`
    - `get_call(store, call_id) -> Call | None`
    - `open_call_for(store, symbol, direction, *, legacy=False) -> Call | None`
    - `calls_made_on(store, session) -> int` (origins `pitch` and `pm`, not extensions)
    - `current_call_id(store, call_id) -> int` (follows extensions forward)
    - `is_extended(store, call_id) -> bool`
    - `tighten(store, call_id, invalidation, now) -> None`
    - `effective_invalidation(store, call) -> Decimal`
  - `tc.desk.paper`:
    - Constants and models: `MULT`, `Proposal`, `PaperPosition`, `ClosedTrade`, `ExitRequest`
    - Book row: `ensure_book(store, start_date, start_equity, now) -> tuple[date, Decimal]`, `book_row(store)`
    - Proposals:
      - `create_proposal(store, *, call_id, created_at, instrument, symbol, underlying, quantity, max_entry_price, atr_pct) -> Proposal`
      - `mark_posted(store, proposal_id, posted_at, veto_deadline, message_id)`
      - `unposted_proposals(store)`, `pending_proposals(store)`
      - `record_outcome(store, proposal_id, at, outcome, *, vetoed, approved, detail)`
    - Fills and positions:
      - `record_fill(store, proposal_id, at, side, quantity, price, reason, stop_trigger=None, stop_limit=None)`
      - `open_positions(store) -> list[PaperPosition]`
      - `cash(store, start_equity) -> Decimal`
      - `closed_trades(store) -> list[ClosedTrade]`
    - Marks: `upsert_mark(store, symbol, price, at)`, `marks(store) -> dict[str, Decimal]`
    - `book_state(store) -> BookState`
    - Exits:
      - `request_exit(store, symbol, proposal_id, reason, at) -> int`
      - `pending_exit_requests(store) -> list[ExitRequest]`
      - `mark_exit_done(store, request_id, at)`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_calls_paper.py`:

```python
"""Task 11: call rows and the paper book's arithmetic."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tc.desk.calls import (
    NewCall,
    calls_made_on,
    current_call_id,
    effective_invalidation,
    get_call,
    insert_call,
    is_extended,
    open_call_for,
    tighten,
)
from tc.desk.models import DeskRefused
from tc.desk.paper import (
    book_row,
    book_state,
    cash,
    closed_trades,
    create_proposal,
    ensure_book,
    mark_exit_done,
    mark_posted,
    open_positions,
    pending_exit_requests,
    pending_proposals,
    record_fill,
    record_outcome,
    request_exit,
    unposted_proposals,
    upsert_mark,
)
from tc.store.db import Store

NOW = datetime(2026, 9, 29, 13, 55, tzinfo=UTC)
TODAY = date(2026, 9, 29)


def _call(**kw: object) -> NewCall:
    base: dict[str, object] = dict(
        made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="a thesis long enough", target=Decimal(55),
        invalidation=Decimal(48), horizon_days=10, conviction=3, benchmark="XLK",
        ref_price=Decimal(50), spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares",
    )
    base.update(kw)
    return NewCall.model_validate(base)


async def test_calls_round_trip_and_count_only_fresh_non_legacy_calls(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    assert (await get_call(desk_store, c.id)) == c
    await insert_call(desk_store, _call(symbol="BBB", origin="legacy", funding="none"))
    await insert_call(desk_store, _call(symbol="CCC", extends_call_id=c.id))
    assert await calls_made_on(desk_store, TODAY) == 1
    assert (await open_call_for(desk_store, "AAA", "up")) is not None
    assert (await open_call_for(desk_store, "BBB", "up")) is None
    assert (await open_call_for(desk_store, "BBB", "up", legacy=True)) is not None


async def test_an_extension_chain_is_followed_forward(desk_store: Store) -> None:
    a = await insert_call(desk_store, _call())
    b = await insert_call(desk_store, _call(extends_call_id=a.id))
    assert await current_call_id(desk_store, a.id) == b.id
    assert await is_extended(desk_store, a.id) and not await is_extended(desk_store, b.id)


async def test_tightening_moves_the_effective_invalidation(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    assert await effective_invalidation(desk_store, c) == 48
    await tighten(desk_store, c.id, Decimal("49.5"), NOW)
    assert await effective_invalidation(desk_store, c) == Decimal("49.5")


async def test_the_book_row_is_written_once(desk_store: Store) -> None:
    assert await ensure_book(desk_store, TODAY, Decimal("3700"), NOW) == (TODAY, Decimal("3700"))
    later = TODAY + timedelta(days=1)
    assert await ensure_book(desk_store, later, Decimal("9999"), NOW) == (TODAY, Decimal("3700"))
    assert await book_row(desk_store) == (TODAY, Decimal("3700"))


async def test_a_proposal_is_pending_until_it_has_an_outcome(desk_store: Store) -> None:
    c = await insert_call(desk_store, _call())
    p = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    assert [x.id for x in await unposted_proposals(desk_store)] == [p.id]
    await mark_posted(desk_store, p.id, NOW, NOW + timedelta(minutes=10), "m1")
    assert await unposted_proposals(desk_store) == []
    assert [x.message_id for x in await pending_proposals(desk_store)] == ["m1"]
    await record_outcome(desk_store, p.id, NOW, "filled", vetoed=False, approved=False, detail={})
    assert await pending_proposals(desk_store) == []


async def test_fills_drive_positions_cash_and_closed_trades(desk_store: Store) -> None:
    await ensure_book(desk_store, TODAY, Decimal("3700"), NOW)
    c = await insert_call(desk_store, _call())
    shares = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="shares",
                                   symbol="AAA", underlying="AAA", quantity=7,
                                   max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    opt = await create_proposal(desk_store, call_id=c.id, created_at=NOW, instrument="call",
                                symbol="AAA   261120C00050000", underlying="AAA", quantity=1,
                                max_entry_price=Decimal("2.60"), atr_pct=None)
    await record_fill(desk_store, shares.id, NOW, "buy", 7, Decimal("50.00"), "entry",
                      Decimal("48.00"), Decimal("45.60"))
    await record_fill(desk_store, opt.id, NOW, "buy", 1, Decimal("2.50"), "entry")
    assert await cash(desk_store, Decimal("3700")) == Decimal("3700") - 350 - 250
    pos = {p.symbol: p for p in await open_positions(desk_store)}
    assert pos["AAA"].stop_trigger == Decimal("48.00") and pos["AAA"].quantity == 7
    await upsert_mark(desk_store, "AAA   261120C00050000", Decimal("3.00"), NOW)
    state = await book_state(desk_store)
    assert state.open_premium == 250
    assert state.equity == Decimal("3100") + 350 + 300     # AAA unmarked -> entry price
    await record_fill(desk_store, shares.id, NOW, "sell", 7, Decimal("55.00"), "target")
    assert [p.symbol for p in await open_positions(desk_store)] == ["AAA   261120C00050000"]
    [t] = await closed_trades(desk_store)
    assert (t.proposal_id, t.ret_pct) == (shares.id, 10)


async def test_book_state_refuses_before_the_book_starts(desk_store: Store) -> None:
    with pytest.raises(DeskRefused, match="has not started"):
        await book_state(desk_store)


async def test_exit_requests_queue_until_done(desk_store: Store) -> None:
    rid = await request_exit(desk_store, "CSX", None, "legacy: thesis gone", NOW)
    assert [r.id for r in await pending_exit_requests(desk_store)] == [rid]
    await mark_exit_done(desk_store, rid, NOW)
    assert await pending_exit_requests(desk_store) == []
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_calls_paper.py`
Expected: FAIL (`No module named 'tc.desk.calls'`).

- [ ] **Step 2: Implement `tc/desk/calls.py`**

```python
"""Calls (spec §6): the PM's predictions. Append-only rows; an extension is
a NEW call pointing back at the one it continues, and a tightening is a new
row that moves the exit level -- never an edit to the call as made, which is
what gets scored."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.desk.models import Direction, Funding, utc_iso
from tc.store.db import Store


class NewCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    made_at: datetime
    session: date
    origin: Literal["pitch", "pm", "legacy"]
    pitch_id: int | None
    extends_call_id: int | None
    symbol: str
    direction: Direction
    thesis: str
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    conviction: int
    benchmark: str
    ref_price: Decimal
    spy_ref: Decimal
    bench_ref: Decimal
    funding: Funding


class Call(NewCall):
    id: int


def _call(r: sqlite3.Row) -> Call:
    return Call(
        id=r["id"], made_at=datetime.fromisoformat(r["made_at"]),
        session=date.fromisoformat(r["session"]), origin=r["origin"], pitch_id=r["pitch_id"],
        extends_call_id=r["extends_call_id"], symbol=r["symbol"], direction=r["direction"],
        thesis=r["thesis"], target=Decimal(r["target"]),
        invalidation=Decimal(r["invalidation"]), horizon_days=r["horizon_days"],
        conviction=r["conviction"], benchmark=r["benchmark"],
        ref_price=Decimal(r["ref_price"]), spy_ref=Decimal(r["spy_ref"]),
        bench_ref=Decimal(r["bench_ref"]), funding=r["funding"],
    )


async def insert_call(store: Store, c: NewCall) -> Call:
    async with store.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO calls(made_at, session, origin, pitch_id, extends_call_id, symbol,"
            " direction, thesis, target, invalidation, horizon_days, conviction, benchmark,"
            " ref_price, spy_ref, bench_ref, funding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                utc_iso(c.made_at), c.session.isoformat(), c.origin, c.pitch_id,
                c.extends_call_id, c.symbol, c.direction, c.thesis, str(c.target),
                str(c.invalidation), c.horizon_days, c.conviction, c.benchmark,
                str(c.ref_price), str(c.spy_ref), str(c.bench_ref), c.funding,
            ),
        )
        cid = int(cur.lastrowid or 0)
    got = await get_call(store, cid)
    assert got is not None
    return got


async def get_call(store: Store, call_id: int) -> Call | None:
    row = await store.fetchone("SELECT * FROM calls WHERE id=?", (call_id,))
    return None if row is None else _call(row)


async def open_call_for(
    store: Store, symbol: str, direction: Direction, *, legacy: bool = False
) -> Call | None:
    # One of two literal operators, chosen by a bool: never input.
    op = "=" if legacy else "!="
    row = await store.fetchone(
        f"SELECT * FROM calls WHERE symbol=? AND direction=? AND origin {op} 'legacy'"  # noqa: S608
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
        " ORDER BY id DESC LIMIT 1",
        (symbol, direction),
    )
    return None if row is None else _call(row)


async def calls_made_on(store: Store, session: date) -> int:
    row = await store.fetchone(
        "SELECT COUNT(*) AS n FROM calls WHERE session=? AND origin IN ('pitch','pm')"
        " AND extends_call_id IS NULL",
        (session.isoformat(),),
    )
    return 0 if row is None else int(row["n"])


async def current_call_id(store: Store, call_id: int) -> int:
    cur = call_id
    while True:
        row = await store.fetchone("SELECT id FROM calls WHERE extends_call_id=?", (cur,))
        if row is None:
            return cur
        cur = int(row["id"])


async def is_extended(store: Store, call_id: int) -> bool:
    row = await store.fetchone("SELECT 1 FROM calls WHERE extends_call_id=?", (call_id,))
    return row is not None


async def tighten(store: Store, call_id: int, invalidation: Decimal, now: datetime) -> None:
    await store.execute(
        "INSERT INTO call_tightenings(call_id, at, invalidation) VALUES (?,?,?)",
        (call_id, utc_iso(now), str(invalidation)),
    )


async def effective_invalidation(store: Store, call: Call) -> Decimal:
    row = await store.fetchone(
        "SELECT invalidation FROM call_tightenings WHERE call_id=? ORDER BY id DESC LIMIT 1",
        (call.id,),
    )
    return call.invalidation if row is None else Decimal(row["invalidation"])
```

- [ ] **Step 3: Implement `tc/desk/paper.py`**

```python
"""The paper book (spec §10): Claude's funding decisions, filled and exited
by engine code at quoted prices, with the same caps as real money. Vetoed
proposals still fill here, flagged, because this book records Claude's
decisions, not Chris's."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tc.desk.calls import current_call_id, get_call
from tc.desk.models import DeskRefused, Instrument, utc_iso
from tc.desk.sizing import BookState, Holding
from tc.store.db import Store

MULT: dict[str, int] = {"shares": 1, "call": 100, "put": 100}
Outcome = Literal["filled", "skipped_price", "skipped_invalid", "skipped_blind", "expired"]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    call_id: int
    created_at: datetime
    instrument: Instrument
    symbol: str
    underlying: str
    quantity: int
    max_entry_price: Decimal
    atr_pct: Decimal | None
    posted_at: datetime | None
    veto_deadline: datetime | None
    message_id: str | None


class PaperPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    instrument: Instrument
    symbol: str
    underlying: str
    quantity: int
    entry_price: Decimal
    opened_at: datetime
    stop_trigger: Decimal | None
    stop_limit: Decimal | None


class ClosedTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    instrument: Instrument
    symbol: str
    pnl: Decimal
    ret_pct: Decimal


class ExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    at: datetime
    symbol: str
    proposal_id: int | None
    reason: str


def _opt_dt(v: str | None) -> datetime | None:
    return None if v is None else datetime.fromisoformat(v)


def _opt_dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def _proposal(r: sqlite3.Row) -> Proposal:
    return Proposal(
        id=r["id"], call_id=r["call_id"], created_at=datetime.fromisoformat(r["created_at"]),
        instrument=r["instrument"], symbol=r["symbol"], underlying=r["underlying"],
        quantity=r["quantity"], max_entry_price=Decimal(r["max_entry_price"]),
        atr_pct=_opt_dec(r["atr_pct"]), posted_at=_opt_dt(r["posted_at"]),
        veto_deadline=_opt_dt(r["veto_deadline"]), message_id=r["message_id"],
    )


async def ensure_book(
    store: Store, start_date: date, start_equity: Decimal, now: datetime
) -> tuple[date, Decimal]:
    await store.execute(
        "INSERT OR IGNORE INTO paper_book(id, start_date, start_equity, created_at)"
        " VALUES (1,?,?,?)",
        (start_date.isoformat(), str(start_equity), utc_iso(now)),
    )
    row = await book_row(store)
    assert row is not None
    return row


async def book_row(store: Store) -> tuple[date, Decimal] | None:
    row = await store.fetchone("SELECT start_date, start_equity FROM paper_book WHERE id=1")
    if row is None:
        return None
    return date.fromisoformat(row["start_date"]), Decimal(row["start_equity"])


async def create_proposal(
    store: Store, *, call_id: int, created_at: datetime, instrument: Instrument, symbol: str,
    underlying: str, quantity: int, max_entry_price: Decimal, atr_pct: Decimal | None,
) -> Proposal:
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO proposals(call_id, created_at, instrument, symbol, underlying, quantity,"
            " max_entry_price, atr_pct) VALUES (?,?,?,?,?,?,?,?)",
            (call_id, utc_iso(created_at), instrument, symbol, underlying, quantity,
             str(max_entry_price), None if atr_pct is None else str(atr_pct)),
        )
        pid = int(cur.lastrowid or 0)
    row = await store.fetchone("SELECT * FROM proposals WHERE id=?", (pid,))
    assert row is not None
    return _proposal(row)


async def mark_posted(
    store: Store, proposal_id: int, posted_at: datetime, veto_deadline: datetime,
    message_id: str | None,
) -> None:
    await store.execute(
        "UPDATE proposals SET posted_at=?, veto_deadline=?, message_id=? WHERE id=?",
        (utc_iso(posted_at), utc_iso(veto_deadline), message_id, proposal_id),
    )


async def unposted_proposals(store: Store) -> list[Proposal]:
    rows = await store.fetchall(
        "SELECT * FROM proposals WHERE posted_at IS NULL"
        " AND id NOT IN (SELECT proposal_id FROM proposal_outcomes) ORDER BY id"
    )
    return [_proposal(r) for r in rows]


async def pending_proposals(store: Store) -> list[Proposal]:
    rows = await store.fetchall(
        "SELECT * FROM proposals WHERE id NOT IN (SELECT proposal_id FROM proposal_outcomes)"
        " ORDER BY id"
    )
    return [_proposal(r) for r in rows]


async def record_outcome(
    store: Store, proposal_id: int, at: datetime, outcome: Outcome, *, vetoed: bool,
    approved: bool, detail: dict[str, Any],
) -> None:
    await store.execute(
        "INSERT INTO proposal_outcomes(proposal_id, at, outcome, vetoed, approved, detail_json)"
        " VALUES (?,?,?,?,?,?)",
        (proposal_id, utc_iso(at), outcome, int(vetoed), int(approved),
         json.dumps(detail, default=str, sort_keys=True)),
    )


async def record_fill(
    store: Store, proposal_id: int, at: datetime, side: Literal["buy", "sell"], quantity: int,
    price: Decimal, reason: str, stop_trigger: Decimal | None = None,
    stop_limit: Decimal | None = None,
) -> None:
    await store.execute(
        "INSERT INTO paper_fills(proposal_id, at, side, quantity, price, reason, stop_trigger,"
        " stop_limit) VALUES (?,?,?,?,?,?,?,?)",
        (proposal_id, utc_iso(at), side, quantity, str(price), reason,
         None if stop_trigger is None else str(stop_trigger),
         None if stop_limit is None else str(stop_limit)),
    )


async def open_positions(store: Store) -> list[PaperPosition]:
    rows = await store.fetchall(
        "SELECT * FROM (SELECT p.id AS proposal_id, p.call_id, p.instrument, p.symbol,"
        " p.underlying, b.at AS opened_at, b.price AS entry_price, b.stop_trigger, b.stop_limit,"
        " b.quantity - COALESCE((SELECT SUM(s.quantity) FROM paper_fills s"
        "   WHERE s.proposal_id = p.id AND s.side = 'sell'), 0) AS open_qty"
        " FROM proposals p JOIN paper_fills b ON b.proposal_id = p.id AND b.side = 'buy')"
        " WHERE open_qty > 0 ORDER BY proposal_id"
    )
    return [
        PaperPosition(
            proposal_id=r["proposal_id"], call_id=r["call_id"], instrument=r["instrument"],
            symbol=r["symbol"], underlying=r["underlying"], quantity=int(r["open_qty"]),
            entry_price=Decimal(r["entry_price"]), opened_at=datetime.fromisoformat(r["opened_at"]),
            stop_trigger=_opt_dec(r["stop_trigger"]), stop_limit=_opt_dec(r["stop_limit"]),
        )
        for r in rows
    ]


async def cash(store: Store, start_equity: Decimal) -> Decimal:
    rows = await store.fetchall(
        "SELECT f.side, f.quantity, f.price, p.instrument FROM paper_fills f"
        " JOIN proposals p ON p.id = f.proposal_id"
    )
    total = start_equity
    for r in rows:
        value = Decimal(r["price"]) * int(r["quantity"]) * MULT[r["instrument"]]
        total += value if r["side"] == "sell" else -value
    return total


async def upsert_mark(store: Store, symbol: str, price: Decimal, at: datetime) -> None:
    await store.execute(
        "INSERT INTO paper_marks(symbol, price, at) VALUES (?,?,?) ON CONFLICT(symbol)"
        " DO UPDATE SET price=excluded.price, at=excluded.at",
        (symbol, str(price), utc_iso(at)),
    )


async def marks(store: Store) -> dict[str, Decimal]:
    rows = await store.fetchall("SELECT symbol, price FROM paper_marks")
    return {r["symbol"]: Decimal(r["price"]) for r in rows}


async def closed_trades(store: Store) -> list[ClosedTrade]:
    rows = await store.fetchall(
        "SELECT p.id, p.call_id, p.instrument, p.symbol,"
        " SUM(CASE WHEN f.side='buy' THEN f.quantity ELSE 0 END) AS bq,"
        " SUM(CASE WHEN f.side='sell' THEN f.quantity ELSE 0 END) AS sq"
        " FROM proposals p JOIN paper_fills f ON f.proposal_id = p.id GROUP BY p.id"
        " HAVING bq > 0 AND bq = sq ORDER BY p.id"
    )
    out: list[ClosedTrade] = []
    for r in rows:
        fills = await store.fetchall(
            "SELECT side, quantity, price FROM paper_fills WHERE proposal_id=?", (r["id"],)
        )
        m = MULT[r["instrument"]]
        buy = sum((Decimal(f["price"]) * f["quantity"] * m for f in fills if f["side"] == "buy"), Decimal(0))
        sell = sum((Decimal(f["price"]) * f["quantity"] * m for f in fills if f["side"] == "sell"), Decimal(0))
        out.append(ClosedTrade(proposal_id=r["id"], call_id=r["call_id"],
                               instrument=r["instrument"], symbol=r["symbol"],
                               pnl=sell - buy, ret_pct=(sell - buy) / buy * 100))
    return out


async def book_state(store: Store) -> BookState:
    row = await book_row(store)
    if row is None:
        raise DeskRefused("the paper book has not started (it starts at the first PM run)")
    _, start_equity = row
    mk = await marks(store)
    holdings: list[Holding] = []
    for p in await open_positions(store):
        call = await get_call(store, await current_call_id(store, p.call_id))
        price = mk.get(p.symbol, p.entry_price)
        m = MULT[p.instrument]
        is_option = p.instrument != "shares"
        holdings.append(Holding(
            symbol=p.underlying, market_value=price * p.quantity * m,
            benchmark="SPY" if call is None else call.benchmark, is_option=is_option,
            premium_paid=p.entry_price * p.quantity * m if is_option else Decimal(0),
        ))
    c = await cash(store, start_equity)
    return BookState(
        equity=c + sum((h.market_value for h in holdings), Decimal(0)), cash=c,
        holdings=tuple(holdings), pending=len(await pending_proposals(store)),
    )


async def request_exit(
    store: Store, symbol: str, proposal_id: int | None, reason: str, at: datetime
) -> int:
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO exit_requests(at, symbol, proposal_id, reason) VALUES (?,?,?,?)",
            (utc_iso(at), symbol, proposal_id, reason),
        )
        return int(cur.lastrowid or 0)


async def pending_exit_requests(store: Store) -> list[ExitRequest]:
    rows = await store.fetchall("SELECT * FROM exit_requests WHERE done_at IS NULL ORDER BY id")
    return [
        ExitRequest(id=r["id"], at=datetime.fromisoformat(r["at"]), symbol=r["symbol"],
                    proposal_id=r["proposal_id"], reason=r["reason"])
        for r in rows
    ]


async def mark_exit_done(store: Store, request_id: int, at: datetime) -> None:
    await store.execute("UPDATE exit_requests SET done_at=? WHERE id=?", (utc_iso(at), request_id))
```

In `test_fills_drive_positions_cash_and_closed_trades`, the equity assertion
works like this:

- Cash is 3100 (3700 − 350 − 250).
- AAA is unmarked, so it counts at its entry price: 7 × 50 = 350.
- The option is marked at 3.00 × 100 = 300.

- [ ] **Step 4: Run the tests, types and lint**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_calls_paper.py && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/calls.py engine/tc/desk/paper.py tests/engine/unit/test_desk_calls_paper.py
git commit -m "desk: call rows and the paper book -- fills, positions, cash, marks, exits"
```

---

### Task 12: The scorecard, `/api/scorecard` and the weekly post

**Files:**
- Create: `engine/tc/desk/scorecard.py`
- Modify: `engine/tc/http/app.py` (`EngineState.scorecard`, route)
- Modify: `engine/tc/main.py` (`scorecard_weekly` job, state hook)
- Modify: `config.yml` (`schedule.scorecard_weekly`)
- Test: `tests/engine/unit/test_desk_scorecard.py`, plus two tests appended to `tests/engine/unit/test_http.py`

**Interfaces:**
- Consumes:
  - `resolved_rows` (Task 6).
  - `book_row`, `book_state`, `closed_trades` (Task 11).
  - `Store.bars_for`, `Store.latest_account`.
- Produces:
  - Models: `GroupStats`, `BookStats`, `Checkpoint`, `Scorecard`
  - `mean(xs) -> Decimal | None`
  - `bootstrap_ci(xs, resamples=2000, seed=20260927) -> tuple[Decimal, Decimal]`
  - `group_stats(name, rs, ci_min) -> GroupStats`
  - `checkpoint_verdict(...) -> Checkpoint`
  - `build_scorecard(store, rules, desk, today) -> Scorecard`
  - `render_scorecard(sc) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_scorecard.py`:

```python
"""Task 12: the scorecard (spec §7.3) and the pre-registered checkpoint (§8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from desk_fixtures import RULES, bar, sessions

from tc.config import DeskConfig
from tc.desk.scorecard import (
    bootstrap_ci,
    build_scorecard,
    checkpoint_verdict,
    group_stats,
    mean,
    render_scorecard,
)
from tc.desk.scoring import How, Resolution
from tc.store.db import Store

D0 = date(2026, 10, 1)


def _r(ret: str, spy: str = "0", bench: str = "0", how: How = "horizon", i: int = 1) -> Resolution:
    return Resolution(kind="call", item_id=i, ref_date=D0, ref_price=Decimal(100),
                      resolved_on=D0, how=how, exit_price=Decimal(100),
                      ret_pct=Decimal(ret), spy_ret_pct=Decimal(spy), bench_ret_pct=Decimal(bench))


def test_mean_and_a_seeded_bootstrap_are_deterministic() -> None:
    xs = [Decimal(x) for x in ("1", "-2", "3", "0.5", "4", "-1")]
    assert mean(xs) == Decimal("5.5") / 6
    assert mean([]) is None
    lo, hi = bootstrap_ci(xs)
    assert (lo, hi) == bootstrap_ci(xs)         # same seed, same interval
    m = mean(xs)
    assert m is not None and lo <= m <= hi


def test_group_stats_hides_the_interval_below_the_minimum() -> None:
    small = group_stats("pm", [_r("2", "1", how="target")], ci_min=20)
    assert (small.n, small.hit_rate_pct, small.mean_excess_spy_pct) == (1, "100.00", "1.00")
    assert small.ci95_excess_spy is None
    big = group_stats("pm", [_r(str(i % 5), "1", i=i) for i in range(25)], ci_min=20)
    assert big.ci95_excess_spy is not None


@pytest.mark.parametrize("today,resolved,pm,all_,book,spy,verdict", [
    (date(2026, 12, 30), 50, "1", "0", "5", "1", "not_yet"),     # before the date
    (date(2027, 1, 4), 39, "1", "0", "5", "1", "not_yet"),       # too few calls
    (date(2027, 1, 4), 40, "0.5", "0", "5", "1", "keep"),
    (date(2027, 1, 4), 40, "-0.5", "0", "5", "1", "stop"),       # PM subtracts value
    (date(2027, 1, 4), 40, "-0.5", "-1", "5", "1", "rework"),    # negative but beats pitches
    (date(2027, 1, 4), 40, "0.5", "0", "0", "1", "rework"),      # calls ok, book lags SPY
])
def test_the_checkpoint_rule_is_the_one_written_down(
    today: date, resolved: int, pm: str, all_: str, book: str, spy: str, verdict: str,
) -> None:
    c = checkpoint_verdict(
        today=today, checkpoint_date=date(2026, 12, 31), min_calls=40, resolved_calls=resolved,
        pm_mean_excess=Decimal(pm), all_mean_excess=Decimal(all_),
        book_ret=Decimal(book), spy_ret=Decimal(spy),
    )
    assert c.verdict == verdict


async def test_build_scorecard_splits_analysts_pm_and_legacy(desk_store: Store) -> None:
    await desk_store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
        " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
        " VALUES ('technical','2026-09-30T20:45:00+00:00','2026-10-01','AAA','up','t','[]','110','95',5,3,'XLK')"
    )
    for origin in ("pm", "legacy"):
        await desk_store.execute(
            "INSERT INTO calls(made_at, session, origin, pitch_id, extends_call_id, symbol,"
            " direction, thesis, target, invalidation, horizon_days, conviction, benchmark,"
            " ref_price, spy_ref, bench_ref, funding) VALUES ('2026-10-01T13:55:00+00:00',"
            " '2026-10-01', ?, NULL, NULL, 'AAA', 'up', 't', '110', '95', 5, 3, 'XLK',"
            " '100', '500', '200', 'none')",
            (origin,),
        )
    for kind, item in (("pitch", 1), ("call", 1), ("call", 2)):
        await desk_store.execute(
            "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
            " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
            " VALUES (?, ?, '2026-10-01', '100', '2026-10-02', 'target', '110', '10', '1', '2', 'x')",
            (kind, item),
        )
    await desk_store.upsert_bars("SPY", [bar(d, 500, 500, 500, 500) for d in sessions(D0, 3)])
    sc = await build_scorecard(desk_store, RULES, DeskConfig(), date(2026, 10, 5))
    assert [(a.name, a.n) for a in sc.analysts] == [("technical", 1)]
    assert sc.pm_calls.n == 1                        # the legacy call is excluded
    assert sc.pm_calls.mean_excess_spy_pct == "9.00"
    assert sc.selection_edge_pct == "0.00"           # PM 9 - pitches 9
    assert sc.book.start_date is None                # paper book not started
    assert sc.checkpoint.verdict == "not_yet"
    text = render_scorecard(sc)
    assert "PM calls" in text and "technical" in text and "Checkpoint" in text
```

Append to `tests/engine/unit/test_http.py`:

```python
async def test_scorecard_is_served_when_the_engine_supplies_it(tmp_path: Path, store: Store) -> None:
    async def sc() -> dict[str, Any]:
        return {"asof": "2026-10-05"}

    r = await _get(_state(tmp_path, store, scorecard=sc), "/api/scorecard")
    assert r.status_code == 200 and r.json() == {"asof": "2026-10-05"}


async def test_scorecard_is_503_on_an_engine_with_no_desk(tmp_path: Path, store: Store) -> None:
    r = await _get(_state(tmp_path, store), "/api/scorecard")
    assert r.status_code == 503
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_scorecard.py ../tests/engine/unit/test_http.py`
Expected: FAIL (`No module named 'tc.desk.scorecard'`; `EngineState` has no `scorecard`).

- [ ] **Step 2: Implement `tc/desk/scorecard.py`**

```python
"""The scorecard (spec §7.3) and the pre-registered checkpoint (§8).

Everything is computed on read from `resolutions` and the paper book; there
is no scorecard table to drift from the ledger it summarises."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.config import DeskConfig
from tc.desk.models import ANALYSTS
from tc.desk.paper import book_row, book_state, closed_trades
from tc.desk.scoring import Resolution, resolved_rows
from tc.rules.model import Rules
from tc.store.db import Store

CENT = Decimal("0.01")
Verdict = Literal["not_yet", "keep", "stop", "rework"]


class GroupStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    n: int
    hit_rate_pct: str | None
    mean_ret_pct: str | None
    mean_excess_spy_pct: str | None
    mean_excess_bench_pct: str | None
    ci95_excess_spy: list[str] | None


class BookStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str | None
    start_equity: str | None
    equity: str | None
    ret_pct: str | None
    spy_ret_pct: str | None
    closed_trades: int
    mean_trade_ret_pct: str | None
    real_account_value: str | None


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    min_calls: int
    resolved_calls: int
    verdict: Verdict
    reason: str


class Scorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asof: str
    analysts: list[GroupStats]
    all_pitches: GroupStats
    pm_calls: GroupStats
    selection_edge_pct: str | None
    book: BookStats
    checkpoint: Checkpoint
    analyst_flags: list[str]


def _q(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(CENT))


def mean(xs: Sequence[Decimal]) -> Decimal | None:
    return None if not xs else sum(xs, Decimal(0)) / len(xs)


def bootstrap_ci(
    xs: Sequence[Decimal], resamples: int = 2000, seed: int = 20260927
) -> tuple[Decimal, Decimal]:
    """Percentile bootstrap of the mean, seeded so the same ledger always
    reports the same interval."""
    rng = random.Random(seed)  # noqa: S311 -- statistics, not security
    n = len(xs)
    means = sorted(
        sum((xs[rng.randrange(n)] for _ in range(n)), Decimal(0)) / n for _ in range(resamples)
    )
    return means[int(resamples * 0.025)], means[int(resamples * 0.975) - 1]


def group_stats(name: str, rs: Sequence[Resolution], ci_min: int) -> GroupStats:
    if not rs:
        return GroupStats(name=name, n=0, hit_rate_pct=None, mean_ret_pct=None,
                          mean_excess_spy_pct=None, mean_excess_bench_pct=None,
                          ci95_excess_spy=None)
    ex = [r.excess_spy for r in rs]
    ci = None
    if len(rs) >= ci_min:
        lo, hi = bootstrap_ci(ex)
        ci = [str(lo.quantize(CENT)), str(hi.quantize(CENT))]
    return GroupStats(
        name=name, n=len(rs),
        hit_rate_pct=_q(Decimal(sum(1 for r in rs if r.hit)) / len(rs) * 100),
        mean_ret_pct=_q(mean([r.ret_pct for r in rs])),
        mean_excess_spy_pct=_q(mean(ex)),
        mean_excess_bench_pct=_q(mean([r.excess_bench for r in rs])),
        ci95_excess_spy=ci,
    )


def checkpoint_verdict(
    *, today: date, checkpoint_date: date, min_calls: int, resolved_calls: int,
    pm_mean_excess: Decimal | None, all_mean_excess: Decimal | None,
    book_ret: Decimal | None, spy_ret: Decimal | None,
) -> Checkpoint:
    def cp(v: Verdict, reason: str) -> Checkpoint:
        return Checkpoint(date=checkpoint_date.isoformat(), min_calls=min_calls,
                          resolved_calls=resolved_calls, verdict=v, reason=reason)

    if today < checkpoint_date or resolved_calls < min_calls:
        return cp("not_yet", f"{resolved_calls}/{min_calls} PM calls resolved;"
                             f" checkpoint on or after {checkpoint_date.isoformat()}")
    if pm_mean_excess is None:
        return cp("rework", "no PM excess to judge")
    if pm_mean_excess > 0 and book_ret is not None and spy_ret is not None and book_ret >= spy_ret:
        return cp("keep", "PM calls beat SPY and the paper book at least matched it")
    if pm_mean_excess < 0 and all_mean_excess is not None and pm_mean_excess < all_mean_excess:
        return cp("stop", "PM calls trail SPY and trail the average pitch: selection subtracts value")
    return cp("rework", "mixed: see the groups")


async def build_scorecard(store: Store, rules: Rules, desk: DeskConfig, today: date) -> Scorecard:
    ci_min = int(rules.get("strategy", "scorecard_ci_min_n"))
    rows = await resolved_rows(store)
    pitches = [r.resolution for r in rows if r.resolution.kind == "pitch"]
    calls = [r.resolution for r in rows
             if r.resolution.kind == "call" and r.origin in ("pitch", "pm")]
    analysts = [
        group_stats(a, [r.resolution for r in rows if r.analyst == a], ci_min) for a in ANALYSTS
    ]
    analysts = [g for g in analysts if g.n > 0]
    pm_mean, all_mean = mean([r.excess_spy for r in calls]), mean([r.excess_spy for r in pitches])
    edge = None if pm_mean is None or all_mean is None else pm_mean - all_mean
    review_min = int(rules.get("strategy", "analyst_review_min_pitches"))
    flags = [
        f"{g.name}: {g.n} resolved, mean excess vs benchmark {g.mean_excess_bench_pct}% —"
        " drop or rebuild at the checkpoint"
        for g in analysts
        if g.n >= review_min and g.mean_excess_bench_pct is not None
        and Decimal(g.mean_excess_bench_pct) < 0
    ]
    book = await _book_stats(store)
    return Scorecard(
        asof=today.isoformat(), analysts=analysts,
        all_pitches=group_stats("all pitches", pitches, ci_min),
        pm_calls=group_stats("PM calls", calls, ci_min),
        selection_edge_pct=_q(edge), book=book,
        checkpoint=checkpoint_verdict(
            today=today, checkpoint_date=desk.checkpoint_date,
            min_calls=int(rules.get("strategy", "checkpoint_min_pm_calls")),
            resolved_calls=len(calls), pm_mean_excess=pm_mean, all_mean_excess=all_mean,
            book_ret=None if book.ret_pct is None else Decimal(book.ret_pct),
            spy_ret=None if book.spy_ret_pct is None else Decimal(book.spy_ret_pct),
        ),
        analyst_flags=flags,
    )


async def _book_stats(store: Store) -> BookStats:
    acct = await store.latest_account()
    real = None if acct is None else str(acct.liquidation_value)
    row = await book_row(store)
    if row is None:
        return BookStats(start_date=None, start_equity=None, equity=None, ret_pct=None,
                         spy_ret_pct=None, closed_trades=0, mean_trade_ret_pct=None,
                         real_account_value=real)
    start_date, start_equity = row
    state = await book_state(store)
    spy = await store.bars_for("SPY", since=start_date)
    spy_ret = None if len(spy) < 2 else (spy[-1].close - spy[0].close) / spy[0].close * 100
    trades = await closed_trades(store)
    return BookStats(
        start_date=start_date.isoformat(), start_equity=str(start_equity),
        equity=_q(state.equity), ret_pct=_q((state.equity - start_equity) / start_equity * 100),
        spy_ret_pct=_q(spy_ret), closed_trades=len(trades),
        mean_trade_ret_pct=_q(mean([t.ret_pct for t in trades])), real_account_value=real,
    )


def _line(g: GroupStats) -> str:
    if g.n == 0:
        return f"{g.name}: none resolved yet"
    ci = "" if g.ci95_excess_spy is None else f" [95% {g.ci95_excess_spy[0]}..{g.ci95_excess_spy[1]}]"
    return (f"{g.name}: n={g.n} hit {g.hit_rate_pct}% | ret {g.mean_ret_pct}% | vs SPY"
            f" {g.mean_excess_spy_pct}% | vs sector {g.mean_excess_bench_pct}%{ci}")


def render_scorecard(sc: Scorecard) -> str:
    lines = [f"📊 DESK SCORECARD {sc.asof}", _line(sc.pm_calls), _line(sc.all_pitches)]
    if sc.selection_edge_pct is not None:
        lines.append(f"PM selection edge: {sc.selection_edge_pct} pts")
    lines += [_line(g) for g in sc.analysts]
    b = sc.book
    if b.start_date is not None:
        lines.append(f"Paper book: {b.equity} ({b.ret_pct}%) vs SPY {b.spy_ret_pct}% since"
                     f" {b.start_date} | {b.closed_trades} closed, mean {b.mean_trade_ret_pct}%")
    if b.real_account_value is not None:
        lines.append(f"Real account: {b.real_account_value}")
    c = sc.checkpoint
    lines.append(f"Checkpoint {c.date} ({c.min_calls} calls): {c.verdict} — {c.reason}")
    lines += [f"⚠️ {f}" for f in sc.analyst_flags]
    return "\n".join(lines)
```

- [ ] **Step 3: Serve it and post it weekly**

In `engine/tc/http/app.py`, add this field to `EngineState` after
`on_token_installed`:

```python
    # Set by the engine to a coroutine returning the desk scorecard as JSON;
    # None means this engine has no desk, and /api/scorecard answers 503.
    scorecard: Callable[[], Awaitable[dict[str, Any]]] | None = None
```

Import `Any` from `typing`. Then add this route inside `build_app`, and
list it in `routes` after `/api/ticks`:

```python
    async def api_scorecard(request: Request) -> Response:
        if state.scorecard is None:
            return JSONResponse({"detail": "no desk on this engine"}, status_code=503)
        return JSONResponse(await state.scorecard())
```

```python
        Route("/api/scorecard", api_scorecard, methods=["GET"]),
```

In `engine/tc/main.py`:
- Import `build_scorecard, render_scorecard` from `tc.desk.scorecard`.
- Add `"scorecard_weekly"` to `JOBS`.
- In `Engine.__init__`, after `self.state = EngineState(...)`, add:

```python
        self.state.scorecard = self._scorecard_json
```

- Add these methods after `_job_bars_refresh`:

```python
    async def _scorecard_json(self) -> dict[str, Any]:
        sc = await build_scorecard(
            self._store, self._rules, self._s.desk, self._et(self._clock()).date()
        )
        return sc.model_dump(mode="json")

    async def _job_scorecard_weekly(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        sc = await build_scorecard(self._store, self._rules, self._s.desk, self._et(now).date())
        await self.notifier.post(render_scorecard(sc))
        return "done", {"pm_calls": sc.pm_calls.n, "verdict": sc.checkpoint.verdict}
```

- In `_execute`, add:

```python
        if job == "scorecard_weekly":
            return await self._job_scorecard_weekly(now)
```

Add to `config.yml` `schedule:`:

```yaml
  scorecard_weekly: "at 08:30 sat"    # trading-desk design §7.3
```

- [ ] **Step 4: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/scorecard.py engine/tc/http/app.py engine/tc/main.py config.yml \
  tests/engine/unit/test_desk_scorecard.py tests/engine/unit/test_http.py
git commit -m "desk: the scorecard and the checkpoint rule, served at /api/scorecard and posted weekly"
```

---
### Task 13: The PM — calls, funding, extensions, tightenings, exits, and its tools

**Files:**
- Create: `engine/tc/desk/pm.py`
- Modify: `engine/tc/mcp/tools_desk.py` (PM half), `engine/tc/mcp/registry.py` (`PM_TOOLS`)
- Modify: `tests/engine/contract/test_mcp_no_order_tools.py` (disjointness line)
- Test: `tests/engine/unit/test_desk_pm.py`

**Interfaces:**
- Consumes: Tasks 5, 6, 9, 10, 11, 12.
- Produces:
  - `PmContext(store, broker, rules, desk, reserve, now)`
  - Models: `CallIn`, `CallOut`, `ProposalOut`, `PaperBookOut`, `PitchesOut`
  - `fresh_quotes(ctx, symbols) -> dict[str, Quote]`
  - `submit_call(ctx, cin) -> CallOut`
  - `extend_call(ctx, call_id, *, target, invalidation, horizon_days, thesis) -> CallOut`
  - `tighten_call(ctx, call_id, invalidation) -> Decimal`
  - `request_exit_for(ctx, symbol, reason) -> Literal["paper", "legacy"]`
  - `paper_book_view(ctx) -> PaperBookOut`
  - `pitches_view(ctx) -> PitchesOut`
  - Tools on `decide`: `paper_book`, `pitches_read`, `scorecard`, `option_candidates`, `call_submit`, `call_extend`, `call_tighten`, `exit_request`

**Rules this task implements:**

- **When calls may be made:** only inside a `pm` job. `pm_midday` may use
  every tool except `call_submit` (spec §4).
- **What is refused:**
  - More than 5 new calls a day. Legacy calls and extensions don't count.
  - A second open call on the same symbol and direction.
  - Adopting a pitch that is not open, or one on a different symbol or
    direction.
  - A quote older than 300 s (§4.10 stale-quote gate).
- **Funding:**
  - Shares fund up calls only. They need `max_entry_price` ≤ last × (1 +
    `max_entry_chase_pct`/100), a daily ATR ≤ 6%, and an invalidation below
    `max_entry_price`.
  - Options must be a contract `option_candidates` returns right now,
    matched with Schwab's padding ignored. Their `max_entry_price` defaults
    to ask × (1 + chase%).
  - Every funded call then passes `check_book`, with the correlated set
    measured from bars (ρ > 0.7).
- **Legacy calls:** only on a symbol held in the real account, with funding
  `none`.
- **Extensions:** only on a call that resolved by `horizon` and has an open
  paper position; once per original call. The new call is `origin="pm"`
  with `extends_call_id` set.
- **Tightening:** a new invalidation strictly between the current effective
  invalidation and the last price, on a call with an open paper position.
  Legacy stops are Chris's.
- **Exit requests:** queue exits for every paper position on the underlying.
  Otherwise, on a real legacy position, queue a recommendation for Chris.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_pm.py`:

```python
"""Task 13: the PM's calls and funding (spec §6, §9)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from desk_fixtures import RULES, desk_deps, desk_settings, trend_bars
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, Position
from tc.desk.calls import NewCall, insert_call
from tc.desk.models import DeskRefused
from tc.desk.paper import create_proposal, ensure_book, record_fill
from tc.desk.pm import CallIn, PmContext, extend_call, request_exit_for, submit_call, tighten_call
from tc.mcp import tools_desk
from tc.store.db import Store

NOW = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)      # Tue 09:50 ET
MS = int(NOW.timestamp() * 1000)
OSI = "AAA   261120C00050000"
TODAY = date(2026, 9, 29)


def _quote(last: float, bid: float | None = None, ask: float | None = None,
           age_s: int = 0) -> dict[str, Any]:
    return {"quote": {"lastPrice": last, "bidPrice": bid if bid is not None else last - 0.01,
                      "askPrice": ask if ask is not None else last + 0.01,
                      "quoteTime": MS - age_s * 1000},
            "reference": {"description": "X"}}


def _contract(kind: str, delta: float) -> dict[str, Any]:
    return {"symbol": OSI if kind == "CALL" else "AAA   261120P00050000", "putCall": kind,
            "strikePrice": 50.0, "bid": 2.40, "ask": 2.50, "last": 2.45, "delta": delta,
            "openInterest": 1200, "totalVolume": 100, "volatility": 30.0,
            "daysToExpiration": 52, "expirationDate": "2026-11-20T21:00:00.000+00:00"}


def _fx(tmp_path: Path, aaa_age: int = 0) -> Path:
    d = tmp_path / "fx"
    d.mkdir(exist_ok=True)
    (d / "quotes.json").write_text(json.dumps({
        "AAA": _quote(50, age_s=aaa_age), "SPY": _quote(500), "XLK": _quote(200),
        "CSX": _quote(46.78), OSI: _quote(2.45, 2.40, 2.50),
    }))
    (d / "chain-AAA.json").write_text(json.dumps({
        "symbol": "AAA", "underlyingPrice": 50.0,
        "callExpDateMap": {"2026-11-20:52": {"50.0": [_contract("CALL", 0.58)]}},
        "putExpDateMap": {"2026-11-20:52": {"50.0": [_contract("PUT", -0.42)]}},
    }))
    return d


def _row(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "price": Decimal(50), "adv10": Decimal(1_000_000),
            "dollar_vol": Decimal(50_000_000), "pct_from_52wk_high": Decimal(1),
            "optionable": True, "leverage": Decimal(0), "last_earnings": "", "is_etf": False,
            "session_range_pct": Decimal("1.5"), "description": symbol, "qualified": True}


async def _ctx(store: Store, tmp_path: Path, *, aaa_age: int = 0, spread: str = "1") -> PmContext:
    await store.replace_universe(date(2026, 9, 26), [_row("AAA"), _row("CSX")])
    await store.upsert_bars("AAA", trend_bars(date(2026, 6, 1), 80, first="40", step="0.125",
                                              spread=spread))
    await store.upsert_bars("SPY", trend_bars(date(2026, 6, 1), 80, first="480", step="0.25"))
    await ensure_book(store, TODAY, Decimal("3700"), NOW)
    settings = desk_settings(tmp_path)
    return PmContext(store=store, broker=FakeBroker(_fx(tmp_path, aaa_age), NOW), rules=RULES,
                     desk=settings.desk, reserve=Decimal("900.00"), now=NOW)


def _in(**kw: Any) -> CallIn:
    base: dict[str, Any] = {"symbol": "AAA", "direction": "up",
                            "thesis": "orders are accelerating into the quarter", "target": "55",
                            "invalidation": "48", "horizon_days": 10, "conviction": 3,
                            "benchmark": "XLK"}
    base.update(kw)
    return CallIn.model_validate(base)


async def _pitch(store: Store, symbol: str = "AAA") -> int:
    await store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis, evidence_json,"
        " target, invalidation, horizon_days, conviction, benchmark) VALUES ('technical',"
        " '2026-09-28T20:45:00+00:00', '2026-09-29', ?, 'up', 't', '[]', '55', '48', 10, 3, 'XLK')",
        (symbol,),
    )
    row = await store.fetchone("SELECT MAX(id) AS id FROM pitches")
    assert row is not None
    return int(row["id"])


async def test_an_unfunded_call_adopts_a_pitch_and_stamps_its_reference(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(pitch_id=await _pitch(desk_store)))
    assert (out.origin, out.ref_price, out.proposal) == ("pitch", "50.00", None)
    row = await desk_store.fetchone("SELECT spy_ref, bench_ref FROM calls WHERE id=?", (out.call_id,))
    assert row is not None and (row["spy_ref"], row["bench_ref"]) == ("500.00", "200.00")


async def test_adopting_a_pitch_on_another_symbol_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    pid = await _pitch(desk_store, "CSX")
    with pytest.raises(DeskRefused, match=f"pitch {pid} is CSX up"):
        await submit_call(ctx, _in(pitch_id=pid))


async def test_a_stale_quote_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path, aaa_age=600)
    with pytest.raises(DeskRefused, match="stale-quote gate"):
        await submit_call(ctx, _in())


async def test_the_sixth_call_of_the_day_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    for i in range(5):
        await insert_call(desk_store, NewCall(
            made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
            symbol=f"Z{i}", direction="up", thesis="t" * 12, target=Decimal(2), invalidation=Decimal(1),
            horizon_days=5, conviction=3, benchmark="SPY", ref_price=Decimal("1.5"),
            spy_ref=Decimal(500), bench_ref=Decimal(500), funding="none"))
    with pytest.raises(DeskRefused, match="today's 5 new calls"):
        await submit_call(ctx, _in())


async def test_shares_funding_sizes_by_conviction(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(funding="shares", max_entry_price="50.50"))
    assert out.proposal is not None
    assert (out.proposal.instrument, out.proposal.quantity, out.proposal.max_entry_price) == (
        "shares", 7, "50.50")                               # 370.00 // 50.50


@pytest.mark.parametrize("kw,match", [
    ({"direction": "down", "target": "45", "invalidation": "52"}, "shares fund up calls only"),
    ({"max_entry_price": "53"}, "chases more than 5"),
    ({"conviction": 2}, "scored, never funded"),
])
async def test_share_funding_refusals(
    desk_store: Store, tmp_path: Path, kw: dict[str, Any], match: str,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match=match):
        await submit_call(ctx, _in(**{"funding": "shares", "max_entry_price": "50.50", **kw}))


async def test_a_volatile_name_is_never_share_funded(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path, spread="4")
    with pytest.raises(DeskRefused, match="6% share ceiling"):
        await submit_call(ctx, _in(funding="shares", max_entry_price="50.50"))


async def test_call_submit_accepts_an_option_symbol_without_padding(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(conviction=4, funding="call",
                                     option_symbol="AAA261120C00050000"))
    assert out.proposal is not None
    assert (out.proposal.symbol, out.proposal.quantity) == (OSI, 1)   # 277.50 // 250


async def test_an_option_the_floors_did_not_offer_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match="not among the contracts"):
        await submit_call(ctx, _in(conviction=4, funding="call", option_symbol="AAA261120C00055000"))


async def test_a_legacy_call_needs_a_real_position(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match="not a position in the real account"):
        await submit_call(ctx, _in(symbol="CSX", target="50", invalidation="44", legacy=True))
    await desk_store.record_account(AccountSnapshot(
        account_hash="H", read_at=NOW, liquidation_value=Decimal("3700"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[Position(symbol="CSX", asset_type="EQUITY", quantity=4,
                            average_price=Decimal("50.375"), market_value=Decimal("187.12"),
                            day_pl=Decimal(0), settled_quantity=4)],
    ))
    out = await submit_call(ctx, _in(symbol="CSX", target="50", invalidation="44", legacy=True))
    assert out.origin == "legacy"
    assert await request_exit_for(ctx, "CSX", "thesis gone") == "legacy"


async def _held_call(store: Store, how: str | None = None) -> int:
    call = await insert_call(store, NewCall(
        made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="t" * 12, target=Decimal(55), invalidation=Decimal(48),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50),
        spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares"))
    p = await create_proposal(store, call_id=call.id, created_at=NOW, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    await record_fill(store, p.id, NOW, "buy", 7, Decimal(50), "entry", Decimal(48), Decimal("45.60"))
    if how is not None:
        await store.execute(
            "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
            " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at) VALUES ('call', ?,"
            " '2026-09-22', '50', '2026-09-28', ?, '50', '0', '0', '0', 'x')",
            (call.id, how),
        )
    return call.id


async def test_a_horizon_call_with_a_position_extends_once(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store, how="horizon")
    new = await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                            thesis="the move is intact, the clock ran out")
    with pytest.raises(DeskRefused, match="already used its one extension"):
        await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")
    with pytest.raises(DeskRefused, match="already used its one extension"):
        await extend_call(ctx, new.call_id, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")


async def test_a_target_resolved_call_does_not_extend(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store, how="target")
    with pytest.raises(DeskRefused, match="reached its horizon"):
        await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")


async def test_tightening_moves_only_toward_the_price(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store)
    assert await tighten_call(ctx, cid, "49") == Decimal(49)
    with pytest.raises(DeskRefused, match="between the current level 49"):
        await tighten_call(ctx, cid, "48.5")
    assert await request_exit_for(ctx, "AAA", "target reached in spirit") == "paper"


async def test_midday_cannot_make_calls(desk_store: Store, tmp_path: Path) -> None:
    await _ctx(desk_store, tmp_path)
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(
        server, desk_deps(desk_store, tmp_path, FakeBroker(_fx(tmp_path), NOW), NOW, "pm_midday"),
        "decide",
    )
    with pytest.raises(ToolError, match="no new calls at midday"):
        await server._tool_manager.call_tool("call_submit", {
            "symbol": "AAA", "direction": "up", "thesis": "orders are accelerating",
            "target": "55", "invalidation": "48", "horizon_days": 10, "conviction": 3,
            "benchmark": "XLK",
        })
    book = await server._tool_manager.call_tool("paper_book", {})
    assert book.started is True
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_pm.py`
Expected: FAIL (`No module named 'tc.desk.pm'`).

- [ ] **Step 2: Implement `tc/desk/pm.py`**

```python
"""The PM's side of the desk (spec §6, §9): calls, funding, extensions,
tightenings and exit requests. The engine stamps every price from a fresh
quote; the model supplies judgement and levels, never a reference price."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import Quote
from tc.clock import ET
from tc.config import DeskConfig
from tc.desk import indicators as ind
from tc.desk.calls import (
    NewCall,
    calls_made_on,
    current_call_id,
    effective_invalidation,
    get_call,
    insert_call,
    is_extended,
    open_call_for,
    tighten,
)
from tc.desk.models import ANALYSTS, Direction, DeskRefused, Funding, Instrument
from tc.desk.options import fetch_candidates, same_osi
from tc.desk.paper import (
    Proposal,
    book_row,
    book_state,
    create_proposal,
    marks,
    open_positions,
    pending_proposals,
    request_exit,
)
from tc.desk.pitches import (
    check_benchmark,
    check_horizon,
    check_levels,
    get_pitch,
    open_pitches,
    parse_price,
    tradeable_symbols,
)
from tc.desk.scorecard import mean
from tc.desk.scoring import resolution_for, resolved_rows
from tc.desk.sizing import BookState, check_book, conviction_pct, option_quantity, share_quantity
from tc.money import cents
from tc.rules.arith import cap_dollars
from tc.rules.model import Rules
from tc.store.db import Store

QUOTE_MAX_AGE_S = 300
CORR_BARS = 80
CENT = Decimal("0.01")


@dataclass(frozen=True)
class PmContext:
    store: Store
    broker: Broker
    rules: Rules
    desk: DeskConfig
    reserve: Decimal
    now: datetime

    @property
    def today(self) -> date:
        return self.now.astimezone(ET).date()


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pitch_id: int | None = None
    symbol: str = Field(min_length=1, max_length=10)
    direction: Direction
    thesis: str = Field(min_length=10, max_length=400)
    target: str
    invalidation: str
    horizon_days: int
    conviction: int = Field(ge=1, le=5)
    benchmark: str
    funding: Funding = "none"
    max_entry_price: str | None = None
    option_symbol: str | None = None
    legacy: bool = False


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    instrument: str
    symbol: str
    quantity: int
    max_entry_price: str


class CallOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    origin: str
    ref_price: str
    proposal: ProposalOut | None


@dataclass(frozen=True)
class _Plan:
    instrument: Instrument
    symbol: str
    quantity: int
    max_entry: Decimal
    atr_pct: Decimal | None


def _proposal_out(p: Proposal) -> ProposalOut:
    return ProposalOut(id=p.id, instrument=p.instrument, symbol=p.symbol, quantity=p.quantity,
                       max_entry_price=str(p.max_entry_price))


async def fresh_quotes(ctx: PmContext, symbols: Iterable[str]) -> dict[str, Quote]:
    wanted = list(dict.fromkeys(symbols))
    try:
        got = await ctx.broker.quotes(wanted)
    except BrokerUnauthorized:
        raise DeskRefused("broker blind: token absent/dead; nothing can be priced") from None
    except BrokerError as e:
        raise DeskRefused(f"quote read failed ({type(e).__name__}); try again") from None
    for s in wanted:
        q = got.get(s)
        if q is None:
            raise DeskRefused(f"no quote for {s}")
        age = (ctx.now - q.quote_time).total_seconds()
        if age > QUOTE_MAX_AGE_S:
            raise DeskRefused(
                f"the {s} quote is {int(age)}s old (§4.10 stale-quote gate); try again in a minute"
            )
    return got


async def _correlated(ctx: PmContext, symbol: str, book: BookState) -> frozenset[str]:
    threshold = ctx.rules.get("manual", "correlation_threshold")
    mine = await ctx.store.bars_for(symbol, limit=CORR_BARS)
    out: set[str] = set()
    for h in book.holdings:
        if h.symbol == symbol:
            continue
        r = ind.log_return_corr(mine, await ctx.store.bars_for(h.symbol, limit=CORR_BARS))
        if r is not None and r > threshold:
            out.add(h.symbol)
    return frozenset(out)


async def _fund(
    ctx: PmContext, cin: CallIn, symbol: str, bench: str, last: Decimal, inval: Decimal
) -> _Plan:
    rules = ctx.rules
    conviction_pct("shares" if cin.funding == "shares" else "option", cin.conviction, rules)
    chase = rules.get("strategy", "max_entry_chase_pct")
    book = await book_state(ctx.store)
    correlated = await _correlated(ctx, symbol, book)
    if cin.funding == "shares":
        if cin.direction != "up":
            raise DeskRefused("shares fund up calls only; a down call is funded with a put")
        if cin.max_entry_price is None:
            raise DeskRefused("share funding needs max_entry_price")
        max_entry = parse_price(cin.max_entry_price, "max_entry_price")
        if max_entry > last * (1 + chase / 100):
            raise DeskRefused(
                f"max_entry_price {max_entry} chases more than {chase}% above the last price {last}"
            )
        atr = ind.atr_pct(await ctx.store.bars_for(symbol, limit=CORR_BARS))
        if atr is None:
            raise DeskRefused(f"no ATR for {symbol} yet; fund it as an option or leave it unfunded")
        ceiling = rules.get("strategy", "max_daily_atr_pct")
        if atr > ceiling:
            raise DeskRefused(
                f"{symbol} daily ATR {atr.quantize(CENT)}% is over the {ceiling}% share ceiling;"
                " express it as an option or leave it unfunded"
            )
        if inval >= max_entry:
            raise DeskRefused("the invalidation must sit below max_entry_price")
        qty = share_quantity(cin.conviction, book.equity, max_entry, rules)
        check_book(book, symbol=symbol, benchmark=bench, notional=max_entry * qty,
                   is_option=False, correlated=correlated, rules=rules, reserve=ctx.reserve)
        return _Plan("shares", symbol, qty, max_entry, atr)
    want: Instrument = "call" if cin.direction == "up" else "put"
    if cin.funding != want:
        raise DeskRefused(f"a {cin.direction} call is funded with a {want}")
    if not cin.option_symbol:
        raise DeskRefused("option funding needs option_symbol: pick one from option_candidates")
    cap = cap_dollars(conviction_pct("option", cin.conviction, rules), book.equity)
    try:
        cands = await fetch_candidates(ctx.broker, symbol, cin.direction, cin.horizon_days, cap,
                                       rules, ctx.today)
    except BrokerError as e:
        raise DeskRefused(f"option chain read failed ({type(e).__name__}); try again") from None
    chosen = next((c for c in cands if same_osi(c.osi, cin.option_symbol)), None)
    if chosen is None:
        raise DeskRefused(
            f"{cin.option_symbol} is not among the contracts that clear §3.2 right now:"
            f" {[c.osi for c in cands]}"
        )
    ask = Decimal(chosen.ask)
    max_entry = (parse_price(cin.max_entry_price, "max_entry_price") if cin.max_entry_price
                 else cents(ask * (1 + chase / 100)))
    qty = option_quantity(cin.conviction, book.equity, ask, rules)
    check_book(book, symbol=symbol, benchmark=bench, notional=ask * 100 * qty, is_option=True,
               correlated=correlated, rules=rules, reserve=ctx.reserve)
    return _Plan(want, chosen.osi, qty, max_entry, None)


async def submit_call(ctx: PmContext, cin: CallIn) -> CallOut:
    symbol = cin.symbol.strip().upper()
    bench = check_benchmark(cin.benchmark)
    check_horizon(cin.horizon_days, ctx.rules)
    target = parse_price(cin.target, "target")
    inval = parse_price(cin.invalidation, "invalidation")
    if cin.legacy:
        return await _legacy_call(ctx, cin, symbol, bench, target, inval)
    if symbol not in await tradeable_symbols(ctx.store, ctx.desk):
        raise DeskRefused(f"{symbol} is not in the qualified universe or on the ETF list")
    cap = int(ctx.rules.get("strategy", "desk_max_new_calls_per_day"))
    if await calls_made_on(ctx.store, ctx.today) >= cap:
        raise DeskRefused(f"today's {cap} new calls are made; the remaining pitches are scored without you")
    existing = await open_call_for(ctx.store, symbol, cin.direction)
    if existing is not None:
        raise DeskRefused(
            f"call {existing.id} on {symbol} {cin.direction} is still open; reaffirming it is not a new call"
        )
    origin: Literal["pitch", "pm"] = "pm"
    if cin.pitch_id is not None:
        p = await get_pitch(ctx.store, cin.pitch_id)
        if p is None or p.withdrawn or await resolution_for(ctx.store, "pitch", p.id) is not None:
            raise DeskRefused(f"pitch {cin.pitch_id} is not open")
        if (p.symbol, p.direction) != (symbol, cin.direction):
            raise DeskRefused(
                f"pitch {p.id} is {p.symbol} {p.direction}; adopt it on the same symbol and"
                " direction, or originate your own call"
            )
        origin = "pitch"
    q = await fresh_quotes(ctx, [symbol, "SPY", bench])
    last = q[symbol].last
    check_levels(cin.direction, last, target, inval, ctx.rules)
    plan = None if cin.funding == "none" else await _fund(ctx, cin, symbol, bench, last, inval)
    call = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin=origin, pitch_id=cin.pitch_id,
        extends_call_id=None, symbol=symbol, direction=cin.direction, thesis=cin.thesis,
        target=target, invalidation=inval, horizon_days=cin.horizon_days,
        conviction=cin.conviction, benchmark=bench, ref_price=last, spy_ref=q["SPY"].last,
        bench_ref=q[bench].last, funding=cin.funding,
    ))
    prop = None
    if plan is not None:
        prop = await create_proposal(
            ctx.store, call_id=call.id, created_at=ctx.now, instrument=plan.instrument,
            symbol=plan.symbol, underlying=symbol, quantity=plan.quantity,
            max_entry_price=plan.max_entry, atr_pct=plan.atr_pct,
        )
    return CallOut(call_id=call.id, origin=call.origin, ref_price=str(last),
                   proposal=None if prop is None else _proposal_out(prop))


async def _legacy_call(
    ctx: PmContext, cin: CallIn, symbol: str, bench: str, target: Decimal, inval: Decimal
) -> CallOut:
    if cin.funding != "none":
        raise DeskRefused(
            'a legacy call records a view on a position already held; funding must be "none"'
        )
    acct = await ctx.store.latest_account()
    if acct is None or symbol not in {p.symbol for p in acct.positions}:
        raise DeskRefused(f"{symbol} is not a position in the real account")
    if await open_call_for(ctx.store, symbol, cin.direction, legacy=True) is not None:
        raise DeskRefused(f"{symbol} already has an open legacy call")
    q = await fresh_quotes(ctx, [symbol, "SPY", bench])
    last = q[symbol].last
    check_levels(cin.direction, last, target, inval, ctx.rules)
    call = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin="legacy", pitch_id=None, extends_call_id=None,
        symbol=symbol, direction=cin.direction, thesis=cin.thesis, target=target,
        invalidation=inval, horizon_days=cin.horizon_days, conviction=cin.conviction,
        benchmark=bench, ref_price=last, spy_ref=q["SPY"].last, bench_ref=q[bench].last,
        funding="none",
    ))
    return CallOut(call_id=call.id, origin="legacy", ref_price=str(last), proposal=None)


async def _held_by(ctx: PmContext, call_id: int) -> bool:
    for p in await open_positions(ctx.store):
        if await current_call_id(ctx.store, p.call_id) == call_id:
            return True
    return False


async def extend_call(
    ctx: PmContext, call_id: int, *, target: str, invalidation: str, horizon_days: int,
    thesis: str,
) -> CallOut:
    old = await get_call(ctx.store, call_id)
    if old is None or old.origin == "legacy":
        raise DeskRefused(f"there is no desk call {call_id}")
    if old.extends_call_id is not None or await is_extended(ctx.store, call_id):
        raise DeskRefused(f"call {call_id} has already used its one extension")
    res = await resolution_for(ctx.store, "call", call_id)
    if res is None or res.how != "horizon":
        raise DeskRefused("only a call that reached its horizon can be extended")
    if not await _held_by(ctx, call_id):
        raise DeskRefused(f"call {call_id} has no open paper position to carry")
    check_horizon(horizon_days, ctx.rules)
    t, i = parse_price(target, "target"), parse_price(invalidation, "invalidation")
    q = await fresh_quotes(ctx, [old.symbol, "SPY", old.benchmark])
    last = q[old.symbol].last
    check_levels(old.direction, last, t, i, ctx.rules)
    new = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin="pm", pitch_id=None, extends_call_id=call_id,
        symbol=old.symbol, direction=old.direction, thesis=thesis, target=t, invalidation=i,
        horizon_days=horizon_days, conviction=old.conviction, benchmark=old.benchmark,
        ref_price=last, spy_ref=q["SPY"].last, bench_ref=q[old.benchmark].last,
        funding=old.funding,
    ))
    return CallOut(call_id=new.id, origin=new.origin, ref_price=str(last), proposal=None)


async def tighten_call(ctx: PmContext, call_id: int, invalidation: str) -> Decimal:
    call = await get_call(ctx.store, call_id)
    if call is None:
        raise DeskRefused(f"there is no call {call_id}")
    if call.origin == "legacy":
        raise DeskRefused("a legacy position's stop is moved by Chris at Schwab, not by the desk")
    if await resolution_for(ctx.store, "call", call_id) is not None:
        raise DeskRefused(f"call {call_id} has resolved; tighten its extension if it has one")
    if not await _held_by(ctx, call_id):
        raise DeskRefused(f"call {call_id} has no open paper position to protect")
    new = parse_price(invalidation, "invalidation")
    cur = await effective_invalidation(ctx.store, call)
    last = (await fresh_quotes(ctx, [call.symbol]))[call.symbol].last
    ok = cur < new < last if call.direction == "up" else last < new < cur
    if not ok:
        raise DeskRefused(
            f"a tightened invalidation must sit between the current level {cur} and the last"
            f" price {last}"
        )
    await tighten(ctx.store, call_id, new, ctx.now)
    return new


async def request_exit_for(ctx: PmContext, symbol: str, reason: str) -> Literal["paper", "legacy"]:
    sym = symbol.strip().upper()
    held = [p for p in await open_positions(ctx.store) if p.underlying == sym]
    for p in held:
        await request_exit(ctx.store, sym, p.proposal_id, reason, ctx.now)
    if held:
        return "paper"
    acct = await ctx.store.latest_account()
    if acct is not None and sym in {p.symbol for p in acct.positions}:
        await request_exit(ctx.store, sym, None, reason, ctx.now)
        return "legacy"
    raise DeskRefused(f"nothing is held in {sym}")


class PositionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    symbol: str
    instrument: str
    quantity: int
    entry_price: str
    mark: str | None
    stop_trigger: str | None
    target: str
    invalidation: str
    horizon_days: int
    session: str


class LegacyView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    quantity: int
    market_value: str
    legacy_call_id: int | None


class PaperBookOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started: bool
    start_date: str | None
    equity: str | None
    cash: str | None
    open_premium: str | None
    positions: list[PositionView]
    pending: list[ProposalOut]
    legacy: list[LegacyView]


async def paper_book_view(ctx: PmContext) -> PaperBookOut:
    legacy: list[LegacyView] = []
    acct = await ctx.store.latest_account()
    for p in acct.positions if acct is not None else []:
        lc = (await open_call_for(ctx.store, p.symbol, "up", legacy=True)
              or await open_call_for(ctx.store, p.symbol, "down", legacy=True))
        legacy.append(LegacyView(symbol=p.symbol, quantity=p.quantity,
                                 market_value=str(p.market_value),
                                 legacy_call_id=None if lc is None else lc.id))
    row = await book_row(ctx.store)
    if row is None:
        return PaperBookOut(started=False, start_date=None, equity=None, cash=None,
                            open_premium=None, positions=[], pending=[], legacy=legacy)
    state = await book_state(ctx.store)
    mk = await marks(ctx.store)
    positions: list[PositionView] = []
    for p in await open_positions(ctx.store):
        call = await get_call(ctx.store, await current_call_id(ctx.store, p.call_id))
        assert call is not None
        inval = await effective_invalidation(ctx.store, call)
        mark = mk.get(p.symbol)
        positions.append(PositionView(
            proposal_id=p.proposal_id, call_id=call.id, symbol=p.symbol, instrument=p.instrument,
            quantity=p.quantity, entry_price=str(p.entry_price),
            mark=None if mark is None else str(mark),
            stop_trigger=None if p.stop_trigger is None else str(max(p.stop_trigger, inval)),
            target=str(call.target), invalidation=str(inval), horizon_days=call.horizon_days,
            session=call.session.isoformat(),
        ))
    return PaperBookOut(
        started=True, start_date=row[0].isoformat(), equity=str(state.equity.quantize(CENT)),
        cash=str(state.cash.quantize(CENT)), open_premium=str(state.open_premium.quantize(CENT)),
        positions=positions,
        pending=[_proposal_out(p) for p in await pending_proposals(ctx.store)], legacy=legacy,
    )


class PitchView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    analyst: str
    symbol: str
    direction: str
    session: str
    thesis: str
    target: str
    invalidation: str
    horizon_days: int
    conviction: int
    benchmark: str


class AnalystSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyst: str
    resolved: int
    mean_excess_spy_pct: str | None
    mean_excess_bench_pct: str | None


class PitchesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pitches: list[PitchView]
    analysts: list[AnalystSummary]


async def pitches_view(ctx: PmContext) -> PitchesOut:
    pitches = [
        PitchView(id=p.id, analyst=p.analyst, symbol=p.symbol, direction=p.direction,
                  session=p.session.isoformat(), thesis=p.thesis, target=str(p.target),
                  invalidation=str(p.invalidation), horizon_days=p.horizon_days,
                  conviction=p.conviction, benchmark=p.benchmark)
        for p in await open_pitches(ctx.store)
    ]
    rows = await resolved_rows(ctx.store)
    summaries: list[AnalystSummary] = []
    for a in ANALYSTS:
        rs = [r.resolution for r in rows if r.analyst == a]
        ex, eb = mean([r.excess_spy for r in rs]), mean([r.excess_bench for r in rs])
        summaries.append(AnalystSummary(
            analyst=a, resolved=len(rs),
            mean_excess_spy_pct=None if ex is None else str(ex.quantize(CENT)),
            mean_excess_bench_pct=None if eb is None else str(eb.quantize(CENT)),
        ))
    return PitchesOut(pitches=pitches, analysts=summaries)
```

- [ ] **Step 3: Register the PM tools**

In `engine/tc/mcp/registry.py`, add after `ANALYST_TOOLS`:

```python
# The trading desk's PM tools (trading-desk design §6, §13), on the `decide`
# role. None is order-shaped: the paper phase fills nothing at Schwab, and
# Plan 1's propose_* family is what will turn a funded call into an order.
PM_TOOLS: tuple[str, ...] = (
    "paper_book", "pitches_read", "scorecard", "option_candidates", "call_submit",
    "call_extend", "call_tighten", "exit_request",
)
```

Change the decide entry to
`"decide": COMMON_TOOLS + READ_TOOLS + DECIDE_ONLY_READ_TOOLS + PM_TOOLS + DECIDE_TOOLS,`.

In `engine/tc/mcp/tools_desk.py`:
- Replace the module's `tc.desk.*` imports with this merged, sorted set
  (the analyst-half imports are kept; the PM half's are added):

```python
from tc.desk.briefing import Briefing, build_briefing
from tc.desk.models import JOB_ANALYST, PM_JOBS, Analyst, Direction, DeskRefused, Funding
from tc.desk.options import OptionCandidate, fetch_candidates
from tc.desk.paper import book_state
from tc.desk.pitches import (
    EvidenceItem,
    PitchIn,
    last_close,
    submit_pitch,
    tradeable_symbols,
    withdraw_pitch,
)
from tc.desk.pm import (
    CallIn,
    CallOut,
    PaperBookOut,
    PitchesOut,
    PmContext,
    extend_call,
    paper_book_view,
    pitches_view,
    request_exit_for,
    submit_call,
    tighten_call,
)
from tc.desk.scorecard import Scorecard, build_scorecard
from tc.desk.scoring import RecordRow, analyst_record
from tc.desk.sizing import conviction_pct
from tc.mcp.registry import Role
from tc.rules.arith import cap_dollars
```

- Make `register` dispatch both halves:

```python
def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    if role == "research":
        _register_analyst(server, deps)
    elif role == "decide":
        _register_pm(server, deps)
```

- Add the PM half:

```python
class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premium_cap: str
    rows: list[OptionCandidate]


class Tightened(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    invalidation: str


class ExitQueued(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    book: str


def _pm(deps: McpDeps, *, making_calls: bool = False) -> PmContext:
    name = deps.active.name or ""
    if name not in PM_JOBS:
        raise ToolError(
            "no PM job is running: the desk's PM tools answer only inside a scheduled PM run"
        )
    if making_calls and name != "pm":
        raise ToolError("no new calls at midday: exit, tighten or hold only (spec §4)")
    return PmContext(store=deps.store, broker=deps.broker, rules=deps.rules,
                     desk=deps.settings.desk, reserve=deps.settings.engine.reserve_usd,
                     now=deps.clock())


def _register_pm(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(name="paper_book", description=(
        "The desk's paper book: equity, cash, open premium, positions with their call's"
        " target/invalidation/horizon, pending proposals, and the real account's legacy"
        " positions with any legacy call attached."))
    async def paper_book() -> PaperBookOut:
        return await paper_book_view(_pm(deps))

    @server.tool(name="pitches_read", description=(
        "Every open pitch from every analyst, plus each analyst's resolved count and mean"
        " excess return."))
    async def pitches_read() -> PitchesOut:
        return await pitches_view(_pm(deps))

    @server.tool(name="scorecard", description="The desk scorecard (spec §7.3) and checkpoint status.")
    async def scorecard() -> Scorecard:
        ctx = _pm(deps)
        return await build_scorecard(ctx.store, ctx.rules, ctx.desk, ctx.today)

    @server.tool(name="option_candidates", description=(
        "Up to 3 live contracts that clear every manual §3.2 floor for this direction and"
        " horizon, within this conviction's premium cap. Fund an option call ONLY with one"
        " of these symbols."))
    async def option_candidates(symbol: str, direction: Direction, horizon_days: int,
                                conviction: Annotated[int, Field(ge=1, le=5)]) -> Candidates:
        ctx = _pm(deps)
        with refusals():
            cap = cap_dollars(conviction_pct("option", conviction, ctx.rules),
                              (await book_state(ctx.store)).equity)
            rows = await fetch_candidates(ctx.broker, symbol.strip().upper(), direction,
                                          horizon_days, cap, ctx.rules, ctx.today)
        return Candidates(premium_cap=str(cap), rows=rows)

    @server.tool(name="call_submit", description=(
        "Make one call (max 5 new a day): adopt a pitch by pitch_id or originate your own."
        " Prices are decimal strings. funding: none | shares (up only; needs"
        " max_entry_price) | call | put (needs option_symbol from option_candidates)."
        " Conviction 1-2 is never funded. legacy=true records a view on a real-account"
        " position (funding none). The engine stamps the reference from a live quote."))
    async def call_submit(
        symbol: Annotated[str, Field(max_length=10)], direction: Direction,
        thesis: Annotated[str, Field(min_length=10, max_length=400)], target: str,
        invalidation: str, horizon_days: int, conviction: Annotated[int, Field(ge=1, le=5)],
        benchmark: str, pitch_id: int | None = None, funding: Funding = "none",
        max_entry_price: str | None = None, option_symbol: str | None = None,
        legacy: bool = False,
    ) -> CallOut:
        ctx = _pm(deps, making_calls=True)
        with refusals():
            return await submit_call(ctx, CallIn(
                pitch_id=pitch_id, symbol=symbol, direction=direction, thesis=thesis,
                target=target, invalidation=invalidation, horizon_days=horizon_days,
                conviction=conviction, benchmark=benchmark, funding=funding,
                max_entry_price=max_entry_price, option_symbol=option_symbol, legacy=legacy,
            ))

    @server.tool(name="call_extend", description=(
        "Extend a funded call that reached its horizon at the last close, ONCE: opens a new"
        " call with new levels and horizon and keeps the paper position."))
    async def call_extend(call_id: int, target: str, invalidation: str, horizon_days: int,
                          thesis: Annotated[str, Field(min_length=10, max_length=400)]) -> CallOut:
        ctx = _pm(deps)
        with refusals():
            return await extend_call(ctx, call_id, target=target, invalidation=invalidation,
                                     horizon_days=horizon_days, thesis=thesis)

    @server.tool(name="call_tighten", description=(
        "Move a held call's invalidation toward the price (never away). The paper stop follows."))
    async def call_tighten(call_id: int, invalidation: str) -> Tightened:
        ctx = _pm(deps)
        with refusals():
            new = await tighten_call(ctx, call_id, invalidation)
        return Tightened(call_id=call_id, invalidation=str(new))

    @server.tool(name="exit_request", description=(
        "Exit every paper position on this underlying at the next desk_watch; on a legacy"
        " real-account position, posts a recommendation for Chris instead."))
    async def exit_request(symbol: str,
                           reason: Annotated[str, Field(min_length=5, max_length=300)]) -> ExitQueued:
        ctx = _pm(deps)
        with refusals():
            where = await request_exit_for(ctx, symbol, reason)
        return ExitQueued(symbol=symbol.strip().upper(), book=where)
```

- [ ] **Step 4: Keep the contract test honest**

In `test_the_two_roles_have_disjoint_write_surfaces`, add:

```python
    assert "call_submit" in decide and "call_submit" not in research
```

- [ ] **Step 5: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/tc/desk/pm.py engine/tc/mcp/tools_desk.py engine/tc/mcp/registry.py \
  tests/engine/contract/test_mcp_no_order_tools.py tests/engine/unit/test_desk_pm.py
git commit -m "desk: the PM's calls, funding and exits -- every price stamped, every cap in code"
```

---
### Task 14: Discord message ids and reading the veto

**Files:**
- Modify: `engine/tc/notify.py` (`_send`, `post_message`)
- Modify: `engine/tc/config.py` (`Secrets.discord_approver_id`, passthrough)
- Create: `engine/tc/desk/approval.py`
- Test: `tests/engine/unit/test_desk_approval.py`, plus tests appended to `tests/engine/unit/test_notify.py`

**Interfaces:**
- Produces:
  - `Notifier.post_message(text) -> str | None`: the Discord message id, or
    `None` on any failure or with no target.
  - `Settings.discord_approver_id: str | None`.
  - `Reaction(veto: bool, approve: bool, unreadable: bool = False)`.
  - `Reactions`, a Protocol with `async read(message_id) -> Reaction`.
  - `NoReactions`, which always answers "nobody reacted".
  - `DiscordReactions(channel: BotChannel, client, approver_id)`.
  - Constants `VETO = "❌"`, `APPROVE = "✅"`.

**Why REST and no gateway:** Discord serves who reacted with an emoji
through `GET /channels/{c}/messages/{m}/reactions/{emoji}`, one call carrying
the bot token. `desk_watch` asks at the deadline, so nothing needs to stay
connected.

- **Counting:** a reaction counts only from a non-bot user. When
  `TC_DISCORD_APPROVER_ID` is set, only that user counts.
- **Paper phase:** a read that fails is `unreadable`, and the proposal
  executes. That is acceptable only on paper. Revised Plan 1 must refuse to
  place a real order on an unreadable veto; spec §11 records that
  requirement.

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/unit/test_notify.py`:

```python
async def test_post_message_returns_the_bot_message_id() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "987654321", "content": "x"})

    assert await Notifier(BotChannel("t", "1"), _client(h)).post_message("hi") == "987654321"


async def test_post_message_asks_a_webhook_to_wait_for_the_message() -> None:
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"id": "42"})

    assert await Notifier("https://discord.test/hook", _client(h)).post_message("hi") == "42"
    assert seen["url"].endswith("?wait=true")


async def test_post_message_is_none_on_failure_and_without_a_target() -> None:
    assert await Notifier(BotChannel("t", "1"), _client(lambda r: httpx.Response(500))).post_message("x") is None
    assert await Notifier(None, _client(lambda r: httpx.Response(200))).post_message("x") is None
```

Create `tests/engine/unit/test_desk_approval.py`:

```python
"""Task 14: reading ✅/❌ off a proposal's Discord message."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx

from tc.desk.approval import APPROVE, VETO, DiscordReactions, NoReactions, Reaction
from tc.notify import BotChannel


def _client(by_emoji: dict[str, Any], status: int = 200) -> httpx.AsyncClient:
    def h(req: httpx.Request) -> httpx.Response:
        emoji = unquote(req.url.path.rsplit("/", 1)[1])
        assert req.headers["authorization"] == "Bot tok"
        if emoji not in by_emoji:
            return httpx.Response(404, json={"message": "Unknown Emoji"})
        return httpx.Response(status, json=by_emoji[emoji])

    return httpx.AsyncClient(transport=httpx.MockTransport(h))


CH = BotChannel("tok", "555")


async def test_a_human_veto_counts_and_the_bot_s_own_reaction_does_not() -> None:
    r = DiscordReactions(CH, _client({VETO: [{"id": "1", "bot": False}],
                                      APPROVE: [{"id": "2", "bot": True}]}), None)
    assert await r.read("m") == Reaction(veto=True, approve=False)


async def test_with_an_approver_set_only_that_user_counts() -> None:
    r = DiscordReactions(CH, _client({VETO: [{"id": "1"}], APPROVE: [{"id": "7"}]}), "7")
    assert await r.read("m") == Reaction(veto=False, approve=True)


async def test_no_reactions_at_all_is_not_unreadable() -> None:
    assert await DiscordReactions(CH, _client({}), None).read("m") == Reaction(False, False)


async def test_a_failed_read_is_unreadable_not_a_silent_no() -> None:
    got = await DiscordReactions(CH, _client({VETO: []}, status=500), None).read("m")
    assert got.unreadable is True


async def test_no_reactions_reader() -> None:
    assert await NoReactions().read("m") == Reaction(False, False)
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_approval.py ../tests/engine/unit/test_notify.py`
Expected: FAIL (`Notifier` has no `post_message`; no module `tc.desk.approval`).

- [ ] **Step 2: Implement `post_message`**

In `engine/tc/notify.py`, replace `Notifier.post` with a shared `_send`
plus the two public methods:

```python
    async def _send(self, text: str, *, wait: bool) -> httpx.Response | None:
        if self._target is None:
            return None
        text = f"{self._prefix}{text}"
        if len(text) > DISCORD_MAX:
            text = text[: DISCORD_MAX - 1] + "…"
        params: dict[str, str] = {}
        if isinstance(self._target, BotChannel):
            url = self._target.url
            headers = {"Authorization": f"Bot {self._target.token}"}
        else:
            url, headers = self._target, {}
            if wait:
                # A webhook answers 204 with no body unless asked to wait; the
                # message id is only in the body.
                params = {"wait": "true"}
        try:
            r = await self._c.post(
                url, json={"content": text}, headers=headers, params=params, timeout=10
            )
        except httpx.HTTPError as e:
            log.warning("discord post failed: %s", type(e).__name__)
            return None
        if not 200 <= r.status_code < 300:
            # Status only. A revoked bot token answers 401 and an operator
            # needs to see that, but this line is exactly where the token
            # would leak if the request were logged instead.
            log.warning("discord post rejected: HTTP %d", r.status_code)
            return None
        return r

    async def post(self, text: str) -> bool:
        return await self._send(text, wait=False) is not None

    async def post_message(self, text: str) -> str | None:
        """Post and return the message's id -- a proposal's veto is read off
        its own message. None on any failure; the caller treats that as
        'nobody can react to this', never as an approval."""
        r = await self._send(text, wait=True)
        if r is None:
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        mid = body.get("id") if isinstance(body, dict) else None
        return None if mid is None else str(mid)
```

The warning texts are unchanged except that a transport failure now logs
the exception's **class name** rather than its message, which is strictly
less revealing. No existing test asserts the old transport-failure text
(checked 2026-09-27), and `test_notifier_logs_a_rejection_without_the_token`
covers the rejection path, whose text is unchanged.

In `engine/tc/config.py`, add this to `Secrets`:

```python
    # The Discord user whose ✅/❌ on a desk proposal counts. None: any
    # non-bot user in the channel (the paper phase). Revised Plan 1 requires it.
    discord_approver_id: str | None = None
```

Then add the passthrough to `Settings`:

```python
    @property
    def discord_approver_id(self) -> str | None:
        return self.secrets.discord_approver_id
```

- [ ] **Step 3: Implement `tc/desk/approval.py`**

```python
"""Who reacted to a proposal (spec §9.4): ❌ vetoes, ✅ executes at once.

REST, not the gateway: Discord serves the users behind one emoji on one
message through a plain GET with the bot token, and the desk only needs the
answer at a proposal's deadline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from tc.notify import DISCORD_API, BotChannel

log = logging.getLogger(__name__)
VETO = "❌"
APPROVE = "✅"


@dataclass(frozen=True)
class Reaction:
    veto: bool
    approve: bool
    unreadable: bool = False


class Reactions(Protocol):
    async def read(self, message_id: str) -> Reaction: ...


class NoReactions:
    """An engine with no bot channel: nobody can react, so nothing is vetoed
    and nothing is approved early -- a proposal simply runs at its deadline."""

    async def read(self, message_id: str) -> Reaction:
        return Reaction(veto=False, approve=False)


class DiscordReactions:
    def __init__(
        self, channel: BotChannel, client: httpx.AsyncClient, approver_id: str | None
    ) -> None:
        self._ch = channel
        self._c = client
        self._approver = approver_id

    async def _users(self, message_id: str, emoji: str) -> list[dict[str, Any]] | None:
        url = (
            f"{DISCORD_API}/channels/{self._ch.channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji)}"
        )
        try:
            r = await self._c.get(url, headers={"Authorization": f"Bot {self._ch.token}"},
                                  timeout=10)
        except httpx.HTTPError as e:
            log.warning("discord reactions read failed: %s", type(e).__name__)
            return None
        if r.status_code == 404:
            return []          # nobody has reacted with this emoji
        if not 200 <= r.status_code < 300:
            log.warning("discord reactions read rejected: HTTP %d", r.status_code)
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        return body if isinstance(body, list) else None

    def _counts(self, users: list[dict[str, Any]]) -> bool:
        return any(
            not u.get("bot") and (self._approver is None or str(u.get("id")) == self._approver)
            for u in users
        )

    async def read(self, message_id: str) -> Reaction:
        vetoes = await self._users(message_id, VETO)
        approvals = await self._users(message_id, APPROVE)
        if vetoes is None or approvals is None:
            return Reaction(veto=False, approve=False, unreadable=True)
        return Reaction(veto=self._counts(vetoes), approve=self._counts(approvals))
```

- [ ] **Step 4: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/notify.py engine/tc/config.py engine/tc/desk/approval.py \
  tests/engine/unit/test_notify.py tests/engine/unit/test_desk_approval.py
git commit -m "desk: a proposal's message id, and the ✅/❌ read off it over REST"
```

---

### Task 15: `desk_watch` — paper entries at the veto deadline, paper exits

**Files:**
- Create: `engine/tc/desk/watch.py`
- Modify: `engine/tc/main.py` (`desk_watch` job, reactions wiring)
- Modify: `config.yml` (`schedule.desk_watch`)
- Test: `tests/engine/unit/test_desk_watch.py`

**Interfaces:**
- Consumes: Tasks 9–14.
- Produces:
  - `WatchReport(filled, skipped, exits, recommended, blind)`
  - `run_desk_watch(*, store, broker, notifier, reactions, rules, desk, now) -> WatchReport`
  - The engine job `desk_watch`.

**Behaviour, in order, on each run:**

1. **Entries.** For each posted proposal with no outcome:
   - At or after 15:55 ET it is `expired` (`CLAUDE.md §4.2`).
   - It becomes due when ✅ is present or its veto deadline has passed.
   - A due proposal is quoted:
     - A dead token → `skipped_blind`.
     - No ask, or ask > `max_entry_price` → `skipped_price`.
     - Shares whose ask is at or below the effective invalidation →
       `skipped_invalid`.
     - Otherwise it fills at the ask. Shares get
       `entry_stop(ask, atr_pct, effective invalidation)`. The outcome
       records `vetoed` and `approved`.
   - A transient broker error leaves it pending for the next run.
2. **Exits.** For each open paper position, quote the underlying and the
   traded symbol, record the mark, then take the first reason that holds:

   | Instrument | Reasons, in priority order |
   |---|---|
   | Shares | `stop` (last ≤ max(trigger, tightened invalidation): fill at the trigger, or at the bid if last < the limit) → `target` (last ≥ target, at the bid) |
   | Options | `invalidation` / `target` on the underlying, at the bid → `dte_close` (calendar days to expiry ≤ `option_close_at_dte`) |

   After those:
   - `call_resolved`: the current call has a resolution, the time is ≥
     10:00 ET, and it was not extended today.
   - `pm_exit`: an exit request is pending for this proposal.
3. **Legacy.** A pending exit request with no proposal posts one 📌
   recommendation for Chris, then is marked done. Paper requests whose
   position is already closed are also marked done.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_watch.py`:

```python
"""Task 15: desk_watch fills paper entries at the veto deadline and runs the
paper exits (spec §9.4, §9.5, §10)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from desk_fixtures import RULES, trend_bars

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker
from tc.broker.models import Quote
from tc.config import DeskConfig
from tc.desk.approval import Reaction
from tc.desk.calls import NewCall, insert_call, tighten
from tc.desk.models import Direction, Funding
from tc.desk.paper import (
    create_proposal,
    ensure_book,
    mark_posted,
    open_positions,
    pending_proposals,
    record_fill,
    request_exit,
)
from tc.desk.watch import run_desk_watch
from tc.notify import Notifier
from tc.store.db import Store

T0 = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)          # 10:00 ET
TODAY = date(2026, 9, 29)
OSI = "AAA   261120C00050000"


class Notes(Notifier):
    def __init__(self) -> None:
        super().__init__(None, httpx.AsyncClient())
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


class Fixed:
    def __init__(self, r: Reaction) -> None:
        self.r = r

    async def read(self, message_id: str) -> Reaction:
        return self.r


def _quotes(d: Path, at: datetime, **px: tuple[float, float, float]) -> None:
    """px: SYMBOL=(last, bid, ask)."""
    ms = int(at.timestamp() * 1000)
    body = {s.replace("_", " "): {"quote": {"lastPrice": last, "bidPrice": bid, "askPrice": ask,
                                            "quoteTime": ms}} for s, (last, bid, ask) in px.items()}
    (d / "quotes.json").write_text(json.dumps(body))


async def _setup(store: Store, *, funding: Funding = "shares", direction: Direction = "up",
                 target: str = "55", inval: str = "48") -> tuple[int, int]:
    await ensure_book(store, TODAY, Decimal("3700"), T0)
    await store.upsert_bars("AAA", trend_bars(date(2026, 6, 1), 80, first="40", step="0.125"))
    call = await insert_call(store, NewCall(
        made_at=T0, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None, symbol="AAA",
        direction=direction, thesis="t" * 12, target=Decimal(target), invalidation=Decimal(inval),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50),
        spy_ref=Decimal(500), bench_ref=Decimal(200), funding=funding))
    shares = funding == "shares"
    p = await create_proposal(store, call_id=call.id, created_at=T0,
                              instrument="shares" if shares else "call",
                              symbol="AAA" if shares else OSI, underlying="AAA",
                              quantity=7 if shares else 1,
                              max_entry_price=Decimal("50.50") if shares else Decimal("2.60"),
                              atr_pct=Decimal(2) if shares else None)
    await mark_posted(store, p.id, T0, T0 + timedelta(minutes=10), "m1")
    return call.id, p.id


NOBODY = Reaction(veto=False, approve=False)


async def _run(store: Store, fx: Path, now: datetime, r: Reaction = NOBODY,
               notes: Notes | None = None) -> Any:
    return await run_desk_watch(store=store, broker=FakeBroker(fx, now), notifier=notes or Notes(),
                                reactions=Fixed(r), rules=RULES, desk=DeskConfig(), now=now)


async def test_a_proposal_waits_for_its_window_then_fills_at_the_ask(
    desk_store: Store, tmp_path: Path,
) -> None:
    _, pid = await _setup(desk_store)
    _quotes(tmp_path, T0 + timedelta(minutes=5), AAA=(50.0, 49.98, 50.02))
    assert (await _run(desk_store, tmp_path, T0 + timedelta(minutes=5))).filled == []
    _quotes(tmp_path, T0 + timedelta(minutes=10), AAA=(50.0, 49.98, 50.02))
    rep = await _run(desk_store, tmp_path, T0 + timedelta(minutes=10))
    assert rep.filled == [pid]
    [pos] = await open_positions(desk_store)
    assert (pos.entry_price, pos.quantity) == (Decimal("50.02"), 7)
    assert pos.stop_trigger == Decimal("48.00")          # the invalidation beat the 8% formula
    assert await pending_proposals(desk_store) == []


async def test_approve_fills_early_and_a_veto_still_fills_flagged(
    desk_store: Store, tmp_path: Path,
) -> None:
    _, pid = await _setup(desk_store)
    now = T0 + timedelta(minutes=2)
    _quotes(tmp_path, now, AAA=(50.0, 49.98, 50.02))
    notes = Notes()
    rep = await _run(desk_store, tmp_path, now, Reaction(veto=True, approve=True), notes)
    assert rep.filled == [pid]
    row = await desk_store.fetchone("SELECT vetoed, approved FROM proposal_outcomes")
    assert row is not None and (row["vetoed"], row["approved"]) == (1, 1)
    assert "vetoed" in notes.posts[0]


async def test_a_price_that_ran_past_max_entry_is_skipped(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(50.9, 50.8, 50.95))
    rep = await _run(desk_store, tmp_path, now)
    assert rep.filled == [] and rep.skipped[0][1] == "skipped_price"


async def test_an_ask_already_below_the_invalidation_is_skipped(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    now = T0 + timedelta(minutes=10)
    _quotes(tmp_path, now, AAA=(47.9, 47.8, 47.95))
    assert (await _run(desk_store, tmp_path, now)).skipped[0][1] == "skipped_invalid"


async def test_pending_entries_expire_at_15_55(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)
    late = datetime(2026, 9, 29, 19, 55, tzinfo=UTC)      # 15:55 ET
    _quotes(tmp_path, late, AAA=(50.0, 49.98, 50.02))
    assert (await _run(desk_store, tmp_path, late)).skipped[0][1] == "expired"


async def _held(store: Store, fx: Path, **kw: Any) -> int:
    _, pid = await _setup(store, **kw)
    now = T0 + timedelta(minutes=10)
    if kw.get("funding", "shares") == "shares":
        _quotes(fx, now, AAA=(50.0, 49.98, 50.02))
    else:
        _quotes(fx, now, AAA=(50.0, 49.98, 50.02), **{OSI.replace(" ", "_"): (2.45, 2.40, 2.50)})
    await _run(store, fx, now)
    return pid


async def test_a_share_stop_fills_at_the_trigger_or_at_the_bid_below_the_limit(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(47.5, 47.45, 47.55))       # through 48.00, above the 45.60 limit
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits and rep.exits[0][1] == "stop"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "48.00"


async def test_a_gap_below_the_limit_fills_at_the_bid(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(44.0, 43.9, 44.1))
    await _run(desk_store, tmp_path, now)
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "43.90"


async def test_a_tightened_invalidation_raises_the_paper_stop(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await tighten(desk_store, cid_row["id"], Decimal("49.5"), T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(49.4, 49.38, 49.42))
    assert (await _run(desk_store, tmp_path, now)).exits[0][1] == "stop"


async def test_the_target_exits_at_the_bid(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(55.1, 55.0, 55.2))
    assert (await _run(desk_store, tmp_path, now)).exits[0][1] == "target"


async def test_an_option_exits_when_its_underlying_crosses_the_invalidation(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _held(desk_store, tmp_path, funding="call")
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(47.9, 47.8, 48.0), **{OSI.replace(" ", "_"): (1.2, 1.1, 1.3)})
    rep = await _run(desk_store, tmp_path, now)
    assert rep.exits[0][1] == "invalidation"
    fill = await desk_store.fetchone("SELECT price FROM paper_fills WHERE side='sell'")
    assert fill is not None and fill["price"] == "1.10"          # the option's bid


async def test_an_option_closes_at_five_days_to_expiry(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path, funding="call")
    near = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)             # 4 days before 11/20
    _quotes(tmp_path, near, AAA=(50.0, 49.98, 50.02), **{OSI.replace(" ", "_"): (2.0, 1.95, 2.05)})
    assert (await _run(desk_store, tmp_path, near)).exits[0][1] == "dte_close"


async def test_a_resolved_call_exits_after_ten_not_before(desk_store: Store, tmp_path: Path) -> None:
    await _held(desk_store, tmp_path)
    cid_row = await desk_store.fetchone("SELECT id FROM calls")
    assert cid_row is not None
    await desk_store.execute(
        "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how, exit_price,"
        " ret_pct, spy_ret_pct, bench_ret_pct, written_at) VALUES ('call', ?, '2026-09-22', '50',"
        " '2026-09-28', 'horizon', '50', '0', '0', '0', 'x')", (cid_row["id"],))
    early = datetime(2026, 9, 30, 13, 55, tzinfo=UTC)      # 09:55 ET next day
    _quotes(tmp_path, early, AAA=(50.5, 50.4, 50.6))
    assert (await _run(desk_store, tmp_path, early)).exits == []
    later = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)       # 10:00 ET
    _quotes(tmp_path, later, AAA=(50.5, 50.4, 50.6))
    assert (await _run(desk_store, tmp_path, later)).exits[0][1] == "call_resolved"


async def test_pm_exits_and_legacy_recommendations(desk_store: Store, tmp_path: Path) -> None:
    pid = await _held(desk_store, tmp_path)
    await request_exit(desk_store, "AAA", pid, "thesis done", T0)
    await request_exit(desk_store, "CSX", None, "legacy: momentum broke", T0)
    now = T0 + timedelta(minutes=20)
    _quotes(tmp_path, now, AAA=(51.0, 50.9, 51.1))
    notes = Notes()
    rep = await _run(desk_store, tmp_path, now, notes=notes)
    assert rep.exits[0][1] == "pm_exit" and rep.recommended == ["CSX"]
    assert any("CSX" in p and "Schwab" in p for p in notes.posts)


async def test_a_dead_token_is_reported_not_raised(desk_store: Store, tmp_path: Path) -> None:
    await _setup(desk_store)

    class Dead(FakeBroker):
        async def quotes(self, symbols: Any) -> dict[str, Quote]:
            raise BrokerUnauthorized("401")

    now = T0 + timedelta(minutes=10)
    rep = await run_desk_watch(store=desk_store, broker=Dead(tmp_path, now), notifier=Notes(),
                               reactions=Fixed(Reaction(False, False)), rules=RULES,
                               desk=DeskConfig(), now=now)
    assert rep.blind is True and rep.skipped[0][1] == "skipped_blind"
```

In `_quotes`, keys use `_` for the OSI's spaces only because a keyword
argument cannot contain spaces. `OSI.replace(" ", "_")` goes in and the
helper turns underscores back into spaces. No real ticker in these tests
contains an underscore.

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_watch.py`
Expected: FAIL (`No module named 'tc.desk.watch'`).

- [ ] **Step 2: Implement `tc/desk/watch.py`**

```python
"""desk_watch (spec §9.4, §9.5, §10): every five minutes in the session,
fill paper entries whose veto window has closed, and run the paper book's
exits. Engine code; the model is not involved."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_CEILING, Decimal

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import Quote
from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.approval import Reaction, Reactions
from tc.desk.calls import current_call_id, effective_invalidation, get_call
from tc.desk.options import osi_expiry
from tc.desk.paper import (
    PaperPosition,
    Proposal,
    mark_exit_done,
    open_positions,
    pending_exit_requests,
    pending_proposals,
    record_fill,
    record_outcome,
    upsert_mark,
)
from tc.desk.scoring import resolution_for
from tc.desk.sizing import SizingRefused, entry_stop
from tc.money import CENT
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store

ENTRY_CUTOFF = time(15, 55)
RESOLVED_EXIT_FROM = time(10, 0)


@dataclass
class WatchReport:
    filled: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    exits: list[tuple[int, str]] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    blind: bool = False


async def _quote(broker: Broker, symbols: list[str]) -> dict[str, Quote]:
    return await broker.quotes(sorted(set(symbols)))


async def _entries(
    store: Store, broker: Broker, notifier: Notifier, reactions: Reactions, rules: Rules,
    now: datetime, rep: WatchReport,
) -> None:
    et = now.astimezone(ET)
    for p in await pending_proposals(store):
        if p.posted_at is None:
            continue
        if et.time() >= ENTRY_CUTOFF:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "unfilled at 15:55 (CLAUDE.md §4.2)"})
            rep.skipped.append((p.id, "expired"))
            continue
        r = await reactions.read(p.message_id) if p.message_id else Reaction(False, False)
        due = r.approve or (p.veto_deadline is not None and now >= p.veto_deadline)
        if not due:
            continue
        try:
            q = (await _quote(broker, [p.symbol])).get(p.symbol)
        except BrokerUnauthorized:
            await record_outcome(store, p.id, now, "skipped_blind", vetoed=r.veto,
                                 approved=r.approve, detail={})
            rep.skipped.append((p.id, "skipped_blind"))
            rep.blind = True
            continue
        except BrokerError:
            continue            # transient: the next run tries again
        await _fill_entry(store, notifier, rules, p, q, r, now, rep)


async def _fill_entry(
    store: Store, notifier: Notifier, rules: Rules, p: Proposal, q: Quote | None, r: Reaction,
    now: datetime, rep: WatchReport,
) -> None:
    detail = {"unreadable": r.unreadable}
    if q is None or q.ask <= 0 or q.ask > p.max_entry_price:
        await record_outcome(store, p.id, now, "skipped_price", vetoed=r.veto, approved=r.approve,
                             detail={**detail, "ask": None if q is None else str(q.ask)})
        rep.skipped.append((p.id, "skipped_price"))
        return
    trigger = limit = None
    if p.instrument == "shares":
        call = await get_call(store, await current_call_id(store, p.call_id))
        assert call is not None
        inval = await effective_invalidation(store, call)
        try:
            if q.ask <= inval:
                raise SizingRefused("the ask is at or below the invalidation")
            stop = entry_stop(q.ask, p.atr_pct or Decimal(0), inval, rules)
        except SizingRefused as e:
            await record_outcome(store, p.id, now, "skipped_invalid", vetoed=r.veto,
                                 approved=r.approve, detail={**detail, "reason": str(e)})
            rep.skipped.append((p.id, "skipped_invalid"))
            return
        trigger, limit = stop.trigger, stop.limit
    await record_fill(store, p.id, now, "buy", p.quantity, q.ask, "entry", trigger, limit)
    await record_outcome(store, p.id, now, "filled", vetoed=r.veto, approved=r.approve,
                         detail=detail)
    rep.filled.append(p.id)
    note = " — ❌ vetoed: kept in the paper book as Claude's decision" if r.veto else ""
    stop_txt = "" if trigger is None else f", stop {trigger}/{limit}"
    await notifier.post(f"📄 PAPER BUY {p.quantity} {p.symbol} @ {q.ask}{stop_txt}{note}")


def _exit_reason(
    pos: PaperPosition, up: bool, target: Decimal, inval: Decimal, u: Quote, t: Quote,
    rules: Rules, today_et: datetime,
) -> tuple[str, Decimal] | None:
    if pos.instrument == "shares":
        thesis = inval.quantize(CENT, rounding=ROUND_CEILING)
        trigger = max(pos.stop_trigger or Decimal(0), thesis)
        if u.last <= trigger:
            price = trigger if pos.stop_limit is None or u.last >= pos.stop_limit else t.bid
            return "stop", price
        if u.last >= target:
            return "target", t.bid
        return None
    if (u.last <= inval) if up else (u.last >= inval):
        return "invalidation", t.bid
    if (u.last >= target) if up else (u.last <= target):
        return "target", t.bid
    if (osi_expiry(pos.symbol) - today_et.date()).days <= rules.option_close_at_dte:
        return "dte_close", t.bid
    return None


async def _exits(
    store: Store, broker: Broker, notifier: Notifier, rules: Rules, now: datetime,
    rep: WatchReport,
) -> None:
    positions = await open_positions(store)
    requests = await pending_exit_requests(store)
    if positions:
        try:
            quotes = await _quote(broker, [p.underlying for p in positions] + [p.symbol for p in positions])
        except BrokerUnauthorized:
            rep.blind = True
            return
        except BrokerError:
            return
        et = now.astimezone(ET)
        wanted = {r.proposal_id: r for r in requests if r.proposal_id is not None}
        for pos in positions:
            u, t = quotes.get(pos.underlying), quotes.get(pos.symbol)
            if u is None or t is None:
                continue
            await upsert_mark(store, pos.symbol, t.bid if pos.instrument != "shares" else u.last, now)
            cid = await current_call_id(store, pos.call_id)
            call = await get_call(store, cid)
            assert call is not None
            inval = await effective_invalidation(store, call)
            hit = _exit_reason(pos, call.direction == "up", call.target, inval, u, t, rules, et)
            if hit is None and et.time() >= RESOLVED_EXIT_FROM:
                if await resolution_for(store, "call", cid) is not None:
                    hit = ("call_resolved", t.bid)
            if hit is None and pos.proposal_id in wanted:
                hit = ("pm_exit", t.bid)
            if hit is None:
                continue
            reason, price = hit
            await record_fill(store, pos.proposal_id, now, "sell", pos.quantity, price, reason)
            rep.exits.append((pos.proposal_id, reason))
            await notifier.post(f"📄 PAPER SELL {pos.quantity} {pos.symbol} @ {price} — {reason}")
    still_open = {p.proposal_id for p in await open_positions(store)}
    for r in requests:
        if r.proposal_id is None:
            await notifier.post(
                f"📌 The PM recommends exiting {r.symbol} (real money, a legacy position):"
                f" {r.reason}. Act at Schwab if you agree; the desk places nothing."
            )
            rep.recommended.append(r.symbol)
            await mark_exit_done(store, r.id, now)
        elif r.proposal_id not in still_open:
            await mark_exit_done(store, r.id, now)


async def run_desk_watch(
    *, store: Store, broker: Broker, notifier: Notifier, reactions: Reactions, rules: Rules,
    desk: DeskConfig, now: datetime,
) -> WatchReport:
    rep = WatchReport()
    await _entries(store, broker, notifier, reactions, rules, now, rep)
    await _exits(store, broker, notifier, rules, now, rep)
    return rep
```

`desk` is not read yet: the 15:55 cutoff is `CLAUDE.md §4.2`'s, not a
setting. Keep the parameter anyway. The engine passes it so a future
setting needs no signature change, and ruff does not flag unused
arguments (`ARG` is not in the rule set).

- [ ] **Step 3: Wire it into the engine**

In `engine/tc/main.py`:
- Import `run_desk_watch` from `tc.desk.watch`, and `DiscordReactions`,
  `NoReactions`, `Reactions` from `tc.desk.approval`.
- Add `reactions: Reactions | None = None` to `Engine.__init__`'s keyword
  arguments, and store `self._reactions = reactions or NoReactions()`.
- Add `"desk_watch"` to `JOBS`.
- In `_execute`, add
  `if job == "desk_watch": return await self._job_desk_watch(now)`.
- Add the method:

```python
    async def _job_desk_watch(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        rep = await run_desk_watch(
            store=self._store, broker=self._broker, notifier=self.notifier,
            reactions=self._reactions, rules=self._rules, desk=self._s.desk, now=now,
        )
        detail: dict[str, Any] = {
            "filled": rep.filled, "skipped": rep.skipped, "exits": rep.exits,
            "recommended": rep.recommended,
        }
        if rep.blind:
            # Not `failed` every five minutes: the tick already reports BLIND,
            # and a dead token is one state, not seventy-two failures a day.
            return "noop", {**detail, "skipped_reason": "blind"}
        return "done", detail
```

- In `build_engine`, build the reactions reader from the same Discord
  target the notifier uses, and pass it to `Engine(...)`:

```python
    target = _discord_target(settings, shadow)
    notifier = Notifier(target, client, "[shadow] " if shadow else "")
    reactions: Reactions = (
        DiscordReactions(target, client, settings.discord_approver_id)
        if isinstance(target, BotChannel) else NoReactions()
    )
```

  (`_discord_target` is now called once and reused.)

Add to `config.yml` `schedule:`:

```yaml
  desk_watch:   "every 5m 09:55-15:55 weekdays"   # trading-desk design §9.4-§9.5, §10
```

- [ ] **Step 4: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/desk/watch.py engine/tc/main.py config.yml tests/engine/unit/test_desk_watch.py
git commit -m "desk: desk_watch fills paper entries at the veto deadline and runs the paper exits"
```

---
### Task 16: The desk's prompts

**Files:**
- Create: `.claude/agents/analyst-technical.md`, `analyst-earnings.md`, `analyst-news.md`, `analyst-macro.md`, `pm.md`
- Modify: `tests/engine/unit/test_prompts.py`

**Interfaces:**
- Consumes: the tool names from Tasks 8 and 13. Every
  `mcp__engine__<name>` in these files must be in
  `ROLE_TOOLS[<the agent's role>]`, and `test_prompts.py` checks this.
- Produces the agent names that Task 17's job specs dispatch:

  | Agent | Model |
  |---|---|
  | `analyst-technical`, `analyst-earnings`, `analyst-news`, `analyst-macro` | sonnet |
  | `pm` | opus |

- **The `tools:` frontmatter:** it is the SDK-enforced ceiling for the run.
  It must equal the job allowlist Task 17 writes. For an agent that two
  jobs share (news, earnings, pm), it is the union of both jobs'
  allowlists, and the two lists are identical by construction.

- [ ] **Step 1: Update the prompt tests first (they will fail)**

In `tests/engine/unit/test_prompts.py`:

```python
LIVE_AGENTS = [
    "research-scout", "deep-research", "scout", "catalyst", "sector-tagger",
    "analyst-technical", "analyst-earnings", "analyst-news", "analyst-macro", "pm",
]
# The MCP role each live agent's tools are checked against. Everything not
# listed runs as `research` (the analysts reuse that role and its bearer).
AGENT_ROLE: dict[str, Role] = {"pm": "decide"}
DESK_MODELS = {
    "analyst-technical": "sonnet", "analyst-earnings": "sonnet", "analyst-news": "sonnet",
    "analyst-macro": "sonnet", "pm": "opus",
}


def _role_of(path: Path) -> Role:
    return AGENT_ROLE.get(path.stem, "research") if path.parent.name == "agents" else "research"
```

Then rewrite the two role-dependent tests and add the model and funding
checks:

```python
@pytest.mark.parametrize("name", LIVE_AGENTS)
def test_agent_tools_are_engine_tools_only(name: str) -> None:
    fm = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")
    tools = [t.strip() for t in fm["tools"].split(",")]
    banned = {"Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Task", "Agent"}
    assert not (set(tools) & banned), f"{name}: {sorted(set(tools) & banned)}"
    role = AGENT_ROLE.get(name, "research")
    for t in tools:
        if t.startswith("mcp__engine__"):
            assert t.removeprefix("mcp__engine__") in ROLE_TOOLS[role], t
        else:
            assert t in {"Read", "WebSearch", "WebFetch"}, t


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_every_engine_tool_named_in_a_live_prompt_is_in_the_registry(path: Path) -> None:
    body = path.read_text()
    role = _role_of(path)
    for name in re.findall(r"mcp__engine__([a-zA-Z_]+)", body):
        assert name in ROLE_TOOLS[role], f"{path} names unknown tool {name!r}"


@pytest.mark.parametrize("name,model", sorted(DESK_MODELS.items()))
def test_desk_agents_run_on_the_models_the_design_chose(name: str, model: str) -> None:
    assert frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")["model"] == model


@pytest.mark.parametrize("name", [n for n in DESK_MODELS if n != "pm"])
def test_no_analyst_is_handed_a_funding_or_call_tool(name: str) -> None:
    tools = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")["tools"]
    for forbidden in ("call_submit", "option_candidates", "paper_book", "exit_request"):
        assert forbidden not in tools, f"{name} holds {forbidden}"
```

Change the file's registry import to `from tc.mcp.registry import ROLE_TOOLS, Role`.

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_prompts.py`
Expected: FAIL (the five agent files do not exist).

- [ ] **Step 2: Write `.claude/agents/analyst-technical.md`**

````markdown
---
name: analyst-technical
description: Technical analyst on the trading desk. One pass reads the engine-computed technical briefing -- breakouts, breakdowns, pullbacks inside uptrends, relative-strength extremes, volume spikes -- and files at most five dated, falsifiable pitches through mcp__engine__pitch_submit. Never sizes, funds or trades; the PM decides. Every pitch is scored against SPY and its sector benchmark whether or not it is traded.
tools: WebSearch, WebFetch, mcp__engine__get_datetime, mcp__engine__market_hours, mcp__engine__quotes, mcp__engine__price_history, mcp__engine__instruments, mcp__engine__briefing, mcp__engine__pitch_submit, mcp__engine__pitch_withdraw, mcp__engine__my_record
model: sonnet
---

# Technical analyst

You are one of four analysts on a trading desk that Claude runs for a small
real-money cash account. Tomorrow at 09:50 ET a portfolio manager -- another
Claude run -- reads every analyst's pitches, makes up to five calls and
decides which to fund. **What is being tested is whether Claude's judgement
has an edge.** Every pitch you file is scored -- hit or miss, return, and
return in excess of SPY and of the sector benchmark you name -- whether or not
anyone trades it, and your last ten results come back to you in every
briefing.

Your approach is **price and volume**: trend, breakouts and breakdowns,
pullbacks inside an uptrend, relative strength, unusual volume. News and
fundamentals matter to you only as a check that a setup is not about to be
broken by something scheduled or already known.

## One pass

1. `mcp__engine__get_datetime` for the Eastern date. Never read a date anywhere else.
2. `mcp__engine__briefing`. Your screen is computed by the engine from stored
   daily bars; every row already carries ATR%, distance from the 20/50/200-day
   averages, 3- and 6-month relative strength against SPY, distance from the
   52-week high and low, the volume ratio and the gap. **Do not recompute
   them.** Read `record` first -- what did your recent pitches get wrong, and
   does it change what you look for tonight? -- and `open_pitches`, which you
   cannot pitch again.
3. Choose the few rows with the cleanest setups. For each you may pull
   `mcp__engine__price_history` to look at the shape, and search the web for
   anything that would break the setup before your horizon ends: an earnings
   date inside the window, a pending deal, an offering, a regulatory ruling.
4. File **at most five** pitches with `mcp__engine__pitch_submit`. Rank before
   you file: the cap is per run and a refused sixth is simply lost.

## A good pitch

- `direction`: `up` or `down`.
- `target`: where the move you expect should reach within the horizon.
  `invalidation`: the price at which your reading of the chart is wrong -- a
  close back inside the base, below the pullback low, above the breakdown
  level. It is where the thesis is falsified, not a stop-loss dressed up.
  Both are decimal strings like `"48.25"`, on the correct sides of the last
  price and within 30% of it.
- `horizon_days`: 2-20 trading days. Match it to the setup: a breakout that
  has not worked in ten sessions usually has not worked.
- `conviction` 1-5, honestly. 1-2 is never funded but is still scored -- use it
  for a setup you would not put money on. The PM sizes by conviction.
- `benchmark`: the sector SPDR the name belongs to (XLK, XLF, XLE, XLV, XLI,
  XLY, XLP, XLU, XLB, XLRE, XLC), or SPY for a name that fits no sector.
- `thesis`: at most 400 characters -- what happens next and why.
- `evidence`: 1-5 items `{url, claim, date}`, each a page you actually read.

You never type a reference price: the engine stamps each pitch at the next
session's opening print. Pitches are filed outside the regular session; the
engine refuses one filed between 09:30 and 16:00.

## Rules

- You never size, fund or trade, and you have no tool that could.
- A refusal from `pitch_submit` is a sentence written for you: read it, fix
  the pitch, retry once. Do not argue with the limits.
- Zero pitches is a legitimate answer on a night with no setup -- say why in
  the summary. A desk that never commits cannot be scored, so it should be rare.

## Return

Return the JSON object matching the AnalystVerdict schema and nothing else:
`pitched` -- the ids `pitch_submit` returned; `withdrawn` -- ids you withdrew
(none, in an evening pass); `summary` -- one line, for example
`TECHNICAL 3 pitches: NVDA up breakout, KRE down breakdown, XOM up pullback`.
````

- [ ] **Step 3: Write `.claude/agents/analyst-earnings.md`**

Use the same frontmatter shape: `name: analyst-earnings`, the identical
`tools:` line and `model: sonnet`. Use this description:
`Earnings analyst on the trading desk. Reads the engine's earnings-reaction screen (gaps on heavy volume in the last three sessions), researches report dates and reactions, and files at most five pitches per evening -- or, in PRE-OPEN MODE, at most two new pitches and withdrawals of its own pitches whose premise broke overnight. Never sizes, funds or trades.`

Body:

````markdown
# Earnings analyst

You are one of four analysts on a trading desk that Claude runs for a small
real-money cash account. Tomorrow at 09:50 ET a portfolio manager -- another
Claude run -- reads every analyst's pitches, makes up to five calls and
decides which to fund. **What is being tested is whether Claude's judgement
has an edge.** Every pitch you file is scored against SPY and against the
sector benchmark you name, traded or not, and your last ten results come back
to you in every briefing.

Your approach is **the earnings cycle**:

- **Reactions.** A name that gapped on heavy volume after a report either
  drifts on in the direction of the surprise or fades back. Your job is to
  tell which: the quality of the beat or miss, guidance, how the gap held on
  day one, what the call revealed that the headline did not.
- **Setups into a print.** A name reporting within the next four weeks whose
  risk into the report is lopsided. Put the report date in your evidence;
  find it from company investor relations or an exchange calendar (Nasdaq's
  earnings calendar works), and say so if you could not confirm it.

## One pass

1. `mcp__engine__get_datetime`.
2. `mcp__engine__briefing`: `rows` are names that gapped at least 3% on at
   least twice their usual volume in the last three sessions, with the
   engine's numbers attached. Read `record` and `open_pitches` first.
3. For the strongest few: read the release and the reaction (WebSearch,
   WebFetch), check `mcp__engine__price_history` for how the gap has held,
   decide drift or fade.
4. File **at most five** pitches with `mcp__engine__pitch_submit`.

**PRE-OPEN MODE** (the job prompt says so; it is about 08:00 ET): read the
pre-market reporters and overnight news against your open pitches. File at
most **two new** pitches, and withdraw any of your own open pitches whose
premise broke overnight with `mcp__engine__pitch_withdraw` -- possible only
before 09:30, after which a pitch can only resolve.

## A good pitch

`direction`; `target` where the move should reach within the horizon;
`invalidation` where your reading is wrong (for a drift call, typically a fill
of the gap); both decimal strings, correct sides of the last price, within
30%. `horizon_days` 2-20 trading days. `conviction` 1-5, honestly -- 1-2 is
scored, never funded. `benchmark` the sector SPDR (XLK, XLF, XLE, XLV, XLI,
XLY, XLP, XLU, XLB, XLRE, XLC) or SPY. `thesis` at most 400 characters.
`evidence` 1-5 items `{url, claim, date}` you actually read. You never type a
reference price: the engine stamps the next session's opening print.

## Rules

You never size, fund or trade. A refusal is a sentence written for you: fix
the pitch and retry once. Zero pitches is allowed with a reason in the summary.

## Return

Return the JSON object matching the AnalystVerdict schema and nothing else:
`pitched`, `withdrawn`, and a one-line `summary`, for example
`EARNINGS 2 pitches: DAL up drift, NKE down fade; withdrew 0`.
````

- [ ] **Step 4: Write `.claude/agents/analyst-news.md`**

Frontmatter: `name: analyst-news`, the identical `tools:` line, `model: sonnet`.
Description: `News and catalyst analyst on the trading desk. Reads the day's Schwab movers and the engine's gap list, researches what happened and who it moves next, and files at most five pitches per evening -- or, in PRE-OPEN MODE, at most two new pitches and withdrawals of its own pitches whose premise broke overnight. Never sizes, funds or trades.`

Body:

````markdown
# News and catalyst analyst

You are one of four analysts on a trading desk that Claude runs for a small
real-money cash account. Tomorrow at 09:50 ET a portfolio manager -- another
Claude run -- reads every analyst's pitches, makes up to five calls and
decides which to fund. **What is being tested is whether Claude's judgement
has an edge.** Every pitch you file is scored against SPY and against the
sector benchmark you name, traded or not, and your last ten results come back
to you in every briefing.

Your approach is **what happened and who it moves next**: guidance changes,
deals, contract wins and losses, regulatory decisions, outages, supply-chain
shifts. The first-order name has usually moved by the time you see it; the
edge, if there is one, is in the second-order name that has not -- the
supplier, the competitor, the customer -- and in telling a move that is
priced from one that is not.

## One pass

1. `mcp__engine__get_datetime`.
2. `mcp__engine__briefing`: `movers` (Schwab's biggest movers, up and down),
   `rows` (the day's largest gaps, with the engine's numbers), `record` and
   `open_pitches`. If `movers_error` is set, say so in your summary and work
   from the gap list.
3. Research the few stories that matter: what actually happened, whether the
   market has already priced it, and who else it touches.
   `mcp__engine__quotes` and `mcp__engine__price_history` tell you whether a
   second-order name has moved yet.
4. File **at most five** pitches with `mcp__engine__pitch_submit`.

**PRE-OPEN MODE** (the job prompt says so; it is about 08:00 ET): read
overnight news against your open pitches. File at most **two new** pitches,
and withdraw any of your own open pitches whose premise broke overnight with
`mcp__engine__pitch_withdraw` -- possible only before 09:30.

## A good pitch

`direction`; `target` where the move should reach within the horizon;
`invalidation` where your reading is wrong; both decimal strings, correct
sides of the last price, within 30%. `horizon_days` 2-20 trading days.
`conviction` 1-5, honestly -- 1-2 is scored, never funded. `benchmark` the
sector SPDR (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) or SPY.
`thesis` at most 400 characters. `evidence` 1-5 items `{url, claim, date}` you
actually read -- name the primary source, not an aggregator. You never type a
reference price: the engine stamps the next session's opening print.

## Rules

You never size, fund or trade. A refusal is a sentence written for you: fix
the pitch and retry once. Zero pitches is allowed with a reason in the summary.

## Return

Return the JSON object matching the AnalystVerdict schema and nothing else:
`pitched`, `withdrawn`, and a one-line `summary`, for example
`NEWS 3 pitches: AVGO up (second-order to the NVDA guide), ...; withdrew 1`.
````

- [ ] **Step 5: Write `.claude/agents/analyst-macro.md`**

Frontmatter: `name: analyst-macro`, the identical `tools:` line, `model: sonnet`.
Description: `Macro and ETF analyst on the trading desk. Reads the engine's ETF table (trend and relative strength across sectors, bonds, commodities, the dollar and emerging markets) and files at most five pitches on ETFs from the desk's list. Never sizes, funds or trades.`

Body:

````markdown
# Macro and ETF analyst

You are one of four analysts on a trading desk that Claude runs for a small
real-money cash account. Tomorrow at 09:50 ET a portfolio manager -- another
Claude run -- reads every analyst's pitches, makes up to five calls and
decides which to fund. **What is being tested is whether Claude's judgement
has an edge.** Every pitch you file is scored against SPY, traded or not, and
your last ten results come back to you in every briefing.

Your approach is **the macro tape, expressed in ETFs**: rates and the curve,
the dollar, oil and metals, credit, and the rotation between sectors that
follows from them. You pitch only ETFs from the desk's list -- the briefing's
`rows` are exactly that list -- and liquid ETF options are the cheapest way the
PM can express a view, so your pitches are likely to be funded.

## One pass

1. `mcp__engine__get_datetime`.
2. `mcp__engine__briefing`: `rows` (every ETF on the list, ranked by 3-month
   relative strength, with the engine's numbers) and `context` (SPY and the
   dollar). Read `record` and `open_pitches`.
3. Read the macro calendar and the week's data (WebSearch): the next CPI, jobs
   report, FOMC, Treasury auctions, OPEC. Form a view of what is priced and
   what is not, then find the ETF that expresses it most directly.
4. File **at most five** pitches with `mcp__engine__pitch_submit`.

## A good pitch

`direction`; `target`; `invalidation`; both decimal strings, correct sides of
the last price, within 30%. `horizon_days` 2-20 trading days. `conviction`
1-5, honestly -- 1-2 is scored, never funded. **`benchmark` is SPY for every
ETF pitch**: a sector ETF scored against itself would always score zero.
`thesis` at most 400 characters. `evidence` 1-5 items `{url, claim, date}` you
actually read. You never type a reference price: the engine stamps the next
session's opening print.

## Rules

You never size, fund or trade. A refusal is a sentence written for you: fix
the pitch and retry once. Zero pitches is allowed with a reason in the summary.

## Return

Return the JSON object matching the AnalystVerdict schema and nothing else:
`pitched`, `withdrawn` (none), and a one-line `summary`, for example
`MACRO 2 pitches: TLT up into CPI, XLE down on the OPEC supply add`.
````

- [ ] **Step 6: Write `.claude/agents/pm.md`**

````markdown
---
name: pm
description: Portfolio manager on the trading desk -- the trader under test. The 09:50 ET run reviews the paper book and every analyst's open pitches, makes up to five calls (adopting a pitch or originating one) and decides which to fund within the engine's caps; the 12:30 run (MIDDAY MODE) manages held positions only. Engine code stamps every price, sizes within the manual's caps, posts proposals behind a 10-minute veto window, fills the paper book and scores everything.
tools: WebSearch, WebFetch, mcp__engine__get_datetime, mcp__engine__market_hours, mcp__engine__quotes, mcp__engine__price_history, mcp__engine__option_chain, mcp__engine__book, mcp__engine__paper_book, mcp__engine__pitches_read, mcp__engine__scorecard, mcp__engine__option_candidates, mcp__engine__call_submit, mcp__engine__call_extend, mcp__engine__call_tighten, mcp__engine__exit_request
model: opus
---

# Portfolio manager

You run a trading desk that Claude operates for Chris's small real-money cash
account. Four analysts -- technical, earnings, news, macro -- pitched overnight
and before the open. You decide. **What is being tested is whether your
judgement has an edge over holding SPY.** Every call you make is scored --
hit, return, excess over SPY and over its sector -- whether or not it is
funded, and so is the gap between your calls and the average pitch: that gap
says whether your choosing adds anything.

The engine enforces the trading manual (CLAUDE.md) in code: position caps,
the $900 settlement reserve, the correlation cap, the option quality floors,
stops, settlement. Spend your turns on judgement, not on re-deriving rules;
when a tool refuses, the sentence says why.

**Paper phase.** Until the order path is built, a funded call fills a paper
book at quoted prices and nothing reaches Schwab. Behave exactly as you would
with real money: the paper book is part of how you are judged.

## The 09:50 run

1. `mcp__engine__get_datetime`.
2. `mcp__engine__paper_book`: positions with their call's target,
   invalidation and horizon; pending proposals; and the real account's legacy
   positions.
3. `mcp__engine__scorecard`: your record, each analyst's, and the checkpoint.
   Let the record inform whom you trust: an analyst running negative needs
   stronger evidence from you.
4. **Held positions first.** For each: hold; `mcp__engine__exit_request` with a
   reason; or `mcp__engine__call_tighten` to move the invalidation toward the
   price and protect a gain. A funded call that reached its horizon at the
   last close is exited at 10:00 unless you `mcp__engine__call_extend` it --
   once, and only if the thesis is intact rather than merely hoped for.
5. **Legacy positions** (CSX and IQV at the start of the test): if one has no
   legacy call, attach your view with `mcp__engine__call_submit` and
   `legacy: true`, funding `"none"`, or recommend an exit with
   `mcp__engine__exit_request`. Chris acts on legacy exits himself at Schwab.
6. `mcp__engine__pitches_read`, then rank. Verify the best with
   `mcp__engine__quotes`, `mcp__engine__price_history` and web search: has the
   open already priced the story? Is there an event inside the horizon?
7. **Make up to five new calls** with `mcp__engine__call_submit`: adopt a pitch
   with `pitch_id` (you may change its levels or horizon, and the change is
   recorded against the call, not the analyst) or originate your own.
   **Commit.** An unfunded call costs nothing and is still scored; express
   doubt with conviction, not by abstaining. Making fewer than three calls
   when three or more pitches are open needs a reason in your summary.
8. **Funding**, conviction 3-5 only:
   - **Shares** for an up call on a name whose daily ATR is at most 6%. Set
     `max_entry_price` at or a little above the ask; the engine refuses more
     than 5% above the last price. Size is 10/15/20% of equity by conviction.
   - **An option** when leverage fits the view or the name is too volatile for
     shares, and **a put** for a down call. Call
     `mcp__engine__option_candidates` with the symbol, direction, horizon and
     conviction first, and pass one of the symbols it returns as
     `option_symbol`. Premium is 5/7.5/10% of equity by conviction. Options go
     to zero: size by conviction, not by hope.
   - The engine refuses anything that breaks a cap and says which one; adjust,
     or leave the call unfunded.
   - A funded call becomes a Discord proposal that executes ten minutes after
     it is posted unless Chris reacts ❌. A vetoed proposal is still recorded
     in the paper book as your decision, so your record stays complete.

## MIDDAY MODE (the 12:30 run; the job prompt says so)

Held positions and today's news only: hold, `mcp__engine__exit_request` or
`mcp__engine__call_tighten`. No new calls -- `call_submit` refuses at midday.

## Return

Return the JSON object matching the PmVerdict schema (the MiddayVerdict schema
at midday) and nothing else: `held` -- one entry per held position,
`{symbol, action: "hold" | "exit" | "tighten", reason}`; `calls` -- the call ids
you made (PmVerdict only); `summary` -- one line, for example
`PM 4 calls (2 funded): AAPL up shares, XLE down put, KRE down, TLT up; exits: none`.
````

- [ ] **Step 7: Run the prompt tests**

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_prompts.py`
Expected: PASS. (`test_every_job_spec_agent_exists…` and the allowlist-equality
tests do not see these agents until Task 17 adds their job specs.)

- [ ] **Step 8: Commit**

```bash
git add .claude/agents/analyst-technical.md .claude/agents/analyst-earnings.md \
  .claude/agents/analyst-news.md .claude/agents/analyst-macro.md .claude/agents/pm.md \
  tests/engine/unit/test_prompts.py
git commit -m "desk: the four analysts and the PM, on the models the design chose"
```

---

### Task 17: Desk jobs — specs, the analyst chain, the PM hooks, per-role bearers, queueing

**Files:**
- Modify: `engine/tc/jobs/spec.py` (verdicts, `tools_for_role`, `retry_failed_after_s`, 8 specs, `DESK_CHAINS`, `CHAINED_JOBS`)
- Modify: `engine/tc/jobs/dispatch.py` (role bearers, busy wait, one PM retry, summary relays)
- Create: `engine/tc/desk/post.py`
- Modify: `engine/tc/main.py` (chains, PM pre/post hooks, bearers)
- Modify: `engine/tc/rules/consistency.py` (chained jobs need no schedule entry)
- Modify: `config.yml` (schedule, one expectation)
- Modify: `tests/engine/unit/test_jobs_dispatch.py` (`_jr` passes a no-op sleep; busy now waits, then `missed`)
- Modify: `tests/engine/unit/test_main.py` (`CHAINED_JOBS` in the schedule test)
- Test: `tests/engine/unit/test_desk_jobs.py`, `tests/engine/unit/test_desk_engine.py`

**Interfaces:**
- Consumes: the agents (Task 16), `post_message` (Task 14), `ensure_book` (Task 11).
- Produces:
  - Verdict models `AnalystVerdict`, `HeldDecision`, `PmVerdict`, `MiddayVerdict`.
  - `tools_for_role(role, *names)`.
  - `JobSpec.retry_failed_after_s`.
  - Specs: `analyst_technical`, `analyst_earnings`, `analyst_news`,
    `analyst_macro`, `preopen_news`, `preopen_earnings`, `pm`, `pm_midday`.
  - `DESK_CHAINS`, `CHAINED_JOBS`.
  - `RunnerClient(..., role_tokens=...)` and `RunnerClient.token_for(role)`.
  - `JobRunner(..., sleep=..., busy_retry_s=...)`.
  - `post_proposals(store, notifier, rules, desk, now) -> list[int]`
  - `pitch_counts_since(store, since) -> dict[str, int]`
  - `desk_summary(chain, results, counts, day) -> str`
  - Engine jobs `desk_evening` and `desk_preopen`.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_desk_jobs.py`:

```python
"""Task 17: the desk's job table."""

from __future__ import annotations

from pathlib import Path

import yaml

from tc.jobs.spec import CHAINED_JOBS, DESK_CHAINS, JOB_SPECS, AnalystVerdict, output_schema

REPO = Path(__file__).resolve().parents[3]


def test_every_chained_job_has_a_spec_and_none_is_scheduled_on_its_own() -> None:
    sched = yaml.safe_load((REPO / "config.yml").read_text())["schedule"]
    assert {"desk_evening", "desk_preopen", "pm", "pm_midday", "desk_watch"} <= set(sched)
    for chain, jobs in DESK_CHAINS.items():
        assert chain in sched
        for job in jobs:
            assert job in JOB_SPECS and job not in sched


def test_the_evening_chain_order_is_technical_earnings_news_macro() -> None:
    assert DESK_CHAINS["desk_evening"] == (
        "analyst_technical", "analyst_earnings", "analyst_news", "analyst_macro",
    )
    assert CHAINED_JOBS >= set(DESK_CHAINS["desk_preopen"])


def test_analysts_run_as_research_and_the_pm_as_decide() -> None:
    for job in CHAINED_JOBS:
        assert JOB_SPECS[job].role == "research"
    assert JOB_SPECS["pm"].role == JOB_SPECS["pm_midday"].role == "decide"


def test_jobs_sharing_an_agent_share_one_allowlist() -> None:
    for a, b in (("analyst_news", "preopen_news"), ("analyst_earnings", "preopen_earnings"),
                 ("pm", "pm_midday")):
        assert set(JOB_SPECS[a].allowed_tools) == set(JOB_SPECS[b].allowed_tools)


def test_only_the_pm_retries_a_failure() -> None:
    assert JOB_SPECS["pm"].retry_failed_after_s == 900
    assert all(s.retry_failed_after_s is None for n, s in JOB_SPECS.items() if n != "pm")


def test_the_analyst_verdict_forbids_extra_keys() -> None:
    schema = output_schema(AnalystVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"pitched", "withdrawn", "summary"}
```

Append to `tests/engine/unit/test_jobs_dispatch.py`, and change `_jr` so
nothing in the file ever really sleeps:

```python
async def _no_sleep(s: float) -> None:
    return None


def _jr(handler: Handler, notifier: RecordingNotifier, **kw: Any) -> JobRunner:
    return JobRunner(_runner(handler, **kw), notifier, lambda: IN_WINDOW, sleep=_no_sleep)
```

Change `test_busy_runner_is_a_noop`: rename it to
`test_a_runner_busy_all_window_is_missed`, and its assertion to
`assert (verdict, detail["skipped"]) == ("missed", "runner busy past its window")`.
Then add:

```python
PM_AT = datetime(2026, 9, 8, 13, 50, tzinfo=UTC)          # 09:50 ET
PM_GOOD: dict[str, Any] = {**GOOD, "verdict_raw": {"held": [], "calls": [4], "summary": "PM 1 call"}}


def _pm_runner(handler: Handler, decide: str | None = "decide-token-not-real") -> RunnerClient:  # noqa: S107
    return RunnerClient("http://runner", TOKEN,
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)), SLACK_S,
                        role_token=ROLE_TOKEN,
                        role_tokens={"decide": decide} if decide else None)


async def test_a_decide_job_presents_the_decide_bearer(notifier: RecordingNotifier) -> None:
    seen: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.read()))
        return httpx.Response(200, json=PM_GOOD)

    jr = JobRunner(_pm_runner(h), notifier, lambda: PM_AT, sleep=_no_sleep)
    verdict, _ = await jr.execute("pm", PM_AT)
    assert verdict == "done"
    assert (seen["mcp_role"], seen["mcp_role_token"]) == ("decide", "decide-token-not-real")
    assert any("PM 1 call" in p for p in notifier.posted)


async def test_a_decide_job_without_a_decide_bearer_is_never_dispatched(
    notifier: RecordingNotifier,
) -> None:
    jr = JobRunner(_pm_runner(_ok(PM_GOOD), decide=None), notifier, lambda: PM_AT, sleep=_no_sleep)
    assert (await jr.execute("pm", PM_AT)) == ("noop", {"skipped": "no mcp decide token"})


async def test_a_busy_runner_is_waited_out_inside_the_window(notifier: RecordingNotifier) -> None:
    replies = [httpx.Response(409), httpx.Response(409), httpx.Response(200, json=GOOD)]
    jr = _jr(lambda r: replies.pop(0), notifier)
    verdict, _ = await jr.execute("scout", IN_WINDOW)
    assert verdict == "done" and replies == []


async def test_the_pm_retries_a_failed_run_once(notifier: RecordingNotifier) -> None:
    failed = {**PM_GOOD, "is_error": True, "subtype": "error_during_execution",
              "result_text": "You've hit your session limit"}
    replies = [httpx.Response(200, json=failed), httpx.Response(200, json=PM_GOOD)]
    jr = JobRunner(_pm_runner(lambda r: replies.pop(0)), notifier, lambda: PM_AT, sleep=_no_sleep)
    verdict, detail = await jr.execute("pm", PM_AT)
    assert verdict == "done" and detail["retried"] is True
```

(`json` is already imported in that file; if it is not, add `import json`.)

Create `tests/engine/unit/test_desk_engine.py`:

```python
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
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_jobs.py ../tests/engine/unit/test_desk_engine.py ../tests/engine/unit/test_jobs_dispatch.py`
Expected: FAIL (no `DESK_CHAINS`, no `role_tokens`, and so on).

- [ ] **Step 2: Job specs**

In `engine/tc/jobs/spec.py`, add `Literal` to the typing import. Then add
the verdict models after `SectorVerdict`:

```python
class AnalystVerdict(BaseModel):
    """An analyst pass. The pitches themselves are rows written through
    `pitch_submit`; the verdict only names them, so a verdict that fails to
    parse loses nothing but the summary line."""

    model_config = ConfigDict(extra="forbid")
    pitched: list[int]
    withdrawn: list[int]
    summary: str


class HeldDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    action: Literal["hold", "exit", "tighten"]
    reason: str


class PmVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    held: list[HeldDecision]
    calls: list[int]
    summary: str


class MiddayVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    held: list[HeldDecision]
    summary: str
```

Replace `tools_for` with a role-aware function, keeping the old name for
the six existing specs:

```python
def tools_for_role(role: Role, *names: str) -> tuple[str, ...]:
    """Prefix each name with `mcp__engine__` after checking it against that
    role's registry, so a typo -- or a tool of the other role -- fails at
    import time rather than as a refusal mid-job."""
    role_tools = ROLE_TOOLS[role]
    for name in names:
        if name not in role_tools:
            raise KeyError(name)
    return tuple(f"mcp__engine__{name}" for name in names)


def tools_for(*names: str) -> tuple[str, ...]:
    return tools_for_role("research", *names)
```

Add `retry_failed_after_s: float | None = None` as the **last** field of
`JobSpec`. It is the only field with a default, so the existing specs are
unchanged.

After `JOB_SPECS` is defined, add the desk specs and chains:

```python
# ---------------------------------------------------------------------------
# The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md §4)
# ---------------------------------------------------------------------------

_WEB: tuple[str, ...] = ("WebSearch", "WebFetch")
_ANALYST_ALLOW = tools_for_role(
    "research", "get_datetime", "market_hours", "quotes", "price_history", "instruments",
    "briefing", "pitch_submit", "pitch_withdraw", "my_record",
) + _WEB
_PM_ALLOW = tools_for_role(
    "decide", "get_datetime", "market_hours", "quotes", "price_history", "option_chain", "book",
    "paper_book", "pitches_read", "scorecard", "option_candidates", "call_submit", "call_extend",
    "call_tighten", "exit_request",
) + _WEB

_EVENING_PROMPT = (
    "EVENING PASS. Follow your agent instructions for one evening pass: read "
    "mcp__engine__briefing, file at most five pitches with mcp__engine__pitch_submit, and "
    "never size, fund or trade. Return a single JSON object matching the AnalystVerdict "
    "schema and nothing else."
)
_PREOPEN_PROMPT = (
    "PRE-OPEN MODE. It is before the 09:30 open. Read overnight news and pre-market reports "
    "against your open pitches and your briefing. File at most two NEW pitches, and withdraw "
    "any of your own open pitches whose premise broke overnight with "
    "mcp__engine__pitch_withdraw. Return a single JSON object matching the AnalystVerdict "
    "schema and nothing else."
)


def _evening(name: str, agent: str) -> JobSpec:
    return JobSpec(
        name=name, agent=agent, command=f"/desk {name}", prompt=_EVENING_PROMPT,
        allowed_tools=_ANALYST_ALLOW, verdict=AnalystVerdict, max_turns=40, timeout_s=1500.0,
        window=(time(16, 25), time(20, 0)), role="research", noop_when=None,
    )


def _preopen(name: str, agent: str) -> JobSpec:
    return JobSpec(
        name=name, agent=agent, command=f"/desk {name}", prompt=_PREOPEN_PROMPT,
        allowed_tools=_ANALYST_ALLOW, verdict=AnalystVerdict, max_turns=20, timeout_s=600.0,
        window=(time(7, 55), time(9, 0)), role="research", noop_when=None,
    )


DESK_SPECS: dict[str, JobSpec] = {
    "analyst_technical": _evening("analyst_technical", "analyst-technical"),
    "analyst_earnings": _evening("analyst_earnings", "analyst-earnings"),
    "analyst_news": _evening("analyst_news", "analyst-news"),
    "analyst_macro": _evening("analyst_macro", "analyst-macro"),
    "preopen_news": _preopen("preopen_news", "analyst-news"),
    "preopen_earnings": _preopen("preopen_earnings", "analyst-earnings"),
    "pm": JobSpec(
        name="pm", agent="pm", command="/desk pm",
        prompt=(
            "Follow your agent instructions for the 09:50 run: the book first, then up to "
            "five calls, then funding. Return a single JSON object matching the PmVerdict "
            "schema and nothing else."
        ),
        allowed_tools=_PM_ALLOW, verdict=PmVerdict, max_turns=40, timeout_s=900.0,
        # Latest start 10:30 (spec §13): a PM that cannot start by then makes
        # no entries that day, and the 900s retry must also land inside it.
        window=(time(9, 45), time(10, 30)), role="decide", noop_when=None,
        retry_failed_after_s=900.0,
    ),
    "pm_midday": JobSpec(
        name="pm_midday", agent="pm", command="/desk pm_midday",
        prompt=(
            "MIDDAY MODE. Held positions and today's news only: hold, mcp__engine__exit_request "
            "or mcp__engine__call_tighten. No new calls. Return a single JSON object matching "
            "the MiddayVerdict schema and nothing else."
        ),
        allowed_tools=_PM_ALLOW, verdict=MiddayVerdict, max_turns=15, timeout_s=300.0,
        window=(time(12, 25), time(13, 30)), role="decide", noop_when=None,
    ),
}
JOB_SPECS.update(DESK_SPECS)

# Chains run their jobs one after another inside one engine job, so the
# one-job-at-a-time runner never sees two of them collide (the 2026-09-08
# catalyst run was lost to "runner busy"). A chained job has no schedule entry
# of its own.
DESK_CHAINS: dict[str, tuple[str, ...]] = {
    "desk_evening": ("analyst_technical", "analyst_earnings", "analyst_news", "analyst_macro"),
    "desk_preopen": ("preopen_news", "preopen_earnings"),
}
CHAINED_JOBS: frozenset[str] = frozenset(j for jobs in DESK_CHAINS.values() for j in jobs)
```

- [ ] **Step 3: Dispatch — per-role bearers, busy wait, one PM retry**

In `engine/tc/jobs/dispatch.py`:
- Add `import asyncio`.
- Extend the imports to `from collections.abc import Awaitable, Callable, Mapping`
  and `from datetime import date, datetime, timedelta`.
- Add `"pm", "pm_midday"` to `SUMMARY_JOBS`.

`RunnerClient.__init__` gains one keyword argument and keeps the old one:

```python
        role_token: str | None = None,
        role_tokens: Mapping[str, str] | None = None,
    ) -> None:
        ...
        # One bearer per MCP role. `role_token` is the research bearer, kept as
        # its own argument because every existing caller passes it that way.
        self._role_tokens: dict[str, str] = {k: v for k, v in (role_tokens or {}).items() if v}
        if role_token:
            self._role_tokens.setdefault("research", role_token)
```

Replace `has_role_token` and add `token_for`:

```python
    @property
    def has_role_token(self) -> bool:
        return "research" in self._role_tokens

    def token_for(self, role: str) -> str | None:
        return self._role_tokens.get(role)
```

In `run`, send the job's own role's bearer:

```python
            "mcp_role_token": self._role_tokens.get(spec.role, ""),
```

`JobRunner.__init__` takes the sleep and the busy interval:

```python
    def __init__(
        self, runner: RunnerClient, notifier: Notifier, clock: Callable[[], datetime],
        *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        busy_retry_s: float = 60.0,
    ) -> None:
        self._runner = runner
        self._notifier = notifier
        self._clock = clock
        self._sleep = sleep
        self._busy_retry_s = busy_retry_s
```

In `execute`, replace the `has_role_token` check:

```python
        if self._runner.token_for(spec.role) is None:
            log.warning("%s not dispatched: no MCP %s token configured", job, spec.role)
            return "noop", {"skipped": f"no mcp {spec.role} token"}
```

Then replace everything from `reply = await self._runner.run(...)` to the
end of `execute` with:

```python
        extra = _prompt_extra(spec, et.date())
        reply = await self._runner.run(spec, prompt_extra=extra)
        if reply.busy:
            reply = await self._wait_out_busy(spec, et, extra)
            if reply.busy:
                return "missed", {"skipped": "runner busy past its window"}
        verdict, model, detail = classify(spec, reply)
        if verdict == "failed" and spec.retry_failed_after_s is not None:
            later = et + timedelta(seconds=spec.retry_failed_after_s)
            if ignore_window or later.time() <= end:
                await self._sleep(spec.retry_failed_after_s)
                first = detail
                verdict, model, detail = classify(
                    spec, await self._runner.run(spec, prompt_extra=extra)
                )
                detail = {**detail, "retried": True, "first": first}
        await self._relay(spec, verdict, model, detail, et.date())
        return verdict, detail

    async def _wait_out_busy(self, spec: JobSpec, et: datetime, extra: str) -> RunnerReply:
        """Queue, don't drop (spec §14): retry every `busy_retry_s` until the
        job's own window closes. The number of tries is fixed from the time
        left at the first refusal, so a frozen test clock cannot spin."""
        end = datetime.combine(et.date(), spec.window[1], tzinfo=et.tzinfo)
        tries = max(0, int((end - et).total_seconds() // self._busy_retry_s))
        reply = RunnerReply(busy=True)
        for _ in range(tries):
            await self._sleep(self._busy_retry_s)
            reply = await self._runner.run(spec, prompt_extra=extra)
            if not reply.busy:
                return reply
        return reply
```

`start, end = spec.window` is already computed earlier in `execute`, so
`end` is in scope. `classify` is unchanged and still maps a busy reply to
`noop`; `execute` no longer lets one reach it.

- [ ] **Step 4: `tc/desk/post.py`**

```python
"""What the desk says in Discord, and the posting of paper proposals behind
their veto window (spec §9.4)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.calls import Call, get_call
from tc.desk.models import utc_iso
from tc.desk.paper import Proposal, mark_posted, record_outcome, unposted_proposals
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store


def render_proposal(p: Proposal, call: Call, deadline_et: str) -> str:
    kind = {"shares": "shares", "call": "CALL", "put": "PUT"}[p.instrument]
    return (
        f"🧾 PAPER PROPOSAL #{p.id} — BUY {p.quantity} {p.symbol} ({kind}), max {p.max_entry_price}\n"
        f"call #{call.id}: {call.symbol} {call.direction} → target {call.target},"
        f" invalidation {call.invalidation}, {call.horizon_days} sessions, conviction {call.conviction}\n"
        f"{call.thesis}\n"
        f"Executes at {deadline_et} ET unless ❌ (✅ = now). Paper phase: nothing reaches Schwab."
    )


async def post_proposals(
    store: Store, notifier: Notifier, rules: Rules, desk: DeskConfig, now: datetime
) -> list[int]:
    et = now.astimezone(ET)
    window = timedelta(minutes=int(rules.get("strategy", "veto_window_minutes")))
    opens = datetime.combine(et.date(), desk.entry_window_start, tzinfo=ET)
    posted: list[int] = []
    for p in await unposted_proposals(store):
        if et.time() >= desk.entry_window_end:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "proposed after the entry window"})
            continue
        call = await get_call(store, p.call_id)
        assert call is not None
        deadline = max(now, opens) + window
        mid = await notifier.post_message(
            render_proposal(p, call, deadline.astimezone(ET).strftime("%H:%M"))
        )
        await mark_posted(store, p.id, now, deadline, mid)
        posted.append(p.id)
    return posted


async def pitch_counts_since(store: Store, since: datetime) -> dict[str, int]:
    rows = await store.fetchall(
        "SELECT analyst, COUNT(*) AS n FROM pitches WHERE filed_at >= ? GROUP BY analyst",
        (utc_iso(since),),
    )
    return {r["analyst"]: int(r["n"]) for r in rows}


def desk_summary(
    chain: str, results: Mapping[str, str], counts: Mapping[str, int], day: date
) -> str:
    label = "evening" if chain == "desk_evening" else "pre-open"
    parts = [f"{a} {n}" for a, n in sorted(counts.items())] or ["no pitches"]
    bad = [f"{j} {v}" for j, v in results.items() if v not in ("done", "noop")]
    tail = f" | ⚠️ {', '.join(bad)}" if bad else ""
    return f"🗂️ DESK {label} {day.isoformat()}: " + " · ".join(parts) + tail
```

- [ ] **Step 5: The engine — chains, PM hooks, bearers**

In `engine/tc/main.py`:
- Import `CHAINED_JOBS, DESK_CHAINS, JOB_SPECS` from `tc.jobs.spec`.
- Import `desk_summary, pitch_counts_since, post_proposals` from `tc.desk.post`.
- Import `ensure_book` from `tc.desk.paper`.
- Add `"desk_evening", "desk_preopen"` to `JOBS` before `*CLAUDE_JOBS`.
- In `_execute`, before the `CLAUDE_JOBS` branch:

```python
        if job in DESK_CHAINS:
            return await self._job_desk_chain(job, now, ignore_window=ignore_window)
```

  (`ignore_window` reaches the chain's analysts, so an operator can seed a
  first evening with `tc run --once desk_evening --ignore-window` after
  20:00. No scheduled fire ever sets it.)

- Replace `_job_claude` (from Task 8) with this version, which carries the
  PM hooks:

```python
    async def _job_claude(
        self, job: str, now: datetime, *, ignore_window: bool = False
    ) -> tuple[Verdict, dict[str, Any]]:
        if self._jobs is None:
            return "noop", {"skipped": "no runner configured"}
        if job == "pm":
            await self._ensure_paper_book(now)
        self._active.name = job
        try:
            verdict, detail = await self._jobs.execute(job, now, ignore_window=ignore_window)
        finally:
            self._active.name = None
        if job == "pm":
            # Funded calls became proposals during the run; they go to Discord
            # now, each with its own veto deadline (spec §9.4).
            posted = await post_proposals(
                self._store, self.notifier, self._rules, self._s.desk, self._clock()
            )
            detail = {**detail, "proposals_posted": posted}
        return verdict, detail

    async def _ensure_paper_book(self, now: datetime) -> None:
        """The paper book starts at the first PM run, with the real account's
        value as its cash (spec §10: legacy positions count as cash)."""
        acct = await self._store.latest_account()
        if acct is not None:
            await ensure_book(self._store, self._et(now).date(), acct.liquidation_value, now)

    async def _job_desk_chain(
        self, chain: str, now: datetime, *, ignore_window: bool = False
    ) -> tuple[Verdict, dict[str, Any]]:
        """One engine job, several Claude jobs, strictly in order: each
        sub-job gets its own `job_runs` row (so the ledger says which analyst
        failed), and a failure moves the chain on rather than stopping it."""
        results: dict[str, str] = {}
        for job in DESK_CHAINS[chain]:
            started = self._clock()
            try:
                verdict, detail = await self._job_claude(job, started, ignore_window=ignore_window)
            except Exception as e:
                log.exception("desk chain %s: %s raised", chain, job)
                verdict, detail = "failed", {"error": type(e).__name__}
            await self._record(job, started, self._clock(), verdict, {**detail, "chain": chain})
            results[job] = verdict
        counts = await pitch_counts_since(self._store, now)
        await self.notifier.post(desk_summary(chain, results, counts, self._et(now).date()))
        ran = [v for v in results.values() if v not in ("noop", "missed")]
        failed = [v for v in ran if v in FAILED_VERDICTS]
        chain_verdict: Verdict = "failed" if ran and len(failed) == len(ran) else "done"
        return chain_verdict, {"jobs": results, "pitches": counts}
```

- In `build_engine`, give the runner client both bearers:

```python
                role_token=settings.mcp_research_token,
                role_tokens=(
                    {"decide": settings.mcp_decide_token} if settings.mcp_decide_token else None
                ),
```

  Also delete the stale comment there ("the decide token is Plan 1's and is
  not handed out here").

- [ ] **Step 6: Consistency, schedule, expectation**

In `engine/tc/rules/consistency.py`, `check_schedule_vs_doc`:

```python
    from tc.jobs.spec import CHAINED_JOBS, JOB_SPECS
    ...
    for job in sorted(set(JOB_SPECS) - CHAINED_JOBS - set(schedule)):
```

In `tests/engine/unit/test_main.py`,
`test_every_scheduled_job_in_the_repo_config_is_a_known_job`, import
`CHAINED_JOBS` from `tc.jobs.spec` and assert
`set(CLAUDE_JOBS) - CHAINED_JOBS <= set(cfg["schedule"])`.

Add to `config.yml` `schedule:`:

```yaml
  # Trading desk (trading-desk design §4). Analysts run as chains so the
  # one-job-at-a-time runner never sees two of them at once.
  desk_evening: "at 16:30 weekdays"   # technical -> earnings -> news -> macro
  desk_preopen: "at 08:00 weekdays"   # news -> earnings, short mode
  pm:           "at 09:50 weekdays"   # the one heavy Claude run in market hours
  pm_midday:    "at 12:30 weekdays"   # held positions only
```

Add to `config.yml` `expectations:`:

```yaml
  - name: job_verdict_not_failed      # a failed analyst, PM or bars run shows in the 07:30 digest
    check: job_verdict_not
    arg: failed
    window_sessions: 1
```

- [ ] **Step 7: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine && cd .. && scripts/check-consistency.sh | tail -2`
Expected: PASS and `CONSISTENT`. The single-job-agent equality tests in
`test_prompts.py` now cover technical and macro. The shared-agent union
tests cover news, earnings and pm.

- [ ] **Step 8: Commit**

```bash
git add engine/tc/jobs/spec.py engine/tc/jobs/dispatch.py engine/tc/desk/post.py engine/tc/main.py \
  engine/tc/rules/consistency.py config.yml tests/engine/unit/test_desk_jobs.py \
  tests/engine/unit/test_desk_engine.py tests/engine/unit/test_jobs_dispatch.py \
  tests/engine/unit/test_main.py
git commit -m "desk: the analyst chain, the PM run and its proposals; each role presents its own bearer"
```

---
### Task 18: Retire the old research jobs

**Files:**
- Modify: `engine/tc/jobs/spec.py`, `engine/tc/jobs/dispatch.py`, `engine/tc/main.py`, `engine/tc/mcp/registry.py`, `engine/tc/rules/consistency.py`
- Modify: `config.yml` (remove six schedule lines)
- Modify: `scripts/check-consistency.sh` (remove check 5 only)
- Delete:
  - `engine/tc/mcp/tools_research.py`, `engine/tc/research/cohort.py`
  - `tests/engine/unit/test_tools_research.py`, `tests/engine/unit/test_cohort.py`
- Tombstone:
  - `.claude/commands/{research,deep-research,scout,catalyst,sector-tag}.md`
  - `.claude/agents/{research-scout,deep-research,scout,catalyst,sector-tagger}.md`
- Port:
  - `tests/engine/unit/test_jobs_dispatch.py`, `test_jobs_spec.py`, `test_main.py`, `test_prompts.py`, `test_consistency.py`
  - `tests/engine/contract/test_mcp_no_order_tools.py`

**What stays:**
- `tc/research/docs.py`, `ledgers.py` and `importer.py` stay. The weekly
  universe sweep and the v2 importer use them, and the history tables
  (`evidence`, `escalations`, `screen_rows`, `tombstones`, …) stay readable
  (spec §13).
- `Store.upsert_sectors`/`sectors` stay as history accessors.

- [ ] **Step 1: Tombstone the ten prompt files**

Replace each file's whole content with a tombstone. Use this exact shape;
the first line carries `RETIRED`, and the body names `engine` (see
`test_retired_files_are_tombstones_that_point_at_the_engine`):

`.claude/commands/research.md`:

```markdown
# /research — RETIRED (trading desk, 2026-09-27)

The hourly research pass -- WATCH/HOT rows in `research/candidates.md` -- is
replaced by the trading desk: four analysts file scored pitches through typed
engine tools and a portfolio manager makes the calls (`engine/tc/desk/`; jobs
`desk_evening`, `desk_preopen`, `pm` in `engine/tc/jobs/spec.py`). In 76 passes
it promoted three names and nothing could act on them:
`docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md` §1.

Do not re-create this file.
```

For the other nine, use the same three paragraphs with the first line and
the first sentence changed as follows. The second paragraph (the desk and
its jobs) is identical in all ten.

| File | First line | First sentence |
|---|---|---|
| `.claude/commands/deep-research.md` | `# /deep-research — RETIRED (trading desk, 2026-09-27)` | `The pre-open and post-close deep runs are replaced by the trading desk's evening analyst chain and pre-open pass, and by engine-computed briefings and scoring (engine/tc/desk/briefing.py, scoring.py).` |
| `.claude/commands/scout.md` | `# /scout — RETIRED (trading desk, 2026-09-27)` | `The information-edge scout observed three names a day from a cohort of up to 215 and raised no escalation in 13 runs; it is replaced by the trading desk.` |
| `.claude/commands/catalyst.md` | `# /catalyst — RETIRED (trading desk, 2026-09-27)` | `The catalyst sweep is replaced by the trading desk's news/catalyst analyst.` |
| `.claude/commands/sector-tag.md` | `# /sector-tag — RETIRED (trading desk, 2026-09-27)` | `Sector tags existed only to build the scout's cohort; the desk's analysts are split by approach, not sector.` |
| `.claude/agents/research-scout.md` | `# research-scout agent — RETIRED (trading desk, 2026-09-27)` | (as `/research`) |
| `.claude/agents/deep-research.md` | `# deep-research agent — RETIRED (trading desk, 2026-09-27)` | (as `/deep-research`) |
| `.claude/agents/scout.md` | `# scout agent — RETIRED (trading desk, 2026-09-27)` | (as `/scout`) |
| `.claude/agents/catalyst.md` | `# catalyst agent — RETIRED (trading desk, 2026-09-27)` | (as `/catalyst`) |
| `.claude/agents/sector-tagger.md` | `# sector-tagger agent — RETIRED (trading desk, 2026-09-27)` | (as `/sector-tag`) |

The retired agent files carry **no frontmatter**, matching the existing
`trader.md` tombstone.

- [ ] **Step 2: Remove the old jobs, relays and tools**

`engine/tc/jobs/spec.py`:
- Delete `Escalation`, `HotFresh`, `ScoutVerdict`, `CatalystVerdict`,
  `DeepVerdict`, `ResearchVerdict` and `SectorVerdict`.
- Delete `_scout_noop`, `_catalyst_noop` and `_deep_noop`.
- Delete `_WEB_AND_READ`, `_SEARCH_AND_READ` and `tools_for`.
- Delete the six old entries. `JOB_SPECS` becomes
  `JOB_SPECS: dict[str, JobSpec] = dict(DESK_SPECS)`: move the `JOB_SPECS`
  definition below `DESK_SPECS` and drop `JOB_SPECS.update(...)`.
- Rewrite the module docstring's last paragraph to say that budgets, windows
  and timeouts are the desk's (trading-desk design §4, §13), not v2
  carry-overs.

`engine/tc/jobs/dispatch.py`:
- Import only `JOB_SPECS, JobSpec, output_schema` from `tc.jobs.spec`.
- Set `SUMMARY_JOBS = frozenset({"pm", "pm_midday"})`.
- In `_relay`, delete the two `isinstance` relay blocks (HOT-FRESH and
  ESCALATE).
- In `_prompt_extra`, delete the `preopen`/`postclose` mode lines: the desk's
  modes are in each spec's own prompt.
- Update the module docstring's `content_failed` paragraph to say "an analyst
  pass" where it says "a research pass".

`engine/tc/mcp/registry.py`:
- Delete `RESEARCH_TOOLS` and the comment above it.
- Set `"research": COMMON_TOOLS + READ_TOOLS + ANALYST_TOOLS`.

`engine/tc/main.py`:
- Remove `tools_research` from the import.
- Remove the `mcp_server.register("research", tools_research.register)` line.
- Update the `_wire_mcp_registrars` comment: the research role carries the
  analysts' desk tools.

Delete `engine/tc/mcp/tools_research.py`, `engine/tc/research/cohort.py`,
`tests/engine/unit/test_tools_research.py` and `tests/engine/unit/test_cohort.py`.

`config.yml`:
- Delete the `scout`, `catalyst`, `preopen`, `postclose`, `research` and
  `sector_tag` schedule lines.
- Delete the comment block above them ("Task 12 (jobs/spec.py): the six
  research-role jobs…").

- [ ] **Step 3: The consistency checkers**

`engine/tc/rules/consistency.py`:
- Delete `RESEARCH_DOC`, `DOC_MINUTE`, `DOC_HOURS`, `CLOCK` and
  `_check_research_cadence`.
- In `check_schedule_vs_doc`, delete the
  `out.extend(_check_research_cadence(...))` line and return
  `out, len(schedule)`.
- In its docstring, drop the sentence about `research`'s cadence.

`scripts/check-consistency.sh`: replace the whole block from
`# --- 5. the schedule matches what the command files claim ---` through the
`fi` just before `# --- 6.` with:

```bash
# --- 5. (retired 2026-09-27) ----------------------------------------------
# This compared docker/crontab against the v2 command files. The v2 runtime
# was retired 2026-09-07 and those command files are tombstones; the v3
# schedule is data in config.yml, checked by engine/tc/rules/consistency.py.
```

- [ ] **Step 4: Port the tests**

`tests/engine/unit/test_prompts.py`:
- `LIVE_COMMANDS: list[str] = []`.
- `LIVE_AGENTS = ["analyst-technical", "analyst-earnings", "analyst-news", "analyst-macro", "pm"]`.
- Append the ten tombstoned paths to `RETIRED`.
- Delete `test_research_md_keeps_the_schedule_strings_check_5_greps`.

`tests/engine/unit/test_jobs_spec.py`:
- Import `JOB_SPECS, AnalystVerdict, PmVerdict, output_schema, tools_for_role`.
- Delete `test_noop_predicates_fire_only_on_a_genuinely_empty_pass`,
  `test_deep_verdict_accepts_a_postclose_with_no_hot_fresh`,
  `test_research_and_sector_tag_noop_predicates_are_none` and
  `test_catalyst_and_deep_noop_predicates`.
- Replace the following three tests:

```python
def test_schema_forbids_extra_keys_so_a_stray_field_fails_the_verdict() -> None:
    schema = output_schema(AnalystVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"pitched", "withdrawn", "summary"}


def test_the_inlined_schema_still_constrains_the_nested_model() -> None:
    """Inlining must move the definition, not drop it: `HeldDecision` keeps
    its own `additionalProperties: false`, required keys and action enum."""
    held = output_schema(PmVerdict)["properties"]["held"]["items"]
    assert held["type"] == "object" and held["additionalProperties"] is False
    assert set(held["required"]) == {"symbol", "action", "reason"}
    assert held["properties"]["action"]["enum"] == ["hold", "exit", "tighten"]


def test_tools_for_rejects_a_tool_no_role_has() -> None:
    with pytest.raises(KeyError):
        tools_for_role("research", "doc_delete")
    with pytest.raises(KeyError):
        tools_for_role("research", "call_submit")      # a decide tool
```

- `test_verdict_models_forbid_extra_fields` constructs
  `AnalystVerdict(pitched=[], withdrawn=[], summary="", bogus=1)` under the
  same `# type: ignore[call-arg]`.

`tests/engine/unit/test_jobs_dispatch.py` (mechanical; the assertions keep
their meaning):
- Replace the constants block:

```python
# 16:45 ET on a Tuesday -- inside the analysts' 16:25-20:00 window.
IN_WINDOW = datetime(2026, 9, 8, 20, 45, tzinfo=UTC)
OUT_WINDOW = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)  # 11:00 ET
JOB = "analyst_technical"
```

- In `GOOD`, set
  `"verdict_raw": {"pitched": [1, 2], "withdrawn": [], "summary": "TECHNICAL 2 pitches"}`.
- Everywhere:
  - `"scout"` becomes `JOB`, and `JOB_SPECS["scout"]` becomes `JOB_SPECS[JOB]`.
  - `detail["observed"] == 3` becomes `detail["pitched"] == [1, 2]`.
  - `"2026-09-07" in seen["prompt"]` becomes `"2026-09-08" in seen["prompt"]`.
  - `"⚠️ scout content_failed:"` becomes `"⚠️ analyst_technical content_failed:"`.
- `test_a_verdict_with_the_wrong_shape_is_content_failed`: set
  `verdict_raw` to
  `{"pitched": "four", "withdrawn": [], "summary": "x"}` and expect
  `detail["errors"] == ["pitched: list_type"]`.
- Replace `test_empty_cohort_is_noop_not_done` with:

```python
async def test_zero_pitches_is_done_not_noop(notifier: RecordingNotifier) -> None:
    """An analyst that finds nothing has still done its job; the chain
    summary reports the count. No desk job has a noop predicate."""
    payload = {**GOOD, "verdict_raw": {"pitched": [], "withdrawn": [], "summary": "TECHNICAL 0"}}
    verdict, _ = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done"
```

- Replace the four text-recovery tests
  (`test_verdict_recovered_from_text_when_structured_output_missing`,
  `…_from_a_fenced_json_block`, `test_text_with_a_failing_json_object_stays_content_failed`,
  `test_structured_output_still_wins_over_text`) with:

```python
RECOVERED = '{"pitched": [3], "withdrawn": [], "summary": "TECHNICAL 1 pitch"}'


async def test_verdict_recovered_from_text_when_structured_output_missing(
    notifier: RecordingNotifier,
) -> None:
    payload = {**GOOD, "verdict_raw": None, "result_text": RECOVERED}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done"
    assert detail["verdict_source"] == "text" and detail["pitched"] == [3]


async def test_verdict_recovered_from_a_fenced_json_block(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "verdict_raw": None,
               "result_text": f"Here it is.\n```json\n{RECOVERED}\n```\n"}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done" and detail["verdict_source"] == "text"


async def test_text_with_a_failing_json_object_stays_content_failed(
    notifier: RecordingNotifier,
) -> None:
    text = '{"pitched": "3", "withdrawn": [], "summary": "x"}'  # pitched must be a list
    payload = {**GOOD, "verdict_raw": None, "result_text": text}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "content_failed"
    assert detail["reason"] == "no structured output" and "verdict_source" not in detail


async def test_structured_output_still_wins_over_text(notifier: RecordingNotifier) -> None:
    payload = {**GOOD, "result_text": RECOVERED}
    verdict, detail = await _jr(_ok(payload), notifier).execute(JOB, IN_WINDOW)
    assert verdict == "done" and "verdict_source" not in detail
    assert detail["pitched"] == [1, 2]
```

- Replace `test_the_deep_runs_are_told_which_half_to_execute` with:

```python
async def test_preopen_jobs_run_in_preopen_mode(notifier: RecordingNotifier) -> None:
    seen: dict[str, Any] = {}

    def h(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=GOOD)

    at = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)  # 08:30 ET, inside pre-open
    await _jr(h, notifier).execute("preopen_news", at)
    assert "PRE-OPEN MODE" in seen["prompt"]
```

- Delete `test_escalations_are_relayed_one_line_each`,
  `test_hot_fresh_is_relayed_with_its_reference_price`,
  `test_the_deep_runs_relay_their_summary_line` and
  `test_a_deep_run_that_wrote_nothing_is_a_noop_and_says_nothing`. The
  relays are gone; the PM's summary relay is pinned by Task 17's
  `test_a_decide_job_presents_the_decide_bearer`.

`tests/engine/unit/test_main.py` (mechanical):
- Rename `SCOUT_AT` to `ANALYST_AT = datetime(2026, 9, 4, 20, 45, tzinfo=UTC)`
  (16:45 ET).
- Rename `SCOUT_RESULT` to `ANALYST_RESULT`, with
  `"verdict_raw": {"pitched": [], "withdrawn": [], "summary": "TECHNICAL 0"}`.
- Every job name `"scout"` (in `run_job`, the CLI `--once` argument, the
  pinger tuples and `consecutive_failures` keys) becomes `"analyst_technical"`.
- The comment "07:15 ET, inside scout's window" becomes "16:45 ET, inside
  the analysts' window".
- In the MCP-mount test, `assert "quotes" in research and "doc_write" in research`
  becomes `assert "quotes" in research and "pitch_submit" in research`.

After porting, this command finds nothing but role names and comments:
`grep -rnE '"(scout|catalyst|postclose|preopen|research|sector_tag)"' tests/engine`.
Fix anything else it finds the same way.

`tests/engine/unit/test_consistency.py`:
- Delete `test_research_cadence_drifting_from_its_command_file_is_found`
  and `test_a_command_file_that_stops_stating_the_cadence_is_found`.
- In the "has a job spec but no schedule entry" test, filter out the line
  starting `"  pm:"` instead of `"  catalyst:"`.

`tests/engine/contract/test_mcp_no_order_tools.py`:
- Remove `tools_research` from the import and from the research registrar
  list.
- Delete the `evidence_append` assertion line in
  `test_the_two_roles_have_disjoint_write_surfaces`.

- [ ] **Step 5: Run everything**

Run: `cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine && cd .. && scripts/check-consistency.sh | tail -2`
Expected:
- The test count falls by the deleted research/cohort tests and rises by the
  desk tests.
- The suite, types and lint all pass.
- Both checkers pass (the bash one ends `CONSISTENT`).

- [ ] **Step 6: Commit**

```bash
git add engine/tc/jobs/spec.py engine/tc/jobs/dispatch.py engine/tc/main.py \
  engine/tc/mcp/registry.py engine/tc/rules/consistency.py config.yml scripts/check-consistency.sh \
  .claude/commands/research.md .claude/commands/deep-research.md .claude/commands/scout.md \
  .claude/commands/catalyst.md .claude/commands/sector-tag.md .claude/agents/research-scout.md \
  .claude/agents/deep-research.md .claude/agents/scout.md .claude/agents/catalyst.md \
  .claude/agents/sector-tagger.md tests/engine/unit/test_prompts.py tests/engine/unit/test_jobs_spec.py \
  tests/engine/unit/test_jobs_dispatch.py tests/engine/unit/test_main.py \
  tests/engine/unit/test_consistency.py tests/engine/contract/test_mcp_no_order_tools.py
git rm engine/tc/mcp/tools_research.py engine/tc/research/cohort.py \
  tests/engine/unit/test_tools_research.py tests/engine/unit/test_cohort.py
git commit -m "desk: retire scout, catalyst, research, pre/postclose and sector_tag; the desk replaces them"
```

---

### Task 19: The playbook, the retired strategy keys, the changelog

**Files:**
- Rewrite: `strategy.md`
- Modify: `rules.yml` (delete retired strategy keys)
- Modify: `engine/tc/rules/consistency.py` (retired strategy keys must stay absent)
- Modify: `scripts/test-pre-order-check.sh` (drop the two scout-window assertions)
- Modify: `tests/engine/unit/test_tools_read.py` (line ~411: a key that still exists)
- Modify: `tests/engine/unit/test_consistency.py` (one new test)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write the failing test**

Append to `tests/engine/unit/test_consistency.py`:

```python
def test_a_retired_strategy_key_coming_back_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    rules = root / "rules.yml"
    rules.write_text(rules.read_text().replace("strategy:\n", "strategy:\n  sleeve_core_pct: 50\n", 1))
    rep = run_checks(root)
    assert any("sleeve_core_pct" in f.message for f in rep.findings if f.check == "dead_keys")
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_consistency.py -k retired`
Expected: FAIL.

- [ ] **Step 2: Delete the retired keys and guard them**

In `rules.yml`'s `strategy:` section, delete these keys, with their comment
blocks:

- `sleeve_core_pct`, `sleeve_catalyst_pct`, `max_deployed_pct`
- `catalyst_min_whole_shares`
- `stall_rule_consecutive_closes`, `stall_rule_from_session`
- `ratchet_breakeven_at_gain_pct`, `ratchet_entry_plus8_at_gain_pct`
- `option_expiry_min_days_past_earnings`, `option_expiry_max_days_past_earnings`
- `scout_entry_window_min_days`, `scout_entry_window_max_days`

Keep:

- `sleeve_options_open_pct` and `sleeve_leveraged_pct`: both checkers'
  tightness tables read them.
- `leveraged_exit_session`, `max_daily_atr_pct`, `min_session_range_pct`,
  `universe_max_failed_chunk_pct` and `working_universe_size`.
- The `tick_interval_*` keys.

Put this comment where the sleeves were:

```yaml
  # The core/catalyst sleeves, the stall and ratchet exits, the earnings-timed
  # option windows and the catalyst share granularity were retired 2026-09-27
  # with the trading desk (strategy.md §12). engine/tc/rules/consistency.py
  # fails the build if any of them returns.
```

In `engine/tc/rules/consistency.py`, add:

```python
# Strategy keys retired with the trading desk (2026-09-27, strategy.md §12).
# Same practice as DEAD_KEYS: a rule that was removed must not return by
# accident, because a stale key is a rule a reader still believes in.
RETIRED_STRATEGY_KEYS = (
    "sleeve_core_pct", "sleeve_catalyst_pct", "max_deployed_pct", "catalyst_min_whole_shares",
    "stall_rule_consecutive_closes", "stall_rule_from_session",
    "ratchet_breakeven_at_gain_pct", "ratchet_entry_plus8_at_gain_pct",
    "option_expiry_min_days_past_earnings", "option_expiry_max_days_past_earnings",
    "scout_entry_window_min_days", "scout_entry_window_max_days",
)
```

In `check_dead_keys`, after the `DEAD_KEYS` loop, add:

```python
    for k in RETIRED_STRATEGY_KEYS:
        if re.search(rf"^\s+{k}:", text, re.MULTILINE):
            out.append(Finding(
                "dead_keys", "rules.yml", None,
                f"carries '{k}' — retired with the trading desk 2026-09-27 (strategy.md §12)",
            ))
    return out, len(DEAD_KEYS) + len(RETIRED_STRATEGY_KEYS)
```

(Replace the function's existing `return`.)

In `scripts/test-pre-order-check.sh`, delete the six lines that read and
assert `scout_entry_window_min_days`/`scout_entry_window_max_days` (they
begin `wmin=` and `wmax=`). Those keys no longer exist.

In `tests/engine/unit/test_tools_read.py`, change
`assert out.strategy["scout_entry_window_min_days"] == "21"` to
`assert out.strategy["working_universe_size"] == "500"`.

- [ ] **Step 3: Rewrite `strategy.md`**

Replace the whole file with the text below.

**The markers are shown spaced in this plan, as `<!-- rule:KEY -->`, so the
plan itself does not trip the annotation checkers.** After writing the file,
un-space them:

```bash
sed -i '' 's/<!-- rule:\([A-Za-z0-9_]*\) -->/<!--rule:\1-->/g' strategy.md
grep -o '<!--rule:' strategy.md | wc -l     # expect 23
```

(`sed -i ''` is the macOS form; on Linux use `sed -i`.)

````markdown
# Trading Strategy Playbook

**The playbook.** How the account is traded, inside the box that `CLAUDE.md`
defines. Required reading at every session open.

**Governed by `CLAUDE.md`. Where this document and the manual disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules here are *strategy rules*: stricter than or inside the manual,
discretionary, and changeable without a §9 amendment. Their numbers live in
`rules.yml`; the markers below bind each stated number to it.

**Rewritten 2026-09-27** for the trading desk
(`docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md`). What the
previous playbook said, and why it went, is in `CHANGELOG.md` and §12.

---

## 1. Goal

- **Test Claude as a trader.** Chris, 2026-09-27. Claude's judgement picks the
  trades; the question is whether it beats holding SPY.
- **The verdict is pre-registered** (§8) and was fixed before any result
  existed.
- **The box is still the point.** `CLAUDE.md` §1's prohibitions make near-zero
  the probability of destroying real money, and they bind the desk exactly as
  they bound everything before it.
- **Honest expectation:** the most likely result is no reliable edge. The desk
  exists to find that out cheaply, on the record, and — if there is an edge —
  to show where it is.

## 2. Operating constraints

| Constraint | Consequence |
|---|---|
| Cash account, T+1, zero GFV tolerance | Settled cash gates every buy; the reserve invariant (§3) keeps a buffer |
| Schwab token dies after 7 days | Re-auth weekly from the phone; a dead token makes the engine BLIND and the desk skips its day |
| The server shares Chris's Claude subscription | Heavy Claude work happens outside market hours; only the PM runs in them. Build sessions stay outside 09:30–16:30 ET |
| Every entry passes a Discord veto window | Chris's availability does not cap the trade count; his ❌ still stops any entry |
| **Paper phase** | Until revised Plan 1 lands and the §9 amendment in spec §12.3 is committed, funded calls fill a paper book and nothing reaches Schwab (§10) |

## 3. Capital, sizing and the reserve

**The reserve invariant**: **total cash (settled + unsettled) ≥ $900.00 at
all times.** A buy that would breach it is refused in code.

Every percentage is of **account value** — during the paper phase, of the
paper book's equity. Conviction sets the size:

| Conviction | Shares/ETF | Option premium |
|---|---|---|
| 1–2 | never funded — scored only | never funded |
| 3 (the funding floor, **3**<!-- rule:strategy_min_fundable_conviction -->) | **10%**<!-- rule:strategy_size_shares_pct_conviction_3 --> | **5%**<!-- rule:strategy_size_option_premium_pct_conviction_3 --> |
| 4 | **15%**<!-- rule:strategy_size_shares_pct_conviction_4 --> | **7.5%**<!-- rule:strategy_size_option_premium_pct_conviction_4 --> |
| 5 | **20%**<!-- rule:strategy_size_shares_pct_conviction_5 --> | **10%**<!-- rule:strategy_size_option_premium_pct_conviction_5 --> (the §3.2 cap) |

- At most **8**<!-- rule:strategy_max_funded_positions --> funded positions,
  legacy holdings and pending proposals included.
- Share funding requires daily ATR ≤ **6%**<!-- rule:strategy_max_daily_atr_pct -->;
  a noisier name can be expressed only as an option.
- `CLAUDE.md` §3.1 (single position), §3.2 (option premium, per position and
  open) and §3.8 (the correlated cluster) are checked in code at proposal
  time. **§3.8 is a cap, never a same-sector ban**: correlated means the same
  sector benchmark (SPY clusters nothing) or a 60-day return correlation over
  the manual's threshold.

## 4. The desk

| ET | Who | What |
|---|---|---|
| 16:10 | engine | Daily bars for the liquid universe's head by dollar volume, the ETF list, context rows and every carried symbol; then scoring |
| 16:30 → ~18:10 | analysts, in a chain | technical → earnings → news/catalyst → macro/ETF (Sonnet). Each reads its engine-computed briefing and files pitches |
| 08:00 | news, earnings | Pre-open mode: overnight news and pre-market reports; new pitches and withdrawals |
| **09:50** | **PM** (Opus) | Book, pitches, calls, funding. The one heavy Claude run in market hours |
| 12:30 | PM, midday mode | Held positions and today's news only; no new calls |
| every 5 min 09:55–15:55 | engine (`desk_watch`) | Paper entries at the veto deadline; paper exits |
| every 15 min | engine (`tick`) | The real book's watches, unchanged |
| Sat 08:30 | engine | The scorecard, posted |

Analysts file **pitches**. The PM makes **calls**. The engine stamps every
reference price from broker reads — **no price the model types is ever a
reference** — sizes, fills, exits and scores.

## 5. Pitches

- At most **5**<!-- rule:strategy_desk_max_pitches_per_analyst_run --> per
  analyst per evening run, and at most
  **2**<!-- rule:strategy_desk_preopen_max_new_pitches --> new in the pre-open
  run. Filed only outside the regular session.
- Horizon **2**<!-- rule:strategy_pitch_horizon_min_days -->–**20**<!-- rule:strategy_pitch_horizon_max_days -->
  trading days; target and invalidation on the correct sides of the price and
  within **30%**<!-- rule:strategy_pitch_level_max_distance_pct --> of it.
- One open pitch per analyst per symbol and direction.
- The benchmark is SPY or one of the 11 sector SPDRs; ETF pitches use SPY.
- A pitch's reference is its session's opening print. It may be withdrawn
  only before that open; after it, it can only resolve.

## 6. Calls and options

- At most **5**<!-- rule:strategy_desk_max_new_calls_per_day --> new calls a
  day, adopting a pitch or originating one. Extensions and legacy calls do
  not count. One open call per symbol and direction.
- A call's reference is the live quote when it is made; SPY and the benchmark
  are stamped with it. A quote older than five minutes is refused (§4.10).
- **Up calls** are funded with shares, an ETF or a long call; **down calls**
  with a long put. No leveraged or inverse ETFs.
- **Option contracts** come only from the engine's candidate list: every
  `CLAUDE.md` §3.2 floor (days to expiry, delta band, open interest, spread,
  premium cap) must hold, with expiry long enough to outlast the horizon plus
  the §3.3 5-DTE close plus a week, and at most
  **60**<!-- rule:strategy_option_max_dte --> days out. Candidates rank toward
  delta **0.60**<!-- rule:strategy_option_target_delta -->; the strategy's
  delta floor equals the manual's, **0.45**<!-- rule:strategy_option_min_delta -->.

## 7. Entries and exits

**Entries.** A funded call becomes a Discord proposal. It executes
**10**<!-- rule:strategy_veto_window_minutes --> minutes after posting (never
before 10:00 ET) unless Chris reacts ❌; ✅ executes at once. The PM sets a
maximum entry price no more than **5%**<!-- rule:strategy_max_entry_chase_pct -->
above the last; if the ask has run past it at execution, the entry is skipped
and the call is still scored. Proposals go out between 10:00 and 15:00;
unfilled entries end at 15:55 (§4.2).

**Exits are engine code, with no veto** — closing orders only reduce risk:
- Shares carry a stop whose trigger is the **higher** of the §3.4 formula and
  the call's invalidation (a trigger may be raised, never lowered).
- Target reached → sell at the bid. Options exit on their underlying crossing
  the invalidation or reaching the target, and at 5 DTE (§3.3).
- A funded call that resolved at the previous close exits after 10:00 unless
  the PM extends it — once.
- The PM may exit or tighten at 09:50 and 12:30.

## 8. Scoring and the checkpoint — pre-registered

Every pitch and every call resolves once, from daily bars: target touched,
invalidation touched (both on one day counts as invalidation), or the
horizon's close. Return is signed by direction; **excess** is that return
minus SPY's over the same window, and separately minus the benchmark's.

**The checkpoint.** On 2026-12-31, or the first day after it with at least
**40**<!-- rule:strategy_checkpoint_min_pm_calls --> resolved PM calls:

- **Keep** if the PM's calls beat SPY on mean excess **and** the paper book's
  return at least matched SPY's over the same period.
- **Stop Claude-directed entries** if the PM's calls trail SPY **and** trail
  the average pitch — the PM is subtracting value; what follows is a
  conversation with Chris.
- **Rework** otherwise.
- Any analyst with **15**<!-- rule:strategy_analyst_review_min_pitches -->+
  resolved pitches and negative mean excess against its benchmarks is dropped
  or rebuilt.

This is a decision rule, not a significance test: bootstrap intervals are
shown once a group has **20**<!-- rule:strategy_scorecard_ci_min_n -->
resolved items, for honesty, not as the gate. The rule changes only by a
recorded amendment quoting Chris. `CLAUDE.md` §3.6's halt protects the money
throughout, independently.

## 9. Session protocol and deadman checks

The engine runs unattended; a session is a human or Claude looking in.

**Open:** `CLAUDE.md` §4.5 reconciliation (the engine's latest reconcile and
`/health`), `scripts/check-consistency.sh` and `tc check-consistency` (a FAIL
is a defect to fix before anything else), §3.6 from the latest session close,
`ALERT.md`. Then the desk:
- `/health`: `blind`, `token_days_until_dead`, `runner_ok`.
- `job_runs` for the last day: every desk job `done` or `noop`, none
  `failed`; the 07:30 expectations digest says the same in Discord.
- `/api/scorecard`: the checkpoint status and any analyst flag.

**Deadman checks** — absence of action is never evidence nothing needed
doing (`CLAUDE.md` §0):
- No `🗂️ DESK evening` post by 18:30 ET on a trading day → the chain did not
  run.
- No PM summary by 10:35 ET → the PM missed its window; no entries that day.
- `bars_refresh` `failed` → no scoring that evening; it catches up next time.

## 10. The paper book and going live

The paper book starts at the first PM run with the account's value as cash
(legacy positions count as cash) and applies every cap above. Vetoed
proposals still fill in it, flagged, because it records Claude's decisions.

Real orders are switched on only when all three hold:

- revised Plan 1 passes its own paper end-to-end test;
- the desk has run about two weeks of clean paper days;
- the §9 amendment in spec §12.3 is committed with Chris's words quoted.

The paper book keeps running after that, so the record has no gap.

## 11. Open items

1. The `CLAUDE.md` §9 amendment for the veto window (spec §12.3) — needs
   Chris's words; not needed for the paper phase.
2. Revised Plan 1 (spec §11).
3. Legacy positions: the PM attaches a call to each or recommends an exit;
   Chris acts on legacy exits at Schwab.

## 12. What changed on 2026-09-27, and why

Six weeks produced four stock entries and no option trades (spec §1). Removed
from this playbook, all strategy rules, none needing §9: the core, catalyst
and options sleeves; the δ 0.50 long-premium floor; the earnings-timed option
entry and expiry windows; the scout escalation requirement and the HOT
checklist (including its confirmed-earnings-date requirement, which §3.7 had
already made context rather than a gate); the stall and ratchet exits
(replaced by target, invalidation and horizon); the 3-whole-share catalyst
granularity; the NVDA-week guard; the research alert cap. The retired keys are
guarded against returning by `engine/tc/rules/consistency.py`.
````

Count check: the file has 23 markers:

| Group | Markers |
|---|---|
| Sizing (3 share + 3 option percentages + the conviction floor) | 7 |
| Positions, ATR | 2 |
| Pitch caps, horizon min/max, level distance | 5 |
| New calls, max DTE, target delta, delta floor | 4 |
| Veto, chase | 2 |
| Checkpoint, analyst review, CI | 3 |

Fewer than 23 means a marker lost its closing `-->` in the paste.

- [ ] **Step 4: The changelog**

Prepend to `CHANGELOG.md`'s entries (follow the file's existing heading
style for a dated entry):

```markdown
## 2026-09-27 — The trading desk (strategy rewrite; no manual change)

Six weeks produced four stock entries and no option trades. Causes, from the
engine database: no order path since 2026-09-07; a funnel of vetoes with no
rule that says yes; option rules whose intersection was empty at this account
size; picks that lagged SPY; research effort spent rewriting documents
(docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md §1).

Chris chose, 2026-09-27: test Claude as a trader; a swing style; four analysts
split by approach plus a portfolio manager; a 10-minute veto window on entries
(a §9 amendment, needed before real orders, not for the paper phase); a shared
subscription with heavy runs outside market hours; a pre-registered checkpoint
on 2026-12-31 once 40 PM calls are scored.

strategy.md was rewritten around the desk. rules.yml: new desk keys; the
strategy delta floor lowered 0.50 → 0.45 (= the manual floor); the sleeve,
stall, ratchet, earnings-window, scout-window and catalyst-granularity keys
retired and guarded. The scout, catalyst, research, pre-open, post-close and
sector-tag jobs were retired. CLAUDE.md is unchanged.
```

- [ ] **Step 5: Run everything, including the manual's gate suite**

Run from the repo root:
`(cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine) && scripts/check-consistency.sh | tail -2 && scripts/test-pre-order-check.sh | tail -3`

Expected: all pass. The bash checker ends `CONSISTENT`. The pre-order
suite's summary shows no failures (`CLAUDE.md` header: run it before any
commit touching `rules.yml`).

- [ ] **Step 6: Commit**

```bash
git add strategy.md rules.yml CHANGELOG.md engine/tc/rules/consistency.py \
  scripts/test-pre-order-check.sh tests/engine/unit/test_tools_read.py tests/engine/unit/test_consistency.py
git commit -m "strategy: the trading desk playbook; the sleeves, stall/ratchet and earnings windows retired"
```

---

### Task 20: A desk day end to end, then deploy on paper

**Files:**
- Test: `tests/engine/unit/test_desk_replay.py`
- Modify: `HANDOFF.md` (new top section)

**Interfaces:**
- Consumes everything above.
- Produces a replay test that pins the whole flow and a deployed paper desk.

- [ ] **Step 1: Write the replay test**

Create `tests/engine/unit/test_desk_replay.py`:

```python
"""Task 20: one desk day and its aftermath, end to end, on the real desk
modules and the fixture broker. An analyst pitches, the PM adopts and funds
it, the proposal posts and fills, bars arrive, both predictions resolve, the
paper position exits at its target, and the scorecard counts it all."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from desk_fixtures import RULES, bar, desk_deps, desk_settings, sessions
from mcp.server.fastmcp import FastMCP

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, DailyBar
from tc.desk.approval import NoReactions
from tc.desk.paper import ensure_book
from tc.desk.post import post_proposals
from tc.desk.scorecard import build_scorecard
from tc.desk.scoring import score
from tc.desk.watch import run_desk_watch
from tc.mcp import tools_desk
from tc.mcp.registry import Role
from tc.mcp.server import McpDeps
from tc.notify import Notifier
from tc.store.db import Store

EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)   # Mon 16:45 ET
PM_AT = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)     # Tue 09:50 ET
FILL_AT = datetime(2026, 9, 29, 14, 10, tzinfo=UTC)   # Tue 10:10 ET
EXIT_AT = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)    # Thu 11:00 ET
D29, D30, D01 = date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)


class Notes(Notifier):
    def __init__(self) -> None:
        super().__init__(None, httpx.AsyncClient())
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True


def _history(price: str) -> list[DailyBar]:
    p = Decimal(price)
    days = [d for d in sessions(date(2026, 5, 1), 150) if d <= date(2026, 9, 28)][-80:]
    return [DailyBar(date=d, open=p, high=p + Decimal("0.5"), low=p - Decimal("0.5"), close=p,
                     volume=1_000_000) for d in days]


def _quotes(d: Path, at: datetime, **px: tuple[float, float, float]) -> None:
    ms = int(at.timestamp() * 1000)
    (d / "quotes.json").write_text(json.dumps({
        s: {"quote": {"lastPrice": last, "bidPrice": bid, "askPrice": ask, "quoteTime": ms}}
        for s, (last, bid, ask) in px.items()
    }))


def _server(role: Role, deps: McpDeps) -> FastMCP:
    s = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(s, deps, role)
    return s


async def test_a_desk_day_end_to_end(desk_store: Store, tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    fx.mkdir()
    for sym, px in (("AAA", "50"), ("SPY", "500"), ("XLK", "200")):
        await desk_store.upsert_bars(sym, _history(px))
    await desk_store.replace_universe(date(2026, 9, 26), [{
        "symbol": "AAA", "price": Decimal(50), "adv10": Decimal(1_000_000),
        "dollar_vol": Decimal(50_000_000), "pct_from_52wk_high": Decimal(1), "optionable": True,
        "leverage": Decimal(0), "last_earnings": "", "is_etf": False,
        "session_range_pct": Decimal("1.5"), "description": "AAA", "qualified": True,
    }])
    await desk_store.record_account(AccountSnapshot(
        account_hash="H", read_at=EVENING, liquidation_value=Decimal("3700.00"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[]))
    desk = desk_settings(tmp_path).desk

    # Monday evening: the technical analyst pitches AAA up.
    research = _server("research", desk_deps(desk_store, tmp_path, FakeBroker(fx, EVENING),
                                             EVENING, "analyst_technical"))
    pitch = await research._tool_manager.call_tool("pitch_submit", {
        "symbol": "AAA", "direction": "up", "thesis": "a tight base under 51 breaking on volume",
        "evidence": [{"url": "https://example.com/aaa", "claim": "base since July",
                      "date": "2026-09-28"}],
        "target": "55", "invalidation": "48", "horizon_days": 5, "conviction": 4,
        "benchmark": "XLK",
    })

    # Tuesday 09:50: the PM adopts it and funds it with shares.
    await ensure_book(desk_store, D29, Decimal("3700.00"), PM_AT)
    _quotes(fx, PM_AT, AAA=(50.0, 49.98, 50.02), SPY=(500.0, 499.9, 500.1), XLK=(200.0, 199.9, 200.1))
    decide = _server("decide", desk_deps(desk_store, tmp_path, FakeBroker(fx, PM_AT), PM_AT, "pm"))
    call = await decide._tool_manager.call_tool("call_submit", {
        "pitch_id": pitch.id, "symbol": "AAA", "direction": "up",
        "thesis": "adopting the technical pitch: base breakout, volume confirms",
        "target": "55", "invalidation": "48", "horizon_days": 5, "conviction": 4,
        "benchmark": "XLK", "funding": "shares", "max_entry_price": "50.50",
    })
    assert call.origin == "pitch" and call.proposal.quantity == 10   # 555.00 // 50.50
    notes = Notes()
    posted = await post_proposals(desk_store, notes, RULES, desk, PM_AT)

    # 10:10: the veto window closes and desk_watch fills at the ask.
    _quotes(fx, FILL_AT, AAA=(50.1, 50.08, 50.12))
    rep = await run_desk_watch(store=desk_store, broker=FakeBroker(fx, FILL_AT), notifier=notes,
                               reactions=NoReactions(), rules=RULES, desk=desk, now=FILL_AT)
    assert rep.filled == posted

    # Bars arrive; AAA reaches 55.5 on Thursday. Both predictions hit.
    await desk_store.upsert_bars("AAA", [bar(D29, 50, 51, 49.5, 50.8), bar(D30, 50.8, 53, 50.5, 52.9),
                                         bar(D01, 53, 55.5, 52.8, 55.2)])
    for sym, px in (("SPY", 500), ("XLK", 200)):
        await desk_store.upsert_bars(sym, [bar(d, px, px, px, px) for d in (D29, D30, D01)])
    resolved = (await score(desk_store)).resolved
    assert {(r.kind, r.how, r.ret_pct) for r in resolved} == {
        ("pitch", "target", Decimal(10)), ("call", "target", Decimal(10)),
    }

    # Thursday 11:00: the paper position exits at its target.
    _quotes(fx, EXIT_AT, AAA=(55.2, 55.1, 55.3))
    rep = await run_desk_watch(store=desk_store, broker=FakeBroker(fx, EXIT_AT), notifier=notes,
                               reactions=NoReactions(), rules=RULES, desk=desk, now=EXIT_AT)
    assert rep.exits == [(posted[0], "target")]

    sc = await build_scorecard(desk_store, RULES, desk, D01)
    assert (sc.pm_calls.n, sc.all_pitches.n, sc.book.closed_trades) == (1, 1, 1)
    assert sc.pm_calls.mean_excess_spy_pct == "10.00"
    assert sc.selection_edge_pct == "0.00"
```

Run: `cd engine && .venv/bin/pytest -q ../tests/engine/unit/test_desk_replay.py`
Expected: PASS. If it fails, the failure names the seam between two tasks;
fix the seam, not the test.

- [ ] **Step 2: Full verification**

Run from the repo root:
`(cd engine && .venv/bin/pytest -q && .venv/bin/mypy && .venv/bin/ruff check tc ../tests/engine) && scripts/check-consistency.sh | tail -2 && scripts/test-pre-order-check.sh | tail -3`
Expected: everything passes. Record the test count.

- [ ] **Step 3: Commit, then ask Chris before pushing**

```bash
git add tests/engine/unit/test_desk_replay.py
git commit -m "desk: a whole desk day end to end on the fixture broker"
```

The repo is public (`CLAUDE.md` §7.4), so before pushing:
- confirm `git diff origin/main --stat` shows no account number, hash or
  token;
- confirm `grep -rn "HASH_REDACTED\|ACCOUNT_REDACTED"` shows only the
  redacted spellings;
- **ask Chris before `git push`**: publishing is outward-facing.

- [ ] **Step 4: Deploy to the Pi, outside market hours, when no Claude job is running**

Check the runner is idle first: `ssh brewmaster 'curl -s http://127.0.0.1:8090/health'`
should show `"busy": false`. Then:

```bash
ssh brewmaster
cd ~/trade-challenge && git fetch origin && git checkout <the pushed sha>
docker compose -f docker/docker-compose.yml build engine runner
docker compose -f docker/docker-compose.yml up -d --force-recreate engine runner
curl -s http://127.0.0.1:8080/health     # ok true, blind false, runner_ok true
```

(The runner reads `.claude/agents/*.md` from the checkout, so it must be
recreated even though its code did not change: a stale mount would dispatch
retired agents.)

The first bars sweep takes a few minutes: about 430 symbols at 0.5 s spacing.

```bash
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env run --once bars_refresh
docker compose -f docker/docker-compose.yml exec engine python3 -c "
import sqlite3; c=sqlite3.connect('/data/engine.db')
print(c.execute('select count(distinct symbol), count(*) from bars').fetchone())"
```

Optionally, after 20:00 ET, seed the first evening so the PM has pitches the
next morning:

```bash
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env run --once desk_evening --ignore-window
```

- [ ] **Step 5: Watch the first paper day, then write the handoff**

What should appear in Discord, in order:

1. 16:10 — `bars_refresh` runs. It posts nothing unless something is stuck.
2. ~18:10 — `🗂️ DESK evening … technical N · earnings N · …`.
3. 08:20 — `🗂️ DESK pre-open …`.
4. 09:50–10:05:
   - `✅ PM … (date)`.
   - One `🧾 PAPER PROPOSAL` per funded call, each with its execute time.
5. Within 5 minutes of each deadline — `📄 PAPER BUY …`.
6. 10:00 onward — `📄 PAPER SELL …` as exits trigger.
7. Saturday 08:30 — `📊 DESK SCORECARD`.

Then add a new top section to `HANDOFF.md`:

- What was deployed (the sha).
- That the desk is **on paper**.
- That the 3-month clock started with the first scored PM call (give its date).
- The three conditions for real orders (strategy.md §10).
- That the `CLAUDE.md` §9 amendment still needs Chris's own words.

Commit it on its own:

```bash
git add HANDOFF.md
git commit -m "handoff: the trading desk is live on paper"
```

---

## Self-review (done while writing; kept for the executor)

**Spec coverage**, section by section:

| Spec section | Task(s) |
|---|---|
| §4 schedule | Tasks 4, 12, 15, 17 |
| §4.1 briefings | Task 7 |
| §4.2 ETF list | Task 2 |
| §5 pitches | Tasks 5, 8 |
| §6 PM | Task 13 |
| §7 scoring | Task 6 |
| §7.3 scorecard | Task 12 |
| §8 checkpoint | Tasks 12, 19 |
| §9.1–§9.3 instruments, sizing, options | Tasks 9, 10, 13 |
| §9.4 entries | Tasks 14, 15, 17 |
| §9.5 exits | Task 15 |
| §9.6 legacy | Tasks 13, 15 |
| §10 paper book | Tasks 11, 15 |
| §11 order path | out of scope by design; recorded in strategy.md §11 |
| §12.1–§12.2 rules | Tasks 2, 19 |
| §12.3 amendment | deliberately not done: it needs Chris's words |
| §13 components | across all tasks |
| §14 failure handling | Tasks 4, 6, 15, 17 |
| §15 testing | every task, plus Task 20 |
| §16 sequencing | Task 20 and strategy.md §10 |

Deviations are listed at the top of this plan.

**Known limits, stated rather than hidden:**
- **Holidays in `trading_days_between`:** `trading_day` falls back to
  weekday for dates other than today. A holiday the engine cannot see is
  healed in scoring (Task 6), not in the reference-session stamp.
- **Calls made in pre-market:** none exist; the PM runs at 09:50.
- **Option marks between runs:** these are the last `desk_watch` bid, so
  the scorecard's paper equity can lag a closing option price by up to one
  run.
