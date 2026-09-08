# v3 Plan 0c — Claude Runner, Engine MCP Server, and the Research Jobs

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Phase 0b engine the other half of its runtime — a `runner`
container that executes one Claude job at a time through `claude-agent-sdk`, an
MCP server inside the engine that is the *only* thing a job can touch, typed
research tools that carry every validation the bash writers carried, the weekly
universe sweep rewritten as engine code, and six scheduled Claude jobs whose
verdicts are structured objects rather than greppable stdout.

**Architecture:** The engine gains `tc/mcp/` (two `FastMCP` instances mounted at
`/mcp/research/` and `/mcp/decide/` behind a bearer-per-role middleware),
`tc/research/` (the storage and validation the tools call), `tc/jobs/` (typed job
specs and the runner client), and `tc/loops/universe.py` (the whole-market sweep
as Python). A new `runner` container holds `CLAUDE_CODE_OAUTH_TOKEN` and nothing
else: no Schwab credential, no database, no Bash. Claude reaches the world only
through `mcp__engine__*`, `WebSearch`, `WebFetch` and `Read` of the read-only
repo mount. Nothing here places, cancels or replaces an order — `propose_*` and
the order path are Plan 1.

**Tech Stack:** Python 3.12, `mcp==1.30.0`, `claude-agent-sdk==0.2.152` (runner
only), Claude Code CLI 2.1.234 (the `docker/Dockerfile` pin), `starlette` +
`uvicorn`, `pydantic` 2, `httpx`, `aiosqlite`, `pytest` + `pytest-asyncio`,
`mypy --strict`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` —
§2 (principles), §3 (topology: two containers, the runner's size and job),
§4 (Claude invocation: typed allowlists, read-only enforced twice, structured
verdicts, prompt reuse, the job list, `decide` is trigger-driven), §6 (state,
and the research-artifact split), §7 (pings and expectations), §9 (config,
secrets, layout), §10 (the contract test).

Two research documents are ground truth for the details and are quoted inside
the tasks:

- `.superpowers/research/0c-sdk-facts.md` — SDK/MCP behaviour verified by
  execution on 2026-09-07. Where this plan states an SDK fact, that file is why.
- `.superpowers/research/0c-writers-contract.md` — every validation rule,
  format, exit code and prompt dependency of the v2 bash writers. **The port
  must not consult the bash; it must satisfy that document.**

## Global Constraints

- Python **3.12**. Engine gate after every task that touches `engine/` or
  `tests/engine/`: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`.
  Runner gate after every task that touches `runner/` or `tests/runner/`:
  `cd runner && pytest -q && mypy && ruff check . ../tests/runner`. A path
  argument to pytest drops `asyncio_mode`; focused runs use
  `pytest -q -c pyproject.toml ../tests/engine/unit/test_x.py`.
- Money is `decimal.Decimal`; caps floor to cents; never `float` for money.
- Every model: `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")`.
- **No rule number is hard-coded** anywhere under `engine/` or `runner/`: every
  threshold comes through `tc.rules.model.Rules`. The one calendar constant that
  has no rules key is `QUARTER_DAYS = 91` in `tc/research/cohort.py`, carried
  over from `cohort.sh:53-55` **with its comment**, because `rules.yml` has no
  key for it.
- **No write-shaped broker call exists in this plan.** `Broker` stays read-only.
  `grep -rn "place_order\|replace_order\|cancel_order\|preview_order" engine/ runner/`
  must stay empty.
- **No tool reachable by any MCP role may match `place|cancel|replace|order`**
  (spec §10). Task 7 makes that a contract test *and* a consistency check.
- No `subprocess`, no shelling out, anywhere under `engine/` or `runner/`. The
  universe fetch is `httpx`, not `curl`; the universe filter is Python, not awk.
- Never commit an account number, account hash, order id, token or secret
  (`CLAUDE.md §7.4`). Test fixtures use `HASH_REDACTED` and synthetic order ids
  in the 1000000000001 range. Runner tests use the literal token
  `test-token-not-real` and never a `sk-ant-` shaped string.
- Work on branch `feat/v3-engine-0c`. **The worktree already exists at
  `/Users/chris/Documents/Projects/trade-challenge-0c`** (branch checked out, venv
  under `engine/.venv`). Work there; stage explicit paths; never `git add -A`.
- Commit trailer on every commit:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The engine stays in **shadow** (`engine.shadow: true`) for the whole of this
  plan: Discord goes to the shadow webhook, and no order tool exists to enable.
- `mcp==1.30.0` is pinned, not floated. mcp 2.x renamed `FastMCP` to `MCPServer`
  and deleted `mcp.server.fastmcp` outright (`0c-sdk-facts.md` §3.1).
  `claude-agent-sdk==0.2.152` and CLI **2.1.234** are pinned as one unit, and
  `cli_path` is always passed explicitly — the SDK bundles its own 2.1.259 CLI
  and prefers it over `PATH`, which would silently defeat the Dockerfile pin
  (`0c-sdk-facts.md` §1.9).

## File Structure

```
engine/tc/
  config.py                 MODIFY: Secrets +3 tokens +runner url; EngineConfig.research_dir; McpConfig
  broker/models.py          MODIFY: VerboseQuote, OptionContract, OptionChainView, Instrument, Mover
  broker/client.py          MODIFY: Broker protocol + SchwabBroker: quotes_verbose, option_chain,
                                    expiration_chain, instruments, movers
  broker/fake.py            MODIFY: same four reads from fixtures; Recorder records them
  store/schema.sql          MODIFY: evidence, escalations, sectors, universe, tombstones,
                                    screen_rows, iv_series, oi_snapshots, events, artifacts
  store/db.py               MODIFY: research accessors, latest_account, latest_tick
  research/__init__.py      CREATE
  research/docs.py          CREATE: DocStore — per-kind validators, CAS, .prev, artifacts index
  research/ledgers.py       CREATE: evidence/escalation/sector/ledger writers + readers
  research/cohort.py        CREATE: cohort(date) as a pure function over universe+sectors
  mcp/__init__.py           CREATE
  mcp/registry.py           CREATE: ROLE_TOOLS table — the single statement of who may call what
  mcp/server.py             CREATE: two FastMCP instances, bearer middleware, mounts, lifespan
  mcp/tools_read.py         CREATE: market/book/rules read tools
  mcp/tools_research.py     CREATE: research writers + research readers
  jobs/__init__.py          CREATE
  jobs/spec.py              CREATE: verdict models, JobSpec, JOB_SPECS
  jobs/dispatch.py          CREATE: RunnerClient, verdict classification, relays
  loops/universe.py         CREATE: Nasdaq fetch + the ten columns and five gates + universe.md
  rules/consistency.py      MODIFY: check_schedule_docs, check_tool_registry
  http/app.py               MODIFY: build_app mounts /mcp/*, lifespan enters both session managers
  main.py                   MODIFY: JOBS += weekly_universe + the six Claude jobs; _job_claude
  cli.py                    MODIFY: import-research
engine/pyproject.toml       MODIFY: mcp==1.30.0
config.yml                  MODIFY: engine.research_dir, mcp:, runner:, seven schedule entries
runner/pyproject.toml       CREATE
runner/tc_runner/__init__.py CREATE
runner/tc_runner/app.py     CREATE: POST /run, GET /health, the PreToolUse gate, the one-run lock
docker/Dockerfile.runner    CREATE
docker/docker-compose.yml   MODIFY: runner service (profile engine)
.claude/commands/           MODIFY: research.md deep-research.md scout.md catalyst.md sector-tag.md
                            RETIRE: weekly-universe.md tick.md
.claude/agents/             MODIFY: research-scout.md deep-research.md scout.md catalyst.md sector-tagger.md
                            RETIRE: weekly-universe.md tick-watch.md session-close.md trader.md
tests/engine/unit/          test_broker_research_reads.py test_research_docs.py test_research_ledgers.py
                            test_cohort.py test_mcp_server.py test_tools_read.py test_tools_research.py
                            test_jobs_spec.py test_jobs_dispatch.py test_universe.py test_import_research.py
tests/engine/contract/      test_mcp_no_order_tools.py
tests/engine/fixtures/broker/  chain-CSX.json expirations-CSX.json instruments-CSX.json
                               movers-EQUITY_ALL.json quotes-verbose.json
tests/engine/fixtures/nasdaq/  nasdaqtraded-sample.txt nasdaqtraded-decoy.html
tests/runner/               test_app.py test_gate.py
```

**Why `tc/research/` exists beside `tc/mcp/`.** Spec §9's tree names
`mcp/tools_research.py` as the writers' home. Everything in it would then be a
FastMCP-decorated function, which cannot be unit-tested without standing up a
server, and the validation is the part most worth testing. So the validation and
storage live in `tc/research/` as plain async functions over `Store`, and
`tools_research.py` is a thin registration layer over them. The spec's file still
exists and still owns the tool surface; only the bodies moved one file down.

---

### Task 1: Config, secrets and the `mcp` pin

**Files:**
- Modify: `engine/tc/config.py`, `engine/pyproject.toml`, `config.yml`
- Test: `tests/engine/unit/test_config.py`

**Interfaces:**
- Produces:
  - `Secrets` gains `runner_token: str | None`, `mcp_research_token: str | None`,
    `mcp_decide_token: str | None` (env `TC_RUNNER_TOKEN`,
    `TC_MCP_RESEARCH_TOKEN`, `TC_MCP_DECIDE_TOKEN`).
  - `EngineConfig.research_dir: Path` — the documents root, `/data/research`.
  - `RunnerConfig(url: AnyHttpUrl, connect_timeout_s: float = 10.0, slack_s: float = 120.0)`
    on `FileConfig` as `runner`, exposed on `Settings` as `runner`.
  - `Settings.runner_token`, `Settings.mcp_research_token`,
    `Settings.mcp_decide_token` read-only properties (same shape as the existing
    `schwab_app_key` passthroughs).
  - `Settings.mcp_tokens() -> dict[str, str]` — `{token: role}` for the two roles
    that have a token configured, empty when neither is set.

Why `slack_s`: the SDK's cancellation takes real wall time to reap the child
process — a 6 s deadline measured 11.5 s end to end (`0c-sdk-facts.md` §1.8).
The engine's HTTP read timeout is `job.timeout_s + runner.slack_s`, so the
runner's own deadline always fires first and the engine gets a structured
timeout rather than a dead socket.

- [ ] **Step 1: Write the failing config test**

Append to `tests/engine/unit/test_config.py`:

```python
def test_runner_and_mcp_sections(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
        "schedule: {scout: 'at 07:12 weekdays'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    monkeypatch.setenv("TC_RUNNER_TOKEN", "runner-secret")
    monkeypatch.setenv("TC_MCP_RESEARCH_TOKEN", "research-secret")
    monkeypatch.setenv("TC_MCP_DECIDE_TOKEN", "decide-secret")
    from tc.config import load_settings

    s = load_settings(cfg)
    assert s.engine.research_dir == Path("/d/research")
    assert str(s.runner.url).rstrip("/") == "http://127.0.0.1:8090"
    assert s.runner.slack_s == 120.0
    assert s.runner_token == "runner-secret"
    assert s.mcp_tokens() == {"research-secret": "research", "decide-secret": "decide"}


def test_mcp_tokens_empty_when_unset(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        "engine: {data_dir: /d, repo_dir: /r, research_dir: /d/research}\n"
        "token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://x.ts.net/oauth/callback}\n"
        "runner: {url: 'http://127.0.0.1:8090'}\n"
    )
    monkeypatch.setenv("TC_SCHWAB_APP_KEY", "k")
    monkeypatch.setenv("TC_SCHWAB_APP_SECRET", "s")
    for k in ("TC_RUNNER_TOKEN", "TC_MCP_RESEARCH_TOKEN", "TC_MCP_DECIDE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from tc.config import load_settings

    s = load_settings(cfg)
    assert s.mcp_tokens() == {}
    assert s.runner_token is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py -k "runner or mcp_tokens"`
Expected: FAIL — `extra_forbidden` on `research_dir` / `runner`.

- [ ] **Step 3: Extend `config.py`**

In `engine/tc/config.py`, add `research_dir` to `EngineConfig`:

```python
    # Documents Claude reads whole (spec §6): candidates, standing, scorecard,
    # the pre-open brief, the roster, universe.md. Under /data, outside any
    # checkout, which is what kills the Glob-on-a-gitignored-path failure.
    research_dir: Path
```

Add the runner model and wire it through:

```python
class RunnerConfig(BaseModel):
    """Where the Claude runner lives and how long the engine waits on it.

    `slack_s` is added to a job's own timeout to form the engine's HTTP read
    timeout, so the runner's deadline always fires first: the SDK reaps its
    child process *after* the cancel and a 6s deadline measured 11.5s wall
    clock, so a tighter engine timeout would turn every long job into a dead
    socket instead of a structured `timeout` verdict.
    """

    model_config = ConfigDict(extra="forbid")
    url: AnyHttpUrl
    connect_timeout_s: float = 10.0
    slack_s: float = 120.0
```

On `FileConfig`: `runner: RunnerConfig`. On `Secrets`:

```python
    # The runner's inbound bearer, and one bearer per MCP role. All optional:
    # an engine with no runner still ticks, closes the session and serves
    # /health -- it simply dispatches no Claude job (jobs/dispatch.py).
    runner_token: str | None = None
    mcp_research_token: str | None = None
    mcp_decide_token: str | None = None
```

On `Settings`: field `runner: RunnerConfig`, the three passthrough properties,
and:

```python
    def mcp_tokens(self) -> dict[str, str]:
        """Bearer -> role. A role with no token configured is simply not
        reachable: the middleware answers 403 rather than defaulting to a role,
        because a default role is a way to reach tools without a credential."""
        pairs = (
            (self.mcp_research_token, "research"),
            (self.mcp_decide_token, "decide"),
        )
        return {t: role for t, role in pairs if t}
```

`load_settings` passes `runner=file_cfg.runner`.

Update `config.yml`:

```yaml
engine:
  # ... existing keys unchanged ...
  research_dir: /data/research   # documents Claude reads whole (spec §6); ledgers are tables
runner:
  url: http://127.0.0.1:8090     # the runner container, host networking, loopback only
  connect_timeout_s: 10.0
  slack_s: 120.0                 # added to a job's timeout for the engine's HTTP read budget
```

Add `"mcp==1.30.0"` to `engine/pyproject.toml` dependencies, with the comment:

```
  # 1.30.0, not 2.x: mcp 2 renamed FastMCP to MCPServer and removed
  # mcp.server.fastmcp entirely. Pinned exactly, not floated.
  "mcp==1.30.0",
```

Fix every existing test/fixture that builds `EngineConfig` or a `config.yml`
without `research_dir`/`runner` (grep `repo_dir` under `tests/engine`).

- [ ] **Step 4: Install and run the config tests**

Run: `cd engine && .venv/bin/pip install -e '.[dev]' && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/config.py engine/pyproject.toml config.yml tests/engine/unit/test_config.py
git commit -m "engine: the runner and the MCP roles get their own config, and a role with no token is unreachable"
```

---

### Task 2: The four broker reads research needs, plus verbose quotes

**Files:**
- Modify: `engine/tc/broker/models.py`, `engine/tc/broker/client.py`, `engine/tc/broker/fake.py`
- Test: `tests/engine/unit/test_broker_research_reads.py`
- Fixtures: `tests/engine/fixtures/broker/{chain-CSX,expirations-CSX,instruments-CSX,movers-EQUITY_ALL,quotes-verbose}.json`

**Interfaces:**
- Consumes: `Broker` (Phase 0a/0b), `FakeBroker`, `Recorder`.
- Produces, on the `Broker` protocol and on both implementations:
  - `async quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]`
  - `async option_chain(self, symbol: str, from_date: date, to_date: date, strike_count: int, contract_type: Literal["CALL","PUT","ALL"]) -> OptionChainView`
  - `async expiration_chain(self, symbol: str) -> list[Expiration]`
  - `async instruments(self, query: str, projection: str) -> list[Instrument]`
  - `async movers(self, index: str, direction: Literal["up","down"]) -> list[Mover]`
- New models in `broker/models.py`, all `extra="forbid"`:
  - `VerboseQuote(symbol, price: Decimal | None, avg10_days_volume: Decimal, fund_leverage_factor: Decimal, high: Decimal | None, low: Decimal | None, week52_high: Decimal, net_percent_change: Decimal, optionable: bool, description: str, last_earnings: str, is_etf: bool)` with `from_payload(symbol, body)`.
  - `OptionContract(osi, expiry: date, strike: Decimal, kind: Literal["CALL","PUT"], bid, ask, last, delta: Decimal | None, open_interest: int, volume: int, implied_volatility: Decimal | None, days_to_expiration: int)`.
  - `OptionChainView(symbol, underlying_price: Decimal, contracts: list[OptionContract])` with `from_payload`.
  - `Expiration(expiry: date, days_to_expiration: int, standard: bool)`.
  - `Instrument(symbol, description, asset_type, exchange, cusip: str | None)`.
  - `Mover(symbol, description, last: Decimal, net_change: Decimal, net_percent_change: Decimal, volume: int)`.

`VerboseQuote.from_payload` implements the sub-block anchoring the v2 filter had
to reverse-engineer out of text (`0c-writers-contract.md` §1.13): the JSON payload
already has named sub-objects, so `price` is `regular.regularMarketLastPrice`,
falling back to `quote.lastPrice`, and **never** `extended.lastPrice`. `price` is
`None` when neither is present — the "unquotable, skipped, never silently" case.
`fund_leverage_factor` stays a **percentage** (0 for a single stock, 100.0 for a
1x fund, 200/300 leveraged, negative inverse); Task 14 divides by 100 only for
display, exactly as `universe-filter.sh` did.

Schwab call shapes (verified against `schwab-py` 1.5.1
`schwab/client/base.py`): `get_quotes(symbols, fields=[Quote.Fields.QUOTE, FUNDAMENTAL, REFERENCE, REGULAR])`;
`get_option_chain(symbol, contract_type=, strike_count=, from_date=, to_date=)`;
`get_option_expiration_chain(symbol)`; `get_instruments(symbols, projection)`;
`get_movers(index, sort_order=)` where `direction="up"` maps to
`Movers.SortOrder.PERCENT_CHANGE_UP` and `"down"` to `PERCENT_CHANGE_DOWN`.

- [ ] **Step 1: Write the fixtures**

`tests/engine/fixtures/broker/chain-CSX.json` — the Schwab shape, trimmed to two
contracts and synthetic numbers:

```json
{
 "symbol": "CSX",
 "underlyingPrice": 48.99,
 "callExpDateMap": {
  "2026-10-16:39": {
   "47.5": [{"symbol": "CSX   261016C00047500", "putCall": "CALL", "strikePrice": 47.5,
             "bid": 2.05, "ask": 2.20, "last": 2.12, "delta": 0.61, "openInterest": 1500,
             "totalVolume": 210, "volatility": 20.8, "daysToExpiration": 39,
             "expirationDate": "2026-10-16T20:00:00.000+00:00"}],
   "50.0": [{"symbol": "CSX   261016C00050000", "putCall": "CALL", "strikePrice": 50.0,
             "bid": 0.95, "ask": 1.10, "last": 1.00, "delta": 0.41, "openInterest": 420,
             "totalVolume": 95, "volatility": 21.4, "daysToExpiration": 39,
             "expirationDate": "2026-10-16T20:00:00.000+00:00"}]
  }
 },
 "putExpDateMap": {}
}
```

`expirations-CSX.json`:

```json
{"expirationList": [
  {"expirationDate": "2026-10-16", "daysToExpiration": 39, "expirationType": "S", "standard": true},
  {"expirationDate": "2026-11-20", "daysToExpiration": 74, "expirationType": "S", "standard": true}]}
```

`instruments-CSX.json`:

```json
{"instruments": [{"cusip": "126408103", "symbol": "CSX",
  "description": "CSX Corporation Common Stock", "exchange": "NASDAQ", "assetType": "EQUITY"}]}
```

`movers-EQUITY_ALL.json`:

```json
{"screeners": [
  {"symbol": "AAAA", "description": "Synthetic Mover A", "lastPrice": 12.34,
   "netChange": 1.20, "netPercentChange": 10.8, "volume": 4500000},
  {"symbol": "BBBB", "description": "Synthetic Mover B", "lastPrice": 55.10,
   "netChange": 4.00, "netPercentChange": 7.8, "volume": 1200000}]}
```

`quotes-verbose.json` — one qualifying stock, one 3x fund, one no-high/low ETF,
one unquotable:

```json
{
 "MPC": {"assetSubType": "", "quote": {"lastPrice": 368.83, "highPrice": 374.10, "lowPrice": 368.10,
          "52WeekHigh": 369.12, "netPercentChange": 0.42},
         "fundamental": {"avg10DaysVolume": 2349452, "fundLeverageFactor": 0.0,
          "lastEarningsDate": "2026-08-04T00:00:00.000Z"},
         "reference": {"optionable": true, "description": "Marathon Petroleum Corp"},
         "regular": {"regularMarketLastPrice": 368.83},
         "extended": {"lastPrice": 999.99}},
 "TQQQ": {"assetSubType": "ETF", "quote": {"lastPrice": 90.00, "highPrice": 91.0, "lowPrice": 88.0,
          "52WeekHigh": 100.0, "netPercentChange": 1.1},
          "fundamental": {"avg10DaysVolume": 30000000, "fundLeverageFactor": 300.0,
           "lastEarningsDate": ""},
          "reference": {"optionable": true, "description": "ProShares UltraPro QQQ"},
          "regular": {"regularMarketLastPrice": 90.00}},
 "SPY": {"assetSubType": "ETF", "quote": {"lastPrice": 640.00, "highPrice": 0, "lowPrice": 0,
          "52WeekHigh": 645.0, "netPercentChange": 0.2},
         "fundamental": {"avg10DaysVolume": 34400000, "fundLeverageFactor": 100.0,
          "lastEarningsDate": ""},
         "reference": {"optionable": true, "description": "SPDR S&P 500 ETF Trust"},
         "regular": {"regularMarketLastPrice": 640.00}},
 "NOPRICE": {"assetSubType": "", "quote": {}, "fundamental": {}, "reference": {}, "regular": {}}
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/engine/unit/test_broker_research_reads.py`:

```python
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from tc.broker.fake import FakeBroker

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker(FIX, NOW)


async def test_verbose_quote_prefers_regular_over_extended(broker):
    q = (await broker.quotes_verbose(["MPC"]))["MPC"]
    assert q.price == D("368.83")           # NOT 999.99 from the extended block
    assert q.avg10_days_volume == D("2349452")
    assert q.fund_leverage_factor == D("0")  # a percentage, not a multiple
    assert q.week52_high == D("369.12")
    assert q.optionable is True
    assert q.last_earnings == "2026-08-04"   # truncated to 10 chars
    assert q.is_etf is False


async def test_verbose_quote_marks_unquotable_rather_than_zero(broker):
    q = (await broker.quotes_verbose(["NOPRICE"]))["NOPRICE"]
    assert q.price is None
    assert q.last_earnings == ""


async def test_verbose_quote_zero_high_low_is_no_data_not_zero_range(broker):
    q = (await broker.quotes_verbose(["SPY"]))["SPY"]
    assert q.high == D("0") and q.low == D("0")
    assert q.fund_leverage_factor == D("100")   # a 1x fund, not "leveraged"
    assert q.is_etf is True


async def test_option_chain_flattens_both_maps(broker):
    view = await broker.option_chain(
        "CSX", date(2026, 10, 1), date(2026, 11, 1), 6, "CALL"
    )
    assert view.underlying_price == D("48.99")
    osis = [c.osi for c in view.contracts]
    assert osis == ["CSX   261016C00047500", "CSX   261016C00050000"]
    first = view.contracts[0]
    assert first.strike == D("47.5") and first.kind == "CALL"
    assert first.delta == D("0.61") and first.open_interest == 1500
    assert first.days_to_expiration == 39 and first.expiry == date(2026, 10, 16)


async def test_expiration_chain(broker):
    exps = await broker.expiration_chain("CSX")
    assert [e.expiry for e in exps] == [date(2026, 10, 16), date(2026, 11, 20)]
    assert exps[0].standard is True


async def test_instruments(broker):
    rows = await broker.instruments("CSX", "symbol-search")
    assert rows[0].symbol == "CSX" and rows[0].exchange == "NASDAQ"
    assert rows[0].asset_type == "EQUITY"


async def test_movers(broker):
    rows = await broker.movers("EQUITY_ALL", "up")
    assert [r.symbol for r in rows] == ["AAAA", "BBBB"]
    assert rows[0].net_percent_change == D("10.8") and rows[0].volume == 4500000
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_broker_research_reads.py`
Expected: FAIL — `AttributeError: 'FakeBroker' object has no attribute 'quotes_verbose'`.

- [ ] **Step 4: Add the models**

In `engine/tc/broker/models.py`:

```python
def _dec(v: Any, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


class VerboseQuote(BaseModel):
    """The universe sweep's row, straight off the JSON payload.

    v2 read this out of two-space-indented verbose *text* and had to split it
    into named sub-blocks by regex, because `lastPrice` exists in BOTH the
    `extended` and `quote` blocks and `extended` came first -- so a whole-body
    match returned the after-hours print (0c-writers-contract.md §1.13). Here
    the sub-objects are already named, and the precedence is stated once:
    regular, then quote, never extended.
    """

    model_config = ConfigDict(extra="forbid")
    symbol: str
    price: Decimal | None            # None = unquotable; recorded, never treated as 0
    avg10_days_volume: Decimal
    fund_leverage_factor: Decimal    # PERCENT: 0 stock, 100 = 1x fund, 200/300 leveraged
    high: Decimal | None
    low: Decimal | None
    week52_high: Decimal
    net_percent_change: Decimal
    optionable: bool
    description: str
    last_earnings: str               # "" when absent; column 8 of the universe table
    is_etf: bool

    @classmethod
    def from_payload(cls, symbol: str, body: dict[str, Any]) -> VerboseQuote:
        q = body.get("quote") or {}
        f = body.get("fundamental") or {}
        r = body.get("reference") or {}
        reg = body.get("regular") or {}
        raw_price = reg.get("regularMarketLastPrice", q.get("lastPrice"))
        return cls(
            symbol=symbol,
            price=None if raw_price in (None, "") else _dec(raw_price),
            avg10_days_volume=_dec(f.get("avg10DaysVolume")),
            fund_leverage_factor=_dec(f.get("fundLeverageFactor")),
            high=None if q.get("highPrice") is None else _dec(q["highPrice"]),
            low=None if q.get("lowPrice") is None else _dec(q["lowPrice"]),
            week52_high=_dec(q.get("52WeekHigh")),
            net_percent_change=_dec(q.get("netPercentChange")),
            optionable=bool(r.get("optionable", False)),
            description=str(r.get("description", "")).strip(),
            last_earnings=str(f.get("lastEarningsDate") or "")[:10],
            is_etf=body.get("assetSubType") == "ETF",
        )


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    osi: str
    expiry: date
    strike: Decimal
    kind: Literal["CALL", "PUT"]
    bid: Decimal
    ask: Decimal
    last: Decimal
    delta: Decimal | None
    open_interest: int
    volume: int
    implied_volatility: Decimal | None
    days_to_expiration: int

    @classmethod
    def from_payload(cls, c: dict[str, Any]) -> OptionContract:
        # Schwab reports a sentinel -999.0 delta on contracts it cannot price.
        # None is the honest record of "unknown"; -999 would clear a delta
        # FLOOR test by being a number and fail a BAND test by being the wrong
        # one, and CLAUDE.md §3.2 is a band.
        raw_delta = c.get("delta")
        delta = None if raw_delta in (None, "NaN", -999.0, "-999.0") else _dec(raw_delta)
        vol = c.get("volatility")
        iv = None if vol in (None, "NaN", -999.0) else _dec(vol)
        return cls(
            osi=str(c["symbol"]),
            expiry=datetime.fromisoformat(str(c["expirationDate"]).replace("Z", "+00:00")).date(),
            strike=_dec(c["strikePrice"]),
            kind="CALL" if str(c["putCall"]).upper() == "CALL" else "PUT",
            bid=_dec(c.get("bid")), ask=_dec(c.get("ask")), last=_dec(c.get("last")),
            delta=delta,
            open_interest=int(c.get("openInterest", 0)),
            volume=int(c.get("totalVolume", 0)),
            implied_volatility=iv,
            days_to_expiration=int(c.get("daysToExpiration", 0)),
        )


class OptionChainView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    underlying_price: Decimal
    contracts: list[OptionContract]

    @classmethod
    def from_payload(cls, symbol: str, body: dict[str, Any]) -> OptionChainView:
        out: list[OptionContract] = []
        for key in ("callExpDateMap", "putExpDateMap"):
            for strikes in (body.get(key) or {}).values():
                for rows in strikes.values():
                    out.extend(OptionContract.from_payload(c) for c in rows)
        out.sort(key=lambda c: (c.expiry, c.kind, c.strike))
        return cls(symbol=symbol, underlying_price=_dec(body.get("underlyingPrice")), contracts=out)


class Expiration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiry: date
    days_to_expiration: int
    standard: bool


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    description: str
    asset_type: str
    exchange: str
    cusip: str | None


class Mover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    description: str
    last: Decimal
    net_change: Decimal
    net_percent_change: Decimal
    volume: int
```

Add `Literal` and `datetime` to the module's imports if absent.

- [ ] **Step 5: Extend the protocol and both implementations**

In `engine/tc/broker/client.py`, add to `class Broker(Protocol)`:

```python
    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]: ...
    async def option_chain(
        self, symbol: str, from_date: date, to_date: date,
        strike_count: int, contract_type: str,
    ) -> OptionChainView: ...
    async def expiration_chain(self, symbol: str) -> list[Expiration]: ...
    async def instruments(self, query: str, projection: str) -> list[Instrument]: ...
    async def movers(self, index: str, direction: str) -> list[Mover]: ...
```

and to `SchwabBroker`:

```python
    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
        """Every field the universe screen needs, in one call.

        v2 could not ask for these: the schwab-mcp wrapper ignored `fields=`,
        so the only way to get avg10DaysVolume / fundLeverageFactor / the
        `regular` block was `verbose=True`, whose ~260KB response had to be
        written to a file and parsed by a script the model was forbidden to
        read. Here it is an ordinary typed read the model never sees at all.
        """
        if not symbols:
            return {}
        c = self._c()
        fields = [
            c.Quote.Fields.QUOTE, c.Quote.Fields.FUNDAMENTAL,
            c.Quote.Fields.REFERENCE, c.Quote.Fields.REGULAR,
        ]
        data = await self._guard(await c.get_quotes(list(symbols), fields=fields))
        assert isinstance(data, dict)
        return {s: VerboseQuote.from_payload(s, b) for s, b in data.items()}

    async def option_chain(
        self, symbol: str, from_date: date, to_date: date,
        strike_count: int, contract_type: str,
    ) -> OptionChainView:
        c = self._c()
        data = await self._guard(
            await c.get_option_chain(
                symbol,
                contract_type=c.Options.ContractType[contract_type],
                strike_count=strike_count, from_date=from_date, to_date=to_date,
            )
        )
        assert isinstance(data, dict)
        return OptionChainView.from_payload(symbol, data)

    async def expiration_chain(self, symbol: str) -> list[Expiration]:
        data = await self._guard(await self._c().get_option_expiration_chain(symbol))
        assert isinstance(data, dict)
        return [
            Expiration(
                expiry=date.fromisoformat(str(e["expirationDate"])[:10]),
                days_to_expiration=int(e.get("daysToExpiration", 0)),
                standard=bool(e.get("standard", True)),
            )
            for e in data.get("expirationList", [])
        ]

    async def instruments(self, query: str, projection: str) -> list[Instrument]:
        c = self._c()
        data = await self._guard(
            await c.get_instruments(query, c.Instrument.Projection(projection))
        )
        assert isinstance(data, dict)
        return [
            Instrument(
                symbol=str(i.get("symbol", "")), description=str(i.get("description", "")),
                asset_type=str(i.get("assetType", "")), exchange=str(i.get("exchange", "")),
                cusip=i.get("cusip"),
            )
            for i in data.get("instruments", [])
        ]

    async def movers(self, index: str, direction: str) -> list[Mover]:
        c = self._c()
        order = (
            c.Movers.SortOrder.PERCENT_CHANGE_UP if direction == "up"
            else c.Movers.SortOrder.PERCENT_CHANGE_DOWN
        )
        data = await self._guard(
            await c.get_movers(c.Movers.Index(index), sort_order=order)
        )
        assert isinstance(data, dict)
        return [
            Mover(
                symbol=str(m.get("symbol", "")), description=str(m.get("description", "")),
                last=_dec(m.get("lastPrice")), net_change=_dec(m.get("netChange")),
                net_percent_change=_dec(m.get("netPercentChange")),
                volume=int(m.get("volume", 0)),
            )
            for m in data.get("screeners", [])
        ]
```

Import `_dec`, `Expiration`, `Instrument`, `Mover`, `OptionChainView`,
`VerboseQuote` from `tc.broker.models`.

In `engine/tc/broker/fake.py`, the same five methods over fixture files:
`quotes-verbose.json` (filtered to the requested symbols), `chain-<symbol>.json`,
`expirations-<symbol>.json`, `instruments-<query>.json`,
`movers-<index>.json`. `_check_shape` gains rows for each new fixture name
(`chain-` → dict with `callExpDateMap`; `expirations-` → dict with
`expirationList`; `instruments-` → dict with `instruments`; `movers-` → dict with
`screeners`; `quotes-verbose.json` → dict). `Recorder.record` gains a `symbols`
loop recording `chain-<s>.json`, `expirations-<s>.json`, `instruments-<s>.json`,
plus one `movers-EQUITY_ALL.json` and one `quotes-verbose.json`, all through
`_raise_for` and `redact` exactly as the existing entries are.

- [ ] **Step 6: Run the tests, then the gate**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_broker_research_reads.py`
Expected: PASS.
Run: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`
Expected: all green (the `_schwab_conforms` cast in `client.py` is what catches a
signature that drifted from the protocol).

- [ ] **Step 7: Commit**

```bash
git add engine/tc/broker/ tests/engine/unit/test_broker_research_reads.py tests/engine/fixtures/broker/
git commit -m "engine: the chain, the expirations, the movers and a verbose quote are typed reads, not a payload file"
```

---

### Task 3: The research tables and the store accessors

**Files:**
- Modify: `engine/tc/store/schema.sql`, `engine/tc/store/db.py`
- Test: `tests/engine/unit/test_store_research.py`

**Interfaces:**
- Produces on `Store` (all `async`):
  - `insert_evidence(row: dict[str, Any]) -> int`
  - `evidence_for(symbol: str) -> list[dict[str, Any]]` (oldest first)
  - `insert_escalation(row: dict[str, Any]) -> None` (kind `raise` or `score`)
  - `escalation_raise_exists(escalation_id: str) -> bool`
  - `escalations(symbol: str | None = None) -> list[dict[str, Any]]` — one entry
    per raise, with `latest_outcome: str | None` reduced from the score rows
  - `upsert_sectors(rows: list[tuple[str, str, str]]) -> tuple[int, int]` →
    `(new, retired)`; retired = a symbol whose stored tag was in scope and whose
    new tag is `other`
  - `sectors() -> list[dict[str, Any]]`
  - `replace_universe(asof: date, rows: list[dict[str, Any]]) -> int`
  - `universe_rows(qualified_only: bool = True) -> list[dict[str, Any]]` — the
    newest `asof` only
  - `universe_asof() -> date | None`
  - `append_ledger(name: str, d: date, symbol: str | None, record: dict[str, Any]) -> None`
  - `ledger_rows(name: str, d: date | None = None, latest_before: date | None = None) -> list[dict[str, Any]]`
  - `oi_symbol_seen(d: date, symbol: str) -> bool`
  - `record_artifact(kind: str, d: date | None, path: str, sha256: str, lines: int) -> None`
  - `latest_account() -> AccountSnapshot | None`
  - `latest_tick() -> TickRow | None`

`ledger_rows` reads from the table the name selects: `screen` →`screen_rows`,
`iv` → `iv_series`, `oi` → `oi_snapshots`, `tombstones` → `tombstones`,
`events` → `events`. One dispatch table, `LEDGER_TABLES`, exported so
`tc/research/ledgers.py` and the importer agree on it.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/unit/test_store_research.py`:

```python
from datetime import date
from decimal import Decimal as D

import pytest

from tc.store.db import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_evidence_round_trips_in_order(store):
    for i, claim in enumerate(["first", "second"]):
        await store.insert_evidence(
            {"symbol": "CSX", "date": "2026-09-07", "claim": claim, "url": f"https://x/{i}",
             "source_type": "end-user", "observed": "2026-09-07", "independence": "unrelated",
             "extra": {}}
        )
    rows = await store.evidence_for("CSX")
    assert [r["claim"] for r in rows] == ["first", "second"]
    assert await store.evidence_for("NOPE") == []


async def test_escalation_last_score_wins(store):
    await store.insert_escalation(
        {"id": "CSX-2026-09-07-123", "kind": "raise", "symbol": "CSX", "at": "2026-09-07",
         "record": {"claim": "c", "direction": "up", "event_date": "2026-10-10",
                    "source_types": ["end-user", "employee"]}}
    )
    assert await store.escalation_raise_exists("CSX-2026-09-07-123") is True
    for outcome in ("wrong", "right"):
        await store.insert_escalation(
            {"id": "CSX-2026-09-07-123", "kind": "score", "symbol": "CSX",
             "at": "2026-09-08", "record": {"outcome": outcome}}
        )
    rows = await store.escalations()
    assert len(rows) == 1                      # one prediction, one entry
    assert rows[0]["latest_outcome"] == "right"


async def test_sectors_upsert_counts_new_and_retired(store):
    new, retired = await store.upsert_sectors([("CSX", "airlines-transport", "2026-09-07")])
    assert (new, retired) == (1, 0)
    new, retired = await store.upsert_sectors([("CSX", "other", "2026-09-08")])
    assert (new, retired) == (0, 1)
    assert [(r["symbol"], r["sector"]) for r in await store.sectors()] == [("CSX", "other")]


async def test_universe_replace_keeps_only_the_newest_sweep(store):
    row = {"symbol": "MPC", "price": D("368.83"), "adv10": D("2349452"),
           "dollar_vol": D("866548381"), "pct_from_52wk_high": D("0.08"), "optionable": True,
           "leverage": D("0.0"), "last_earnings": "2026-08-04", "is_etf": False,
           "session_range_pct": D("1.62"), "description": "Marathon Petroleum Corp",
           "qualified": True}
    await store.replace_universe(date(2026, 8, 29), [row])
    await store.replace_universe(date(2026, 9, 5), [{**row, "symbol": "CSX"}])
    assert [r["symbol"] for r in await store.universe_rows()] == ["CSX"]
    assert await store.universe_asof() == date(2026, 9, 5)


async def test_oi_idempotency_is_per_symbol_per_day(store):
    await store.append_ledger("oi", date(2026, 9, 7), "CSX", {"symbol": "CSX", "t": "16:26:00"})
    assert await store.oi_symbol_seen(date(2026, 9, 7), "CSX") is True
    assert await store.oi_symbol_seen(date(2026, 9, 8), "CSX") is False


async def test_ledger_latest_before_finds_the_prior_file(store):
    for d, iv in ((date(2026, 9, 3), "0.248"), (date(2026, 9, 7), "0.208")):
        await store.append_ledger("iv", d, "CSX", {"symbol": "CSX", "t": "16:26:00", "atm_iv": iv})
    prior = await store.ledger_rows("iv", latest_before=date(2026, 9, 7))
    assert [r["record"]["atm_iv"] for r in prior] == ["0.248"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_store_research.py`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'insert_evidence'`.

- [ ] **Step 3: Extend `schema.sql`**

Append to `engine/tc/store/schema.sql`:

```sql
-- Research ledgers (spec §6). Everything the bash writers validated as JSON or
-- TSV becomes a row here; the documents Claude reads whole stay as files and
-- are indexed in `artifacts`.
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, date TEXT NOT NULL,
  claim TEXT NOT NULL, url TEXT NOT NULL, source_type TEXT NOT NULL,
  observed TEXT NOT NULL, independence TEXT NOT NULL, extra_json TEXT NOT NULL,
  written_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_symbol ON evidence(symbol, id);

-- One table for raises AND scores, because scoring is append-only and an id may
-- carry many score rows: the LAST score per id wins, and a reader that counts
-- rows instead of reducing to the latest gets the hit rate wrong
-- (0c-writers-contract.md §1.5, reader contract).
CREATE TABLE IF NOT EXISTS escalations (
  row_id INTEGER PRIMARY KEY, id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('raise','score')),
  symbol TEXT NOT NULL, at TEXT NOT NULL, record_json TEXT NOT NULL,
  written_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS escalations_id ON escalations(id, row_id);

CREATE TABLE IF NOT EXISTS sectors (
  symbol TEXT PRIMARY KEY, sector TEXT NOT NULL, date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe (
  asof TEXT NOT NULL, symbol TEXT NOT NULL,
  price TEXT NOT NULL, adv10 TEXT NOT NULL, dollar_vol TEXT NOT NULL,
  pct_from_52wk_high TEXT NOT NULL, optionable INTEGER NOT NULL, leverage TEXT NOT NULL,
  last_earnings TEXT NOT NULL, is_etf INTEGER NOT NULL, session_range_pct TEXT,
  description TEXT NOT NULL, qualified INTEGER NOT NULL,
  PRIMARY KEY (asof, symbol)
);

CREATE TABLE IF NOT EXISTS tombstones (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_rows (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iv_series (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
-- UNIQUE(date,symbol) is the §1.7 idempotency guard: one snapshot per
-- underlying per day, and a second attempt is a SKIP, never an error.
CREATE TABLE IF NOT EXISTS oi_snapshots (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL,
  UNIQUE (date, symbol)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, date TEXT,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, lines INTEGER NOT NULL,
  written_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_update BEFORE UPDATE ON escalations BEGIN SELECT RAISE(ABORT, 'escalations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_delete BEFORE DELETE ON escalations BEGIN SELECT RAISE(ABORT, 'escalations is append-only'); END;
```

Note deliberately **not** append-only: `sectors` (a tag is corrected in place —
`sector-write.sh` merge semantics) and `universe` (a weekly sweep replaces the
week; the previous `asof` rows are kept as history and `replace_universe` only
deletes its own `asof`).

- [ ] **Step 4: Implement the accessors**

In `engine/tc/store/db.py`:

```python
LEDGER_TABLES: dict[str, str] = {
    "screen": "screen_rows", "iv": "iv_series", "oi": "oi_snapshots",
    "tombstones": "tombstones", "events": "events",
}
IN_SCOPE_SECTORS = ("consumer-software", "airlines-transport", "semis-hardware")
```

```python
    async def append_ledger(
        self, name: str, d: date, symbol: str | None, record: dict[str, Any]
    ) -> None:
        table = LEDGER_TABLES[name]
        await self.execute(
            f"INSERT INTO {table} (date, symbol, record_json, written_at) VALUES (?,?,?,?)",
            (d.isoformat(), symbol or "", json.dumps(record, sort_keys=True), _now()),
        )

    async def ledger_rows(
        self, name: str, d: date | None = None, latest_before: date | None = None
    ) -> list[dict[str, Any]]:
        table = LEDGER_TABLES[name]
        if latest_before is not None:
            row = await self.fetchone(
                f"SELECT date FROM {table} WHERE date < ? ORDER BY date DESC LIMIT 1",
                (latest_before.isoformat(),),
            )
            if row is None:
                return []
            d = date.fromisoformat(row["date"])
        sql = f"SELECT date, symbol, record_json FROM {table}"
        params: tuple[Any, ...] = ()
        if d is not None:
            sql += " WHERE date = ?"
            params = (d.isoformat(),)
        rows = await self.fetchall(sql + " ORDER BY id", params)
        return [
            {"date": r["date"], "symbol": r["symbol"], "record": json.loads(r["record_json"])}
            for r in rows
        ]

    async def oi_symbol_seen(self, d: date, symbol: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM oi_snapshots WHERE date=? AND symbol=?", (d.isoformat(), symbol)
        )
        return row is not None

    async def upsert_sectors(self, rows: list[tuple[str, str, str]]) -> tuple[int, int]:
        """Replace each named symbol's tag; count what changed.

        `new` is a symbol that had no in-scope tag and now has one; `retired`
        is one that had an in-scope tag and is now `other`. That is what the
        v2 return line reported and what the weekly digest reads. Symbols are
        compared as TEXT throughout -- awk's numeric compare made tagging `1E2`
        delete the row for `100`, and on BSD awk the real ticker `NAN` too.
        """
        new = retired = 0
        async with self._transaction() as c:
            for symbol, sector, d in rows:
                cur = await c.execute("SELECT sector FROM sectors WHERE symbol=?", (symbol,))
                prev_row = await cur.fetchone()
                prev = None if prev_row is None else str(prev_row[0])
                if sector in IN_SCOPE_SECTORS and prev not in IN_SCOPE_SECTORS:
                    new += 1
                if sector == "other" and prev in IN_SCOPE_SECTORS:
                    retired += 1
                await c.execute(
                    "INSERT INTO sectors (symbol, sector, date) VALUES (?,?,?)"
                    " ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector, date=excluded.date",
                    (symbol, sector, d),
                )
        return new, retired
```

`insert_evidence`, `evidence_for`, `insert_escalation`,
`escalation_raise_exists`, `escalations`, `replace_universe`, `universe_rows`,
`universe_asof`, `record_artifact`, `latest_account`, `latest_tick` follow the
same shape as the Phase 0b accessors already in the file (`_s()` for Decimals,
`json.dumps` for blobs, `fetchall` + a comprehension out). `escalations()`
groups raises by `id` and folds the score rows:

```python
    async def escalations(self, symbol: str | None = None) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT id, kind, symbol, at, record_json FROM escalations"
            + ("" if symbol is None else " WHERE symbol = ?")
            + " ORDER BY row_id",
            () if symbol is None else (symbol,),
        )
        raises: dict[str, dict[str, Any]] = {}
        for r in rows:
            rec = json.loads(r["record_json"])
            if r["kind"] == "raise":
                raises[r["id"]] = {
                    "id": r["id"], "symbol": r["symbol"], "raised": r["at"],
                    "latest_outcome": None, **rec,
                }
            elif r["id"] in raises:
                # last score wins: this is a plain overwrite in row order
                raises[r["id"]]["latest_outcome"] = rec.get("outcome")
        return list(raises.values())
```

`replace_universe` deletes its own `asof` first so a re-run is idempotent;
`universe_rows` reads `universe_asof()` and filters `qualified = 1` unless told
otherwise.

- [ ] **Step 5: Run the tests, then the gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/store/ tests/engine/unit/test_store_research.py
git commit -m "engine: the research ledgers are tables with the ledger's own rules -- one prediction is one row, the last score wins"
```

---

### Task 4: `DocStore` — the six documents, their validators, and the CAS

**Files:**
- Create: `engine/tc/research/__init__.py`, `engine/tc/research/docs.py`
- Test: `tests/engine/unit/test_research_docs.py`

**Interfaces:**
- Consumes: `Store.record_artifact`.
- Produces:
  - `DocKind = Literal["candidates", "standing", "scorecard", "preopen", "options-roster", "universe"]`
  - `class DocValidationError(ValueError)` and `class DocCasMismatch(ValueError)` —
    two distinct kinds, because the caller's response differs: a validation error
    means fix the body, a CAS mismatch means re-read, merge, retry **once**.
  - `class DocStore(root: Path, store: Store)`:
    - `path_for(kind: DocKind, d: date | None) -> Path`
    - `async read(kind: DocKind, d: date | None = None) -> DocView`
    - `async replace(kind, body: str, d: date | None = None, expect_last_pass: str | None = None) -> DocWrite`
  - `DocView(kind, path: str, exists: bool, body: str, last_pass: str | None, verified_as_of: str | None)`
  - `DocWrite(kind, path: str, lines: int)`

Per-kind rules, ported one for one from `0c-writers-contract.md` §1.1 and §1.2:

| kind | file | min lines | first line | required substrings | extra |
|---|---|---|---|---|---|
| `candidates` | `candidates.md` | > 10 | `# Research candidates` | `never a source for order parameters` | a `^Last pass:` line; CAS on it |
| `options-roster` | `options-roster.md` | > 5 | `# Options-viable roster` | `never a source for order parameters`, `TTL` | — |
| `preopen` | `preopen-<date>.md` | > 5 | `# Pre-open brief — <date>` (em dash) | `Pre-market data informs, it never qualifies` | `date` required |
| `scorecard` | `scorecard.md` | > 5 | `# Research scorecard` | `never loosens a gate in-flight`, `explicit conversation with Chris` | — |
| `universe` | `universe.md` | > 5 | `# Fallback universe` | `never a source for order parameters` | — |
| `standing` | `standing.md` | > 5 | `# Standing research reference` | `never a source for order parameters` | a `^Verified as of:` line |

`date` is rejected on every kind but `preopen`, and required for `preopen`.
A `.prev` snapshot is written before every replace. The v2 mkdir lock and its
`exit 5` are **not** ported: there is exactly one writer process now (spec §2.3),
so an `asyncio.Lock` per kind is the whole of the mutual exclusion and there is
no timeout to report. The CAS is taken inside that lock, so compare-and-swap is
still atomic.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_research_docs.py`:

```python
from datetime import date

import pytest

from tc.research.docs import DocCasMismatch, DocStore, DocValidationError
from tc.store.db import Store

CANDIDATES = "\n".join(
    ["# Research candidates", "",
     "**This file is never a source for order parameters — every entry re-verifies",
     "live under §4.9/§4.10.**", "",
     "Last pass: 2026-09-07 14:44 ET", "", "## HOT", "", "(none)", "", "## WATCH", "", "(none)"]
)
STANDING = "\n".join(
    ["# Standing research reference", "",
     "never a source for order parameters", "",
     "Verified as of: 2026-09-07 16:47 ET (postclose deep run)", "", "body"]
)


@pytest.fixture
async def docs(tmp_path):
    s = Store(tmp_path / "e.db")
    await s.open()
    yield DocStore(tmp_path / "research" / "docs", s)
    await s.close()


async def test_replace_then_read_surfaces_last_pass_as_a_field(docs):
    w = await docs.replace("candidates", CANDIDATES)
    assert w.lines == len(CANDIDATES.splitlines())
    v = await docs.read("candidates")
    assert v.exists is True
    assert v.last_pass == "Last pass: 2026-09-07 14:44 ET"
    assert v.verified_as_of is None


async def test_read_of_a_missing_document_is_not_an_error(docs):
    v = await docs.read("scorecard")
    assert v.exists is False and v.body == "" and v.last_pass is None


async def test_standing_surfaces_verified_as_of(docs):
    await docs.replace("standing", STANDING)
    assert (await docs.read("standing")).verified_as_of.startswith("Verified as of: 2026-09-07")


@pytest.mark.parametrize(
    "body, why",
    [
        ("# Research candidates\nshort", "line count"),
        (CANDIDATES.replace("# Research candidates", "# Candidates"), "first line"),
        (CANDIDATES.replace("never a source for order parameters", "trust me"), "banner"),
        (CANDIDATES.replace("Last pass: 2026-09-07 14:44 ET", "passed today"), "Last pass"),
    ],
)
async def test_candidates_validators(docs, body, why):
    with pytest.raises(DocValidationError):
        await docs.replace("candidates", body)


async def test_standing_requires_the_anchored_stamp(docs):
    with pytest.raises(DocValidationError):
        await docs.replace("standing", STANDING.replace("Verified as of:", "verified as of:"))


async def test_cas_refuses_a_stale_writer_and_accepts_the_fresh_one(docs):
    await docs.replace("candidates", CANDIDATES)
    stale = CANDIDATES.replace("14:44", "13:00")
    with pytest.raises(DocCasMismatch):
        await docs.replace("candidates", stale, expect_last_pass="Last pass: 2026-09-07 13:00 ET")
    fresh = CANDIDATES.replace("14:44", "15:12")
    w = await docs.replace(
        "candidates", fresh, expect_last_pass="Last pass: 2026-09-07 14:44 ET"
    )
    assert w.lines > 10
    assert (await docs.read("candidates")).last_pass.endswith("15:12 ET")


async def test_cas_on_a_missing_file_is_allowed(docs):
    await docs.replace("candidates", CANDIDATES, expect_last_pass="Last pass: whatever")


async def test_preopen_needs_its_date_and_the_em_dash(docs):
    body = "\n".join(
        ["# Pre-open brief — 2026-09-08", "",
         "Pre-market data informs, it never qualifies", "", "a", "b", "c"]
    )
    w = await docs.replace("preopen", body, d=date(2026, 9, 8))
    assert w.path.endswith("preopen-2026-09-08.md")
    with pytest.raises(DocValidationError):
        await docs.replace("preopen", body)                       # no date
    with pytest.raises(DocValidationError):
        await docs.replace("preopen", body, d=date(2026, 9, 9))   # H1 names another day
    with pytest.raises(DocValidationError):
        await docs.replace("candidates", CANDIDATES, d=date(2026, 9, 8))  # date on a 1-arity kind


async def test_previous_version_is_kept_and_the_artifact_is_indexed(docs):
    await docs.replace("candidates", CANDIDATES)
    await docs.replace("candidates", CANDIDATES.replace("14:44", "15:12"))
    prev = docs.path_for("candidates", None).with_suffix(".md.prev")
    assert "14:44" in prev.read_text()
    rows = await docs.store.fetchall("SELECT kind, lines FROM artifacts ORDER BY id")
    assert [r["kind"] for r in rows] == ["candidates", "candidates"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_research_docs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.research'`.

- [ ] **Step 3: Implement `tc/research/docs.py`**

```python
"""The six documents Claude reads whole, and the checks that stand between a
truncated heredoc and the file the next pass will trust.

Every rule here is a v2 writer's rule, ported by number rather than by reading
the bash: research-write.sh's four validators plus its compare-and-swap on the
`Last pass:` line, and research-replace.sh's per-target H1 and banner table
(0c-writers-contract.md §1.1, §1.2).

Two things deliberately did NOT survive the port:

* **The mkdir lock and its exit 5.** v2 needed advisory locking because any
  number of bash processes could write the same file; here there is exactly one
  writer process (spec §2.3) and an asyncio.Lock per kind is the whole story.
  The CAS is still taken inside that lock, so compare-and-swap stays atomic.
* **Numeric exit codes.** A caller had to map 1/3/5 back to meaning. Two
  exception types say it directly, and they are different because the response
  is different: a validation error means fix the body; a CAS mismatch means
  re-read, merge, and retry exactly once, never with the stale copy.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.store.db import Store

DocKind = Literal["candidates", "standing", "scorecard", "preopen", "options-roster", "universe"]

LAST_PASS = re.compile(r"^Last pass:.*$", re.MULTILINE)
VERIFIED_AS_OF = re.compile(r"^Verified as of:.*$", re.MULTILINE)


class DocValidationError(ValueError):
    """The body is not the document it claims to be."""


class DocCasMismatch(ValueError):
    """Someone else wrote since you read. Re-read, merge, retry once."""


@dataclass(frozen=True)
class _Spec:
    filename: str            # "{date}" is substituted for a dated kind
    min_lines: int
    first_line: str          # "{date}" likewise
    banners: tuple[str, ...]
    dated: bool
    needs_last_pass: bool = False
    needs_verified: bool = False


SPECS: dict[str, _Spec] = {
    "candidates": _Spec(
        "candidates.md", 10, "# Research candidates",
        ("never a source for order parameters",), False, needs_last_pass=True,
    ),
    "options-roster": _Spec(
        "options-roster.md", 5, "# Options-viable roster",
        ("never a source for order parameters", "TTL"), False,
    ),
    "preopen": _Spec(
        "preopen-{date}.md", 5, "# Pre-open brief — {date}",
        ("Pre-market data informs, it never qualifies",), True,
    ),
    "scorecard": _Spec(
        "scorecard.md", 5, "# Research scorecard",
        ("never loosens a gate in-flight", "explicit conversation with Chris"), False,
    ),
    "universe": _Spec(
        "universe.md", 5, "# Fallback universe",
        ("never a source for order parameters",), False,
    ),
    "standing": _Spec(
        "standing.md", 5, "# Standing research reference",
        ("never a source for order parameters",), False, needs_verified=True,
    ),
}


class DocView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    path: str
    exists: bool
    body: str
    last_pass: str | None
    verified_as_of: str | None


class DocWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    path: str
    lines: int


class DocStore:
    def __init__(self, root: Path, store: Store) -> None:
        self.root = root
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def path_for(self, kind: str, d: date | None) -> Path:
        spec = self._spec(kind)
        if spec.dated:
            if d is None:
                raise DocValidationError(f"{kind} needs a date")
            return self.root / spec.filename.format(date=d.isoformat())
        if d is not None:
            raise DocValidationError(f"{kind} takes no date")
        return self.root / spec.filename

    @staticmethod
    def _spec(kind: str) -> _Spec:
        try:
            return SPECS[kind]
        except KeyError:
            raise DocValidationError(f"unknown document kind {kind!r}") from None

    async def read(self, kind: str, d: date | None = None) -> DocView:
        path = self.path_for(kind, d)
        body = path.read_text() if path.exists() else ""
        lp = LAST_PASS.search(body)
        va = VERIFIED_AS_OF.search(body)
        return DocView(
            kind=kind, path=str(path), exists=path.exists(), body=body,
            last_pass=lp.group(0).strip() if lp else None,
            verified_as_of=va.group(0).strip() if va else None,
        )

    def _validate(self, kind: str, body: str, d: date | None) -> None:
        spec = self._spec(kind)
        lines = body.splitlines()
        if len(lines) <= spec.min_lines:
            raise DocValidationError(
                f"{kind}: {len(lines)} lines, need more than {spec.min_lines}"
                " (truncated heredoc?)"
            )
        want = spec.first_line.format(date=d.isoformat()) if spec.dated else spec.first_line
        if not lines or lines[0].strip() != want:
            raise DocValidationError(f"{kind}: first line must be exactly {want!r}")
        for banner in spec.banners:
            if banner not in body:
                raise DocValidationError(f"{kind}: body must contain {banner!r}")
        if spec.needs_last_pass and not LAST_PASS.search(body):
            raise DocValidationError(f"{kind}: body must carry a line starting 'Last pass:'")
        if spec.needs_verified and not VERIFIED_AS_OF.search(body):
            raise DocValidationError(f"{kind}: body must carry a line starting 'Verified as of:'")

    async def replace(
        self, kind: str, body: str, d: date | None = None, expect_last_pass: str | None = None
    ) -> DocWrite:
        path = self.path_for(kind, d)          # raises on a date/arity mismatch
        self._validate(kind, body, d)
        async with self._lock(str(path)):
            if expect_last_pass is not None and path.exists():
                current = LAST_PASS.search(path.read_text())
                have = current.group(0).strip() if current else None
                if have != expect_last_pass.strip():
                    raise DocCasMismatch(
                        f"{kind} moved under you: expected {expect_last_pass!r}, found {have!r}."
                        " Re-read, merge onto the fresh copy, retry once."
                    )
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.with_suffix(".md.prev").write_text(path.read_text())
            text = body if body.endswith("\n") else body + "\n"
            path.write_text(text)
            lines = len(text.splitlines())
            await self.store.record_artifact(
                kind, d, str(path), hashlib.sha256(text.encode()).hexdigest(), lines
            )
        return DocWrite(kind=kind, path=str(path), lines=lines)
```

- [ ] **Step 4: Run the tests, then the gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/research/ tests/engine/unit/test_research_docs.py
git commit -m "research: the documents keep every v2 validator and the compare-and-swap, and lose the exit codes"
```

---

### Task 5: `tc/research/ledgers.py` — the validating writers

**Files:**
- Create: `engine/tc/research/ledgers.py`
- Test: `tests/engine/unit/test_research_ledgers.py`

**Interfaces:**
- Consumes: `Store` (Task 3), `IN_SCOPE_SECTORS`, `LEDGER_TABLES`.
- Produces (all `async`, all raising `LedgerError` on refusal):
  - `class LedgerError(ValueError)`
  - `evidence_append(store, symbol: str, d: date, record: dict) -> dict` → `{"appended": True, "symbol": ...}`
  - `escalation_raise(store, symbol: str, d: date, record: dict) -> dict` → `{"id": ...}`
  - `escalation_score(store, escalation_id: str, outcome: Literal["right","wrong","void"], d: date) -> dict`
  - `sector_write(store, rows: list[dict], d: date) -> dict` → `{"written": n, "new": n, "retired": n}`
  - `ledger_append(store, name: str, d: date, record: dict) -> dict` → `{"appended": bool, "reason": str | None}`
  - `tombstone(store, symbol: str, d: date, gate: str, reason: str, ref_price: Decimal, hypo_qty: int | None = None, hypo_stop: Decimal | None = None) -> dict`
  - `SOURCE_TYPES`, `BAR_SOURCE_TYPES`, `escalation_id(symbol, d, record) -> str`

Validation, ported by number from `0c-writers-contract.md`:

- **`evidence_append`** (§1.4): `symbol` non-empty and matching `[A-Za-z0-9.-]+`;
  `date` `YYYY-MM-DD`; `claim`, `url`, `independence` typed strings with
  length > 0; `source_type`, `observed` typed strings; `source_type` in the
  closed set `end-user | employee | counterparty | enthusiast | primary-doc |
  mainstream`; **`observed` must equal the `date` argument**. Typed-non-null
  throughout, never `has()` — `has()` is true for an explicit null, which is the
  exact bug the v2 script was hardened against.
- **`escalation_raise`** (§1.5): the same symbol/date checks; `claim` non-empty
  string; `direction` in `{up, down}`; `event_date` a date string;
  `source_types` a JSON **array** (type-checked before length, so a bare string
  cannot clear a "2 entries" bar on its character count); at least **2 distinct**
  members of `BAR_SOURCE_TYPES`, which is `SOURCE_TYPES` **minus `mainstream`**;
  the payload may not carry `outcome`, `scored` or `kind`; the id is
  `f"{symbol}-{d}-{crc32(canonical json)}"` over `json.dumps(record, sort_keys=True,
  separators=(",",":"))` so formatting cannot change the hash; a duplicate id is
  refused.
- **`sector_write`** (§1.6): every row validated **before any row is written**;
  `sector` in the closed set `consumer-software | airlines-transport |
  semis-hardware | other`; a batch naming the same symbol twice keeps the **last**
  entry; symbols compared as strings.
- **`ledger_append`** (§1.3, §1.7, §1.8) required keys per name:
  `screen` → `symbol`, `t`, `src`; `iv` → `symbol`, `t`, `atm_iv`;
  `oi` → `symbol`, `t`; `tombstones` → `symbol`, `date`, `gate`, `reason`,
  `ref_price` **and** `record["date"] == d`; `events` → `t`. All typed-non-null.
  `oi` is idempotent per symbol per day and returns
  `{"appended": False, "reason": "already snapshotted today"}` — **not an error**,
  because the caller's correct response is to skip that underlying and continue.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_research_ledgers.py`:

```python
from datetime import date
from decimal import Decimal as D

import pytest

from tc.research.ledgers import (
    LedgerError, escalation_raise, escalation_score, evidence_append,
    ledger_append, sector_write, tombstone,
)
from tc.store.db import Store

D7 = date(2026, 9, 7)
OBS = {"claim": "queue times doubled at three sites", "url": "https://forum/1",
       "source_type": "end-user", "observed": "2026-09-07",
       "independence": "unaffiliated poster, no cross-links"}
RAISE = {"claim": "unit shipments up materially into the print", "direction": "up",
         "event_date": "2026-10-14", "source_types": ["end-user", "counterparty"]}


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_evidence_happy_path(store):
    assert (await evidence_append(store, "CSX", D7, OBS))["appended"] is True
    assert len(await store.evidence_for("CSX")) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        {"claim": ""},                    # empty string, not merely present
        {"claim": None},                  # explicit null -- has() would pass this
        {"url": None},
        {"independence": ""},
        {"source_type": "blog"},          # outside the closed set
        {"observed": "2026-09-06"},       # != the date argument
    ],
)
async def test_evidence_refusals(store, mutate):
    with pytest.raises(LedgerError):
        await evidence_append(store, "CSX", D7, {**OBS, **mutate})


async def test_evidence_symbol_charset(store):
    await evidence_append(store, "BRK.B", D7, OBS)          # a dot is legal
    with pytest.raises(LedgerError):
        await evidence_append(store, "C SX", D7, OBS)


async def test_mainstream_is_recordable_but_never_counts_toward_the_bar(store):
    await evidence_append(store, "CSX", D7, {**OBS, "source_type": "mainstream"})
    with pytest.raises(LedgerError):
        await escalation_raise(
            store, "CSX", D7, {**RAISE, "source_types": ["end-user", "mainstream"]}
        )


@pytest.mark.parametrize(
    "mutate",
    [
        {"source_types": ["end-user"]},                  # one distinct type
        {"source_types": ["end-user", "end-user"]},      # two entries, one type
        {"source_types": "end-user,counterparty"},       # a string, not an array
        {"direction": "sideways"},
        {"event_date": "next month"},
        {"outcome": "right"},                            # a raise may not carry its outcome
        {"scored": "2026-09-08"},
        {"kind": "raise"},
    ],
)
async def test_escalation_bar(store, mutate):
    with pytest.raises(LedgerError):
        await escalation_raise(store, "CSX", D7, {**RAISE, **mutate})


async def test_escalation_id_is_canonical_and_deduped(store):
    a = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    assert a.startswith("CSX-2026-09-07-")
    reordered = {k: RAISE[k] for k in reversed(list(RAISE))}
    with pytest.raises(LedgerError):        # same claim, different key order, same id
        await escalation_raise(store, "CSX", D7, reordered)


async def test_score_needs_a_raise_and_a_legal_outcome(store):
    eid = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    with pytest.raises(LedgerError):
        await escalation_score(store, eid, "maybe", D7)
    with pytest.raises(LedgerError):
        await escalation_score(store, "CSX-2026-09-07-nope", "right", D7)
    await escalation_score(store, eid, "right", D7)
    assert (await store.escalations())[0]["latest_outcome"] == "right"


async def test_sector_write_validates_every_row_before_writing_any(store):
    with pytest.raises(LedgerError):
        await sector_write(
            store,
            [{"symbol": "CSX", "sector": "airlines-transport"},
             {"symbol": "NOPE", "sector": "biotech"}],
            D7,
        )
    assert await store.sectors() == []            # nothing was written


async def test_sector_write_last_row_wins_and_counts(store):
    out = await sector_write(
        store,
        [{"symbol": "CSX", "sector": "semis-hardware"},
         {"symbol": "CSX", "sector": "airlines-transport"}],
        D7,
    )
    assert out == {"written": 1, "new": 1, "retired": 0}
    assert (await store.sectors())[0]["sector"] == "airlines-transport"


@pytest.mark.parametrize(
    "name, record",
    [
        ("screen", {"symbol": "SAN", "t": "16:42:00", "src": "screener"}),
        ("iv", {"symbol": "CSX", "t": "16:26:00", "atm_iv": 0.208}),
        ("events", {"t": "08:26:00", "event": "deep_research", "mode": "preopen"}),
    ],
)
async def test_ledger_append_happy_paths(store, name, record):
    assert (await ledger_append(store, name, D7, record))["appended"] is True
    assert len(await store.ledger_rows(name, D7)) == 1


@pytest.mark.parametrize(
    "name, record",
    [
        ("screen", {"symbol": "SAN", "t": "16:42:00"}),          # no src
        ("iv", {"symbol": "CSX", "t": "16:26:00", "atm_iv": None}),  # explicit null
        ("events", {"event": "x"}),                               # no t
        ("nosuch", {"t": "1"}),
    ],
)
async def test_ledger_append_refusals(store, name, record):
    with pytest.raises(LedgerError):
        await ledger_append(store, name, D7, record)


async def test_oi_second_snapshot_is_a_skip_not_an_error(store):
    rec = {"symbol": "CSX", "t": "16:26:00", "call_oi": 2874, "put_oi": 1902}
    assert (await ledger_append(store, "oi", D7, rec))["appended"] is True
    again = await ledger_append(store, "oi", D7, rec)
    assert again == {"appended": False, "reason": "already snapshotted today"}


async def test_tombstone_cross_checks_its_own_date(store):
    await tombstone(store, "MNDY", D7, "gap-and-hold", "faded the gap", D("88.50"))
    assert (await store.ledger_rows("tombstones", D7))[0]["record"]["date"] == "2026-09-07"
    with pytest.raises(LedgerError):
        await ledger_append(
            store, "tombstones", D7,
            {"symbol": "X", "date": "2026-09-06", "gate": "g", "reason": "r", "ref_price": 1.0},
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_research_ledgers.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.research.ledgers'`.

- [ ] **Step 3: Implement `tc/research/ledgers.py`**

```python
"""The validating ledger writers, ported rule for rule from the bash they
replace (0c-writers-contract.md §1.3-§1.8).

Three properties are load-bearing and are why this is not a thin insert:

* **Typed and non-empty, never `has()`.** `has("claim")` is true for an
  explicit null, and v2 was hardened against exactly that after nulls reached
  the evidence ledger. Every required field here is checked for type AND
  emptiness.
* **`mainstream` is recordable evidence that never counts toward the escalation
  bar.** It is the kill-switch record -- the thing you write down when the story
  is already public -- so it must be storable and must not clear a bar.
* **Empty is a correct answer; already-done is not an error.** A second OI
  snapshot for a symbol on a day returns `appended: False` with a reason, and
  the caller skips that underlying and continues. v2 said this with exit 4 and
  a comment reading "Not an error; skip this underlying."
"""

from __future__ import annotations

import json
import re
import zlib
from datetime import date
from decimal import Decimal
from typing import Any

from tc.store.db import IN_SCOPE_SECTORS, LEDGER_TABLES, Store

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SOURCE_TYPES = (
    "end-user", "employee", "counterparty", "enthusiast", "primary-doc", "mainstream",
)
# The escalation bar counts these only. `mainstream` is deliberately absent:
# corroboration by something already on the wire is not an information edge.
BAR_SOURCE_TYPES = tuple(t for t in SOURCE_TYPES if t != "mainstream")
SECTORS = (*IN_SCOPE_SECTORS, "other")
DIRECTIONS = ("up", "down")
OUTCOMES = ("right", "wrong", "void")

LEDGER_REQUIRED: dict[str, tuple[str, ...]] = {
    "screen": ("symbol", "t", "src"),
    "iv": ("symbol", "t", "atm_iv"),
    "oi": ("symbol", "t"),
    "tombstones": ("symbol", "date", "gate", "reason", "ref_price"),
    "events": ("t",),
}


class LedgerError(ValueError):
    """A refusal with a reason the caller can act on."""


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not value or not SYMBOL_RE.match(value):
        raise LedgerError(f"symbol must be non-empty and match [A-Za-z0-9.-]+, got {value!r}")
    return value


def _date_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise LedgerError(f"{field} must be YYYY-MM-DD, got {value!r}")
    return value


def _nonempty_str(record: dict[str, Any], key: str) -> str:
    v = record.get(key)
    if not isinstance(v, str) or not v:
        raise LedgerError(f"{key} must be a non-empty string, got {v!r}")
    return v


def _present(record: dict[str, Any], key: str) -> Any:
    v = record.get(key)
    if v is None or v == "":
        raise LedgerError(f"{key} must be present and not null")
    return v


async def evidence_append(
    store: Store, symbol: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    sym = _symbol(symbol)
    ds = d.isoformat()
    claim = _nonempty_str(record, "claim")
    url = _nonempty_str(record, "url")
    independence = _nonempty_str(record, "independence")
    source_type = _nonempty_str(record, "source_type")
    observed = _nonempty_str(record, "observed")
    if source_type not in SOURCE_TYPES:
        raise LedgerError(f"source_type must be one of {SOURCE_TYPES}, got {source_type!r}")
    if observed != ds:
        # The ledger's value is the delta against a name's history; a wrong
        # observation date poisons every future comparison.
        raise LedgerError(f"observed {observed!r} must equal the date argument {ds!r}")
    extra = {k: v for k, v in record.items()
             if k not in {"claim", "url", "source_type", "observed", "independence"}}
    await store.insert_evidence(
        {"symbol": sym, "date": ds, "claim": claim, "url": url, "source_type": source_type,
         "observed": observed, "independence": independence, "extra": extra}
    )
    return {"appended": True, "symbol": sym, "date": ds}


def escalation_id(symbol: str, d: date, record: dict[str, Any]) -> str:
    """CRC32 over the CANONICAL claim, so re-ordering keys cannot mint a
    second id for the same prediction."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return f"{symbol}-{d.isoformat()}-{zlib.crc32(canonical.encode()) & 0xFFFFFFFF}"


async def escalation_raise(
    store: Store, symbol: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    sym = _symbol(symbol)
    for reserved in ("outcome", "scored", "kind"):
        if reserved in record:
            raise LedgerError(f"a raise may not carry {reserved!r}: scoring is a separate row")
    _nonempty_str(record, "claim")
    direction = _nonempty_str(record, "direction")
    if direction not in DIRECTIONS:
        raise LedgerError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    _date_str(_present(record, "event_date"), "event_date")
    types = record.get("source_types")
    # Type before length: a bare string would clear a "2 entries" bar on its
    # character count.
    if not isinstance(types, list):
        raise LedgerError("source_types must be a JSON array")
    distinct = {t for t in types if isinstance(t, str) and t in BAR_SOURCE_TYPES}
    if len(distinct) < 2:
        raise LedgerError(
            f"the bar is 2 distinct source types from {BAR_SOURCE_TYPES}"
            f" (mainstream never counts); got {sorted(distinct)}"
        )
    eid = escalation_id(sym, d, record)
    if await store.escalation_raise_exists(eid):
        raise LedgerError(f"already raised: {eid}")
    await store.insert_escalation(
        {"id": eid, "kind": "raise", "symbol": sym, "at": d.isoformat(), "record": record}
    )
    return {"id": eid}


async def escalation_score(
    store: Store, escalation_id_: str, outcome: str, d: date
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise LedgerError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    if not await store.escalation_raise_exists(escalation_id_):
        # Matched as a literal id, never a pattern: tickers carry dots and a
        # regex would attach an outcome to the wrong prediction.
        raise LedgerError(f"no such raise: {escalation_id_}")
    await store.insert_escalation(
        {"id": escalation_id_, "kind": "score", "symbol": escalation_id_.split("-")[0],
         "at": d.isoformat(), "record": {"outcome": outcome}}
    )
    return {"id": escalation_id_, "outcome": outcome}


async def sector_write(
    store: Store, rows: list[dict[str, Any]], d: date
) -> dict[str, Any]:
    if not rows:
        raise LedgerError("no rows to write")
    ds = d.isoformat()
    validated: dict[str, tuple[str, str, str]] = {}
    for i, row in enumerate(rows, start=1):
        sym = _symbol(row.get("symbol"))
        sector = row.get("sector")
        if sector not in SECTORS:
            # An unrecognised tag would silently shrink the universe the scout
            # sweeps, so the whole batch is refused with the offending row named.
            raise LedgerError(f"row {i}: sector must be one of {SECTORS}, got {sector!r}")
        validated[sym] = (sym, sector, ds)     # a repeated symbol keeps the LAST row
    new, retired = await store.upsert_sectors(list(validated.values()))
    return {"written": len(validated), "new": new, "retired": retired}


async def ledger_append(
    store: Store, name: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    if name not in LEDGER_REQUIRED or name not in LEDGER_TABLES:
        raise LedgerError(f"unknown ledger {name!r}, expected one of {sorted(LEDGER_REQUIRED)}")
    if not isinstance(record, dict):
        raise LedgerError("record must be an object")
    for key in LEDGER_REQUIRED[name]:
        _present(record, key)
    if name == "tombstones" and record["date"] != d.isoformat():
        raise LedgerError(f"tombstone date {record['date']!r} != the date argument {d.isoformat()!r}")
    symbol = record.get("symbol")
    if name == "oi":
        sym = _symbol(symbol)
        if await store.oi_symbol_seen(d, sym):
            return {"appended": False, "reason": "already snapshotted today"}
    await store.append_ledger(name, d, None if symbol is None else str(symbol), record)
    return {"appended": True, "reason": None}


async def tombstone(
    store: Store, symbol: str, d: date, gate: str, reason: str, ref_price: Decimal,
    hypo_qty: int | None = None, hypo_stop: Decimal | None = None,
) -> dict[str, Any]:
    """The typed front door onto the same validator `ledger_append` uses, so a
    tombstone cannot be written two ways with two sets of rules."""
    record: dict[str, Any] = {
        "symbol": _symbol(symbol), "date": d.isoformat(), "gate": gate,
        "reason": reason, "ref_price": str(ref_price),
    }
    if hypo_qty is not None:
        record["hypo_qty"] = hypo_qty
    if hypo_stop is not None:
        record["hypo_stop"] = str(hypo_stop)
    return await ledger_append(store, "tombstones", d, record)
```

- [ ] **Step 4: Run the tests, then the gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/research/ledgers.py tests/engine/unit/test_research_ledgers.py
git commit -m "research: every writer's validation survives the port, including the two the bash was hardened for"
```

---

### Task 6: `cohort(date)` as a pure function

**Files:**
- Create: `engine/tc/research/cohort.py`
- Test: `tests/engine/unit/test_cohort.py`

**Interfaces:**
- Consumes: `Store.universe_rows`, `Store.sectors`, `Rules`.
- Produces:
  - `CohortRow(symbol: str, sector: str, est_next_earnings: date, days_out: int)` (pydantic, `extra="forbid"`).
  - `async cohort(store: Store, rules: Rules, d: date) -> list[CohortRow]`
  - `QUARTER_DAYS = 91`

Ported from `cohort.sh` (`0c-writers-contract.md` §1.14):

- Window from `rules.yml`: `strategy.scout_entry_window_min_days` (21) and
  `strategy.scout_entry_window_max_days` (42). Never a literal.
- `QUARTER_DAYS = 91` is the one hard-coded calendar constant, on purpose:
  `rules.yml` carries no key for it, and inventing one would put a calendar fact
  in a risk-parameter file.
- The join is universe (newest `asof`, `qualified` only) ⋈ sectors on symbol.
- A symbol that is untagged, or tagged `other`, is skipped.
- `last_earnings` that is not a valid date is skipped, never guessed —
  the awk port returned a `BAD` sentinel rather than a wrong number.
- `est_next = last_earnings + 91 days`; `days_out = est_next − d`; emit when
  `min ≤ days_out ≤ max`.
- Sorted by `(days_out, symbol)`.
- **Empty is a correct answer**, including when the universe or the sectors table
  is empty: between earnings seasons and before the first weekly sweep the right
  answer is no rows, and a raised exception would make the deadman cry wolf daily.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/unit/test_cohort.py`:

```python
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from tc.research.cohort import QUARTER_DAYS, cohort
from tc.rules.model import Rules
from tc.store.db import Store

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
TODAY = date(2026, 9, 7)


def _u(symbol: str, last_earnings: str, qualified: bool = True) -> dict:
    return {"symbol": symbol, "price": D("50"), "adv10": D("1000000"),
            "dollar_vol": D("50000000"), "pct_from_52wk_high": D("1.0"), "optionable": True,
            "leverage": D("0"), "last_earnings": last_earnings, "is_etf": False,
            "session_range_pct": D("1.2"), "description": symbol, "qualified": qualified}


def _le(days_out: int) -> str:
    """A last_earnings date that puts the next print `days_out` from TODAY."""
    return (TODAY + timedelta(days=days_out - QUARTER_DAYS)).isoformat()


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_empty_store_is_an_empty_cohort_not_an_error(store):
    assert await cohort(store, RULES, TODAY) == []


async def test_window_edges_are_inclusive(store):
    lo = int(RULES.get("strategy", "scout_entry_window_min_days"))
    hi = int(RULES.get("strategy", "scout_entry_window_max_days"))
    await store.replace_universe(
        date(2026, 9, 5),
        [_u("EDGELO", _le(lo)), _u("EDGEHI", _le(hi)),
         _u("TOOSOON", _le(lo - 1)), _u("TOOFAR", _le(hi + 1))],
    )
    await store.upsert_sectors(
        [(s, "semis-hardware", "2026-09-05") for s in ("EDGELO", "EDGEHI", "TOOSOON", "TOOFAR")]
    )
    rows = await cohort(store, RULES, TODAY)
    assert [r.symbol for r in rows] == ["EDGELO", "EDGEHI"]
    assert rows[0].days_out == lo and rows[1].days_out == hi


async def test_untagged_and_other_are_out_of_scope(store):
    await store.replace_universe(
        date(2026, 9, 5), [_u("TAGGED", _le(30)), _u("OTHER", _le(30)), _u("UNTAGGED", _le(30))]
    )
    await store.upsert_sectors(
        [("TAGGED", "consumer-software", "2026-09-05"), ("OTHER", "other", "2026-09-05")]
    )
    assert [r.symbol for r in await cohort(store, RULES, TODAY)] == ["TAGGED"]


async def test_unqualified_and_undated_rows_are_skipped_not_guessed(store):
    await store.replace_universe(
        date(2026, 9, 5),
        [_u("NOEARN", ""), _u("BADDATE", "not-a-date"), _u("UNQUAL", _le(30), qualified=False)],
    )
    await store.upsert_sectors(
        [(s, "semis-hardware", "2026-09-05") for s in ("NOEARN", "BADDATE", "UNQUAL")]
    )
    assert await cohort(store, RULES, TODAY) == []


async def test_sorted_by_days_out_then_symbol(store):
    await store.replace_universe(
        date(2026, 9, 5), [_u("ZZZZ", _le(25)), _u("AAAA", _le(25)), _u("MMMM", _le(22))]
    )
    await store.upsert_sectors(
        [(s, "airlines-transport", "2026-09-05") for s in ("ZZZZ", "AAAA", "MMMM")]
    )
    assert [r.symbol for r in await cohort(store, RULES, TODAY)] == ["MMMM", "AAAA", "ZZZZ"]


async def test_est_next_is_the_estimated_next_print_not_the_last_one(store):
    await store.replace_universe(date(2026, 9, 5), [_u("CSX", _le(30))])
    await store.upsert_sectors([("CSX", "airlines-transport", "2026-09-05")])
    row = (await cohort(store, RULES, TODAY))[0]
    assert row.est_next_earnings == TODAY + timedelta(days=30)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_cohort.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.research.cohort'`.

- [ ] **Step 3: Implement `tc/research/cohort.py`**

```python
"""The active earnings cohort: the right-hand side of the scout's daily work.

A port of cohort.sh, whose awk implementation carried Howard Hinnant's civil
date algorithms because `date -d` is GNU-only. Here it is `datetime.timedelta`,
which is the whole reason this belongs in Python.

Three of that script's decisions are kept exactly:

* **Empty is a correct answer, and never an error.** Between earnings seasons,
  and before the first weekly sweep has ever run, the honest cohort is zero
  rows. A raised exception here would make the deadman cry wolf every day.
* **An unparseable last-earnings date is skipped, not guessed.** The awk version
  returned a BAD sentinel rather than a wrong number, because a wrong number
  silently puts a name in or out of the window.
* **`other` is out of scope, and so is untagged.** `other` exists only to retire
  a previously in-scope tag.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict

from tc.rules.model import Rules
from tc.store.db import IN_SCOPE_SECTORS, Store

# A calendar fact, not a risk parameter: rules.yml carries no key for it, and
# inventing one would put "a quarter is 91 days" in the file that holds caps.
QUARTER_DAYS = 91


class CohortRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    sector: str
    est_next_earnings: date
    days_out: int


async def cohort(store: Store, rules: Rules, d: date) -> list[CohortRow]:
    win_min = int(rules.get("strategy", "scout_entry_window_min_days"))
    win_max = int(rules.get("strategy", "scout_entry_window_max_days"))
    tags = {r["symbol"]: r["sector"] for r in await store.sectors()}
    out: list[CohortRow] = []
    for row in await store.universe_rows(qualified_only=True):
        sector = tags.get(row["symbol"])
        if sector is None or sector not in IN_SCOPE_SECTORS:
            continue
        try:
            last = date.fromisoformat(str(row["last_earnings"])[:10])
        except ValueError:
            continue
        est_next = last + timedelta(days=QUARTER_DAYS)
        days_out = (est_next - d).days
        if win_min <= days_out <= win_max:
            out.append(
                CohortRow(
                    symbol=row["symbol"], sector=sector,
                    est_next_earnings=est_next, days_out=days_out,
                )
            )
    out.sort(key=lambda r: (r.days_out, r.symbol))
    return out
```

- [ ] **Step 4: Run the tests, then the gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/research/cohort.py tests/engine/unit/test_cohort.py
git commit -m "research: the cohort is a join with a window from rules.yml, and an empty one is an answer"
```

---

### Task 7: The MCP server — two roles, one middleware, and the contract test

**Files:**
- Create: `engine/tc/mcp/__init__.py`, `engine/tc/mcp/registry.py`, `engine/tc/mcp/server.py`
- Modify: `engine/tc/http/app.py`, `engine/tc/rules/consistency.py`
- Test: `tests/engine/unit/test_mcp_server.py`, `tests/engine/contract/test_mcp_no_order_tools.py`, `tests/engine/unit/test_consistency.py`

**Interfaces:**
- Produces:
  - `tc/mcp/registry.py`:
    - `Role = Literal["research", "decide"]`
    - `READ_TOOLS: tuple[str, ...]` — the eleven Task 8 names
    - `RESEARCH_TOOLS: tuple[str, ...]` — the Task 9 names
    - `DECIDE_TOOLS: tuple[str, ...]` — `()` in this plan; Plan 1 adds `propose_*`
    - `ROLE_TOOLS: dict[Role, tuple[str, ...]]`
    - `FORBIDDEN = re.compile(r"place|cancel|replace|order")`
    - `def forbidden_tools() -> list[tuple[str, str]]` — `(role, tool)` pairs that match
  - `tc/mcp/server.py`:
    - `@dataclass McpDeps(store, broker, docs: DocStore, rules: Rules, settings: Settings, clock: Callable[[], datetime], account_hash: Callable[[], str | None])`
    - `def build_servers(deps: McpDeps) -> dict[Role, FastMCP]`
    - `class RoleAuthMiddleware(BaseHTTPMiddleware)` — `__init__(app, tokens: dict[str, str])`
    - `def mcp_routes(servers) -> list[Mount]`, `def mcp_lifespan(servers)` (an
      `asynccontextmanager` entering every `session_manager.run()` on one `AsyncExitStack`)
  - `build_app(state, *, mcp=None)` in `http/app.py` gains an optional
    `McpMounts(servers, tokens)` argument; when present it adds the two mounts,
    installs the middleware **scoped to `/mcp`** and chains the MCP lifespan.
  - `tc/rules/consistency.py` gains `check_tool_registry(root, rules)` in `CHECKS`.

Behaviour, from `0c-sdk-facts.md` §3.2 and §3.3 (working code, executed):

- Two **physically separate** `FastMCP(name="engine", streamable_http_path="/")`
  instances with disjoint tool sets, mounted at `/mcp/research` and `/mcp/decide`.
  Not one server filtering by token: FastMCP's registry is process-global, not
  per-request, so a filtered single server is a gate the model could be talked
  past. Disjoint registries mean a research token cannot reach a decide tool
  **at all**.
- The middleware maps `Authorization: Bearer <t>` → role, then checks the role
  against the mount prefix: 401 with no bearer, 403 on an unknown token, 403 when
  the role and the prefix disagree.
- **Both session managers must be entered in the app's own lifespan.**
  `streamable_http_app()` builds the manager but does not start it, and mounting
  into our Starlette app replaces FastMCP's lifespan with ours. Forgetting this
  is the classic 500.
- Clients are given the **trailing-slash** URL; `/mcp/research` without it costs a
  307 on every message.
- Tool names Claude sees are `mcp__<client config key>__<tool>` — the prefix comes
  from the runner's `mcp_servers` key, which Task 10 fixes at `engine`. The server
  cannot change it, so the allowlists and the prompts both say `mcp__engine__*`.

- [ ] **Step 1: Write the failing server tests**

Create `tests/engine/unit/test_mcp_server.py`:

```python
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tc.mcp.registry import ROLE_TOOLS


@pytest.fixture
async def served(engine_app_factory):
    """engine_app_factory is a conftest fixture that builds the real Starlette
    app over a tmp store/fixture broker and serves it on a free port with
    uvicorn, yielding (base_url, tokens)."""
    async with engine_app_factory() as ctx:
        yield ctx


async def test_no_bearer_is_401(served):
    base, _ = served
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{base}/mcp/research/", json={})
        assert r.status_code == 401


async def test_unknown_token_is_403(served):
    base, _ = served
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{base}/mcp/research/", json={}, headers={"Authorization": "Bearer nope"}
        )
        assert r.status_code == 403


async def test_research_token_on_the_decide_mount_is_403(served):
    base, tokens = served
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{base}/mcp/decide/", json={},
            headers={"Authorization": f"Bearer {tokens['research']}"},
        )
        assert r.status_code == 403


async def test_each_role_lists_exactly_its_registry(served):
    base, tokens = served
    for role, expected in ROLE_TOOLS.items():
        headers = {"Authorization": f"Bearer {tokens[role]}"}
        async with streamablehttp_client(f"{base}/mcp/{role}/", headers=headers) as (r_, w_, _):
            async with ClientSession(r_, w_) as session:
                await session.initialize()
                names = sorted(t.name for t in (await session.list_tools()).tools)
        assert names == sorted(expected), role


async def test_a_read_tool_answers_over_http(served):
    base, tokens = served
    headers = {"Authorization": f"Bearer {tokens['research']}"}
    async with streamablehttp_client(f"{base}/mcp/research/", headers=headers) as (r_, w_, _):
        async with ClientSession(r_, w_) as session:
            await session.initialize()
            res = await session.call_tool("get_datetime", {})
    assert res.isError is False
    assert res.structuredContent["tz"] == "America/New_York"


async def test_health_still_answers_with_mcp_mounted(served):
    base, _ = served
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{base}/health")).status_code == 200
```

Add to `tests/engine/conftest.py`:

```python
import contextlib
import socket

import pytest
import uvicorn


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


@pytest.fixture
def engine_app_factory(tmp_path):
    """The real app -- routes, middleware and both MCP lifespans -- on a free
    loopback port. Tests that only need a tool's behaviour call the function
    directly; these ones need the wire, because the wire is the gate."""

    @contextlib.asynccontextmanager
    async def factory():
        from tc.http.app import McpMounts, build_app
        # ... build Store, FakeBroker, DocStore, Rules, EngineState (see test_http.py) ...
        tokens = {"research": "research-token-test", "decide": "decide-token-test"}
        servers = build_servers(deps)
        app = build_app(
            state,
            mcp=McpMounts(servers=servers, tokens={v: k for k, v in tokens.items()}),
        )
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", tokens
        finally:
            server.should_exit = True
            await task

    return factory
```

Create `tests/engine/contract/test_mcp_no_order_tools.py`:

```python
"""Spec §10: for EVERY MCP role, no tool name may match place|cancel|replace|order.

This is the contract test the design calls "the single largest safety
simplification": the order path is deterministic engine code, and the model's
only write-shaped tools will be `propose_*` (Plan 1). The check runs against the
LIVE servers, not the registry table, so a tool registered without being listed
still trips it.
"""

import re

from tc.mcp.registry import FORBIDDEN, ROLE_TOOLS, forbidden_tools


async def test_registry_declares_no_order_shaped_tool():
    assert forbidden_tools() == []


async def test_no_live_server_exposes_an_order_shaped_tool(engine_servers):
    for role, server in engine_servers.items():
        names = [t.name for t in await server.list_tools()]
        assert names, f"{role} registered no tools at all"
        offenders = [n for n in names if FORBIDDEN.search(n)]
        assert offenders == [], f"{role} exposes {offenders}"


async def test_live_servers_match_the_declared_registry(engine_servers):
    for role, server in engine_servers.items():
        assert sorted(t.name for t in await server.list_tools()) == sorted(ROLE_TOOLS[role])
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_mcp_server.py ../tests/engine/contract/test_mcp_no_order_tools.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.mcp'`.

- [ ] **Step 3: Write `tc/mcp/registry.py`**

```python
"""Who may call what, stated once.

This table is the single source for three consumers: `server.py` registers
exactly these names, `jobs/spec.py` builds each job's allowlist out of them, and
`rules/consistency.py` fails the build if any of them is order-shaped. Keeping
it as data rather than as a property of the FastMCP objects means the
consistency checker can read it without standing up a server.
"""

from __future__ import annotations

import re
from typing import Literal

Role = Literal["research", "decide"]

READ_TOOLS: tuple[str, ...] = (
    "get_datetime", "market_hours", "quotes", "price_history", "option_chain",
    "expiration_chain", "instruments", "movers", "status_latest", "rules",
)
# `book` is the held-position view. The research roles have never had account
# tools (0c-writers-contract.md §4.3) and do not get them here.
DECIDE_ONLY_READ_TOOLS: tuple[str, ...] = ("book",)

RESEARCH_TOOLS: tuple[str, ...] = (
    # writers
    "evidence_append", "escalation_raise", "escalation_score", "sector_write",
    "ledger_append", "tombstone", "doc_replace",
    # readers the prompts cannot run without
    "doc_read", "evidence_read", "escalations_read", "ledger_read", "sectors_read",
    "cohort", "universe_symbols", "universe_names_page", "alert_read",
)

# Empty on purpose: Plan 1 adds propose_entry / propose_exit /
# propose_option_close / get_proposal here and nowhere else.
DECIDE_TOOLS: tuple[str, ...] = ()

ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "research": READ_TOOLS + RESEARCH_TOOLS,
    "decide": READ_TOOLS + DECIDE_ONLY_READ_TOOLS + DECIDE_TOOLS,
}

FORBIDDEN = re.compile(r"place|cancel|replace|order")


def forbidden_tools() -> list[tuple[str, str]]:
    return [
        (role, name)
        for role, names in ROLE_TOOLS.items()
        for name in names
        if FORBIDDEN.search(name)
    ]
```

- [ ] **Step 4: Write `tc/mcp/server.py`**

```python
"""The engine's MCP surface: two roles, two mounts, one middleware.

Shape verified by execution (0c-sdk-facts.md §3.2-§3.3):

* Two FastMCP instances with DISJOINT tool registries, not one server filtering
  per token. FastMCP's registry is process-global, so a per-request filter would
  be a gate inside the thing being gated; disjoint registries make a decide tool
  unreachable with a research token rather than merely unchosen.
* `streamable_http_path="/"` plus `Mount("/mcp/<role>", ...)` puts the endpoint at
  `/mcp/<role>/`. Clients get the trailing slash; without it every message pays a
  307.
* Both `session_manager.run()` contexts MUST be entered in OUR lifespan.
  `streamable_http_app()` builds the manager without starting it, and mounting
  into our app replaces FastMCP's own lifespan. Forgetting this is a 500 on the
  first request, every time.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount

from tc.broker.client import Broker
from tc.config import Settings
from tc.mcp import tools_read, tools_research
from tc.mcp.registry import ROLE_TOOLS
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store


@dataclass
class McpDeps:
    """Everything a tool may reach. Nothing here can place an order: `broker`
    is the read-only Phase 0a protocol and there is no order module imported."""

    store: Store
    broker: Broker
    docs: DocStore
    rules: Rules
    settings: Settings
    clock: Callable[[], datetime]
    account_hash: Callable[[], str | None]


def build_servers(deps: McpDeps) -> dict[str, FastMCP]:
    servers: dict[str, FastMCP] = {}
    for role in ROLE_TOOLS:
        server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
        tools_read.register(server, deps, role)
        if role == "research":
            tools_research.register(server, deps)
        servers[role] = server
    return servers


class RoleAuthMiddleware(BaseHTTPMiddleware):
    """Bearer -> role, then role vs mount prefix. Everything outside /mcp is
    untouched: /health must keep answering an unauthenticated probe."""

    def __init__(self, app: Starlette, tokens: dict[str, str]) -> None:
        super().__init__(app)
        self._tokens = tokens

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not path.startswith("/mcp/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer"}, status_code=401)
        role = self._tokens.get(auth.split(" ", 1)[1].strip())
        if role is None:
            return JSONResponse({"error": "bad token"}, status_code=403)
        wanted = path.strip("/").split("/")[1]
        if wanted != role:
            return JSONResponse(
                {"error": f"token role {role} may not use /{wanted}"}, status_code=403
            )
        request.scope["mcp_role"] = role
        return await call_next(request)


def mcp_routes(servers: dict[str, FastMCP]) -> list[Mount]:
    return [Mount(f"/mcp/{role}", app=s.streamable_http_app()) for role, s in servers.items()]


@contextlib.asynccontextmanager
async def mcp_lifespan(servers: dict[str, FastMCP]) -> AsyncIterator[None]:
    async with contextlib.AsyncExitStack() as stack:
        for server in servers.values():
            await stack.enter_async_context(server.session_manager.run())
        yield
```

In `engine/tc/http/app.py`:

```python
@dataclass
class McpMounts:
    servers: dict[str, FastMCP]
    tokens: dict[str, str]          # bearer -> role


def build_app(state: EngineState, *, mcp: McpMounts | None = None) -> Starlette:
    ...
    routes = [ ... existing Routes ... ]
    middleware: list[Middleware] = []
    lifespan = None
    if mcp is not None:
        routes.extend(mcp_routes(mcp.servers))
        middleware.append(Middleware(RoleAuthMiddleware, tokens=mcp.tokens))

        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with mcp_lifespan(mcp.servers):
                yield

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
```

`main.py` builds `McpMounts` from `settings.mcp_tokens()` and passes it when the
mapping is non-empty; with no tokens configured the app is exactly what Phase 0b
served, so an engine without a runner still boots.

- [ ] **Step 5: Add the consistency check**

In `engine/tc/rules/consistency.py`:

```python
def check_tool_registry(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    """Spec §9/§10: no MCP role may expose a tool matching place|cancel|replace|order.

    The bash checker could not see this at all -- there was no registry to see.
    It is checked here rather than only in the contract test because a rule that
    only a test enforces is a rule the build does not.
    """
    from tc.mcp.registry import ROLE_TOOLS, forbidden_tools

    findings = [
        Finding(check="tool-registry", path="engine/tc/mcp/registry.py",
                message=f"role {role!r} exposes order-shaped tool {name!r}")
        for role, name in forbidden_tools()
    ]
    return findings, sum(len(v) for v in ROLE_TOOLS.values())
```

Register it in `CHECKS` as `("tool registry has no order-shaped tool", check_tool_registry, True)`.
Add to `tests/engine/unit/test_consistency.py`:

```python
def test_tool_registry_check_trips_on_an_order_shaped_name(monkeypatch, repo_root):
    import tc.mcp.registry as reg

    monkeypatch.setitem(reg.ROLE_TOOLS, "research", ("quotes", "cancel_order"))
    findings, _ = check_tool_registry(repo_root, RULES)
    assert [f.message for f in findings] == [
        "role 'research' exposes order-shaped tool 'cancel_order'"
    ]


def test_tool_registry_check_passes_as_shipped(repo_root):
    findings, count = check_tool_registry(repo_root, RULES)
    assert findings == [] and count > 0
```

- [ ] **Step 6: Run everything, then the gate; commit**

Tasks 8 and 9 supply `tools_read.register` and `tools_research.register`; until
they land, register stub bodies that raise `NotImplementedError` **but declare the
right names**, so this task's list-tools and contract tests are meaningful on
their own. Task 8 and 9 replace the bodies, not the names.

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/mcp/ engine/tc/http/app.py engine/tc/rules/consistency.py engine/tc/main.py tests/engine/
git commit -m "engine: two MCP roles with disjoint registries, and a contract test that no role can name an order"
```

---

### Task 8: Read tools

**Files:**
- Create: `engine/tc/mcp/tools_read.py`
- Test: `tests/engine/unit/test_tools_read.py`

**Interfaces:**
- Consumes: `McpDeps`, `Broker` (Task 2), `Store`, `Rules`.
- Produces `def register(server: FastMCP, deps: McpDeps, role: str) -> None`, and
  these tools. Every parameter is **flat and typed** with
  `Annotated[..., Field(...)]`; every return is a pydantic model, because a
  `-> dict` return yields `structuredContent=None` and the model then has to
  parse prose (`0c-sdk-facts.md` §3.4).

| tool | signature | returns |
|---|---|---|
| `get_datetime` | `()` | `Now(date: str, time_et: str, iso_utc: str, tz: str)` |
| `market_hours` | `(date: str)` | `Hours(date, is_trading_day: bool, rth_start_et: str \| None, rth_end_et: str \| None)` |
| `quotes` | `(symbols: list[str])` | `Quotes(quotes: list[QuoteOut], missing: list[str])` |
| `price_history` | `(symbol: str, days: int)` | `Bars(symbol, bars: list[BarOut])` |
| `option_chain` | `(symbol, from_date, to_date, strike_count, contract_type)` | `OptionChainView` (Task 2) |
| `expiration_chain` | `(symbol: str)` | `Expirations(symbol, expirations: list[Expiration])` |
| `instruments` | `(query: str, projection: str)` | `Instruments(instruments: list[Instrument])` |
| `movers` | `(index: str, direction: str)` | `Movers(movers: list[Mover])` |
| `status_latest` | `()` | `StatusLatest(session_status, last_tick)` |
| `rules` | `()` | `RulesOut(manual: dict[str,str], strategy: dict[str,str], source: str)` |
| `book` | `()` — **decide role only** | `Book(read_at, account_value, settled_cash, unsettled_cash, positions, stops, restricted)` |

`QuoteOut` is deliberately compact — `symbol, last, bid, ask, quote_time,
description, week52_high, volume, optionable` — because the verbose payload is
what the two-tier universe design exists to keep out of context, and a research
pass needs the price and the timestamp, not the fundamentals block.

`get_datetime` is the tool every prompt calls first: every command file resolves
its own Eastern date from the broker and never from the machine clock, because
the laptop runs Pacific. `Now.tz` is always `"America/New_York"` and `time_et` is
`HH:MM:SS`.

`market_hours` returns the RTH window **even on a closed or after-hours read**.
`MarketWindow.from_payload` reports `is_trading_day=False` unless `isOpen` and an
RTH block are both present, and `tick.md` §B1's standing warning is that `isOpen`
reads `true` at 23:20 ET — so the tool reports the window and the trading-day
flag as two separate facts and lets the caller decide, rather than collapsing
them.

`status_latest` replaces `scripts/latest-status.sh` and its `--hwm` awk: it
returns the latest `session_status` row (hwm, halt, drawdown_pct, level, close
value, date) and the latest tick row, as fields. The v2 script existed only
because `status/` is gitignored and Glob returns nothing under an ignored path;
that failure mode is gone with `/data`, and the numbers now arrive typed instead
of scraped out of a markdown block with a money regex.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_tools_read.py`. Call the registered functions
directly through a tiny helper rather than over HTTP — the wire is Task 7's
subject, the answers are this task's:

```python
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from tc.mcp import tools_read

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)   # 14:00 ET


def tool(server, name):
    """The callable behind a registered FastMCP tool."""
    return server._tool_manager.get_tool(name).fn


async def test_get_datetime_is_eastern(read_server):
    out = await tool(read_server, "get_datetime")()
    assert out.date == "2026-09-07" and out.time_et == "14:00:00"
    assert out.tz == "America/New_York"


async def test_market_hours_reports_the_window_even_when_closed(read_server):
    out = await tool(read_server, "market_hours")(date="2026-09-07")
    assert out.date == "2026-09-07"
    assert out.rth_start_et is not None and out.rth_end_et is not None


async def test_quotes_are_compact_and_name_the_symbols_that_did_not_answer(read_server):
    out = await tool(read_server, "quotes")(symbols=["AMH", "NOSUCHSYM"])
    assert [q.symbol for q in out.quotes] == ["AMH"]
    assert out.missing == ["NOSUCHSYM"]
    assert not hasattr(out.quotes[0], "avg10_days_volume")   # verbose stays out of context


async def test_option_chain_passes_the_window_through(read_server):
    out = await tool(read_server, "option_chain")(
        symbol="CSX", from_date="2026-10-01", to_date="2026-11-01",
        strike_count=6, contract_type="CALL",
    )
    assert out.underlying_price == D("48.99") and len(out.contracts) == 2


async def test_rules_returns_rules_yml_as_strings(read_server):
    out = await tool(read_server, "rules")()
    assert out.manual["option_min_dte"] == "18"
    assert out.strategy["scout_entry_window_min_days"] == "21"


async def test_status_latest_reports_the_mark_as_a_field(read_server, seeded_store):
    out = await tool(read_server, "status_latest")()
    assert out.session_status.hwm == "3800.00"
    assert out.session_status.level == "OK"


async def test_status_latest_before_any_close_says_so(read_server):
    out = await tool(read_server, "status_latest")()
    assert out.session_status is None and out.last_tick is None


async def test_book_is_absent_from_the_research_role(read_server, decide_server):
    assert read_server._tool_manager.get_tool("book") is None
    assert decide_server._tool_manager.get_tool("book") is not None


async def test_book_reports_positions_and_their_stops(decide_server, reconciled_store):
    out = await tool(decide_server, "book")()
    assert out.positions and out.stops
    assert out.restricted is False


async def test_a_broker_failure_is_a_tool_error_not_a_crash(read_server_unauthorized):
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await tool(read_server_unauthorized, "quotes")(symbols=["AMH"])
```

- [ ] **Step 2: Run to verify it fails; Step 3: implement `tools_read.py`**

Implementation shape — every tool body follows this pattern, so one is written
out and the rest are stated by the table above:

```python
def register(server: FastMCP, deps: McpDeps, role: str) -> None:
    @server.tool(name="get_datetime", description="The current Eastern date and time.")
    async def get_datetime() -> Now:
        now_et = deps.clock().astimezone(ET)
        return Now(
            date=now_et.date().isoformat(),
            time_et=now_et.strftime("%H:%M:%S"),
            iso_utc=deps.clock().astimezone(UTC).isoformat(),
            tz="America/New_York",
        )

    @server.tool(name="quotes", description="Compact quotes for up to 50 symbols.")
    async def quotes(
        symbols: Annotated[list[str], Field(min_length=1, max_length=50)],
    ) -> Quotes:
        try:
            got = await deps.broker.quotes(symbols)
        except BrokerError as e:
            # ToolError is the ANTICIPATED failure: the model sees the message
            # and can act on it, and the server logs at INFO with no traceback.
            # Any other exception is a crash and shows the model nothing useful.
            raise ToolError(f"quote read failed: {type(e).__name__}") from e
        return Quotes(
            quotes=[QuoteOut.of(q) for q in got.values()],
            missing=[s for s in symbols if s not in got],
        )
    ...
    if role == "decide":
        @server.tool(name="book", description="Positions, stops and cash from the last reconcile.")
        async def book() -> Book: ...
```

Rules for every body: catch `BrokerError`/`BrokerUnauthorized` and re-raise as
`ToolError` with the **class name only** (an exception message can carry a URL,
a token fragment or an account number); never return a bare `dict`; keep
`symbols` capped so one call cannot pull an unbounded payload into context.

- [ ] **Step 4: Run the tests; gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/mcp/tools_read.py tests/engine/unit/test_tools_read.py
git commit -m "mcp: the read tools answer in typed fields, and the verbose payload never reaches context"
```

---

### Task 9: Research tools

**Files:**
- Create: `engine/tc/mcp/tools_research.py`
- Test: `tests/engine/unit/test_tools_research.py`

**Interfaces:**
- Consumes: `tc.research.docs`, `tc.research.ledgers`, `tc.research.cohort`, `Store`.
- Produces `def register(server: FastMCP, deps: McpDeps) -> None` registering
  exactly `RESEARCH_TOOLS` from Task 7's registry:

| tool | signature | notes |
|---|---|---|
| `evidence_append` | `(symbol, date, record: dict)` | §1.4 validation |
| `escalation_raise` | `(symbol, date, record: dict)` | §1.5 bar and id |
| `escalation_score` | `(escalation_id, outcome, date)` | append-only |
| `sector_write` | `(rows: list[SectorRow], date)` | batch is the only form |
| `ledger_append` | `(name, date, record: dict)` | `screen\|iv\|oi\|tombstones\|events` |
| `tombstone` | `(symbol, date, gate, reason, ref_price, hypo_qty=None, hypo_stop=None)` | typed front door |
| `doc_replace` | `(kind, body, date=None, expect_last_pass=None)` | CAS on `candidates` |
| `doc_read` | `(kind, date=None)` | surfaces `last_pass` / `verified_as_of` as fields |
| `evidence_read` | `(symbol)` | the delta baseline the scout reads before searching |
| `escalations_read` | `(symbol=None)` | last score per id already reduced |
| `ledger_read` | `(name, date=None, latest_before=None)` | the OI prior-day diff, screen ingestion |
| `sectors_read` | `()` | `/catalyst` §A.2's existence gate becomes an empty list |
| `cohort` | `(date)` | Task 6 |
| `universe_symbols` | `()` | replaces the `sed\|grep\|tail\|cut` pipeline |
| `universe_names_page` | `(offset, limit)` | replaces the chunked `Read` of universe-names.tsv |
| `alert_read` | `()` | replaces `Read ALERT.md`, a precondition of every research command |

Error mapping, once, at the top of the module: `LedgerError`,
`DocValidationError` and `DocCasMismatch` become `ToolError` with the message
intact — these are *anticipated* refusals the model must read and act on, and
`ToolError` is exactly the shape that reaches the model as text with
`isError=True` and no server traceback. Anything else is a crash and stays one.

`sector_write` takes `rows: list[SectorRow]` where `SectorRow(symbol: str,
sector: str)`. There is no single-row form: v2 needed one because a ~3,000-name
universe could not be tagged one Bash call at a time and the permission gate
accepted exactly one multi-line shape, and having two forms is what gave it two
different validation orderings. One row is `len(rows) == 1`.

`alert_read` returns `Alert(exists: bool, acknowledged: bool, body: str)` from
the engine's `alerts` table (Phase 0b `Store.open_alerts`), not from a file at
the repo root. An unacknowledged alert still means closing-only posture; it is
now a row with an `acked_at` column rather than a file whose absence and whose
emptiness look the same.

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/unit/test_tools_research.py`:

```python
from datetime import date

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tc.mcp.registry import RESEARCH_TOOLS


def tool(server, name):
    return server._tool_manager.get_tool(name).fn


async def test_every_declared_research_tool_is_registered(research_server):
    for name in RESEARCH_TOOLS:
        assert research_server._tool_manager.get_tool(name) is not None, name


async def test_evidence_append_refusal_reaches_the_model_as_a_tool_error(research_server):
    with pytest.raises(ToolError) as e:
        await tool(research_server, "evidence_append")(
            symbol="CSX", date="2026-09-07",
            record={"claim": "x", "url": "u", "source_type": "blog",
                    "observed": "2026-09-07", "independence": "i"},
        )
    assert "source_type" in str(e.value)          # the reason, not just "failed"


async def test_doc_replace_cas_mismatch_says_what_to_do(research_server, seeded_candidates):
    with pytest.raises(ToolError) as e:
        await tool(research_server, "doc_replace")(
            kind="candidates", body=seeded_candidates, expect_last_pass="Last pass: stale"
        )
    assert "retry once" in str(e.value)


async def test_doc_read_of_a_missing_document_is_not_an_error(research_server):
    out = await tool(research_server, "doc_read")(kind="scorecard")
    assert out.exists is False and out.body == ""


async def test_ledger_read_latest_before_returns_the_prior_day(research_server, two_iv_days):
    out = await tool(research_server, "ledger_read")(name="iv", latest_before="2026-09-07")
    assert [r["date"] for r in out.rows] == ["2026-09-03"]


async def test_oi_already_snapshotted_is_a_result_not_an_error(research_server):
    rec = {"symbol": "CSX", "t": "16:26:00"}
    first = await tool(research_server, "ledger_append")(name="oi", date="2026-09-07", record=rec)
    assert first.appended is True
    again = await tool(research_server, "ledger_append")(name="oi", date="2026-09-07", record=rec)
    assert again.appended is False and "already snapshotted" in again.reason


async def test_sector_write_takes_only_the_batch_form(research_server):
    out = await tool(research_server, "sector_write")(
        rows=[{"symbol": "CSX", "sector": "airlines-transport"}], date="2026-09-07"
    )
    assert out.written == 1 and out.new == 1


async def test_universe_symbols_and_names_page(research_server, swept_universe):
    syms = await tool(research_server, "universe_symbols")()
    assert syms.symbols[:2] == ["CSX", "MPC"]
    page = await tool(research_server, "universe_names_page")(offset=1, limit=1)
    assert [r.symbol for r in page.rows] == ["MPC"]
    assert page.total == 2


async def test_cohort_is_empty_and_that_is_a_result(research_server):
    out = await tool(research_server, "cohort")(date="2026-09-07")
    assert out.rows == []


async def test_alert_read_reports_an_unacked_alert(research_server, open_alert):
    out = await tool(research_server, "alert_read")()
    assert out.exists is True and out.acknowledged is False
```

- [ ] **Step 2: Run to verify it fails; Step 3: implement**

`tools_research.py` skeleton — every writer body is three lines, because the
validation lives in Task 5:

```python
"""The research role's tool surface. Bodies are thin on purpose: every rule
lives in tc/research/, where it can be tested without a server.

One mapping matters here and nowhere else -- which failures the model is meant
to READ. A LedgerError or a DocValidationError is an anticipated refusal with an
actionable reason ("source_type must be one of ...", "re-read, merge, retry
once"), and ToolError is the shape that delivers it: isError=True, the message
visible, no traceback in the log. Everything else is a crash, and a crash shows
the model only "Error executing tool <name>", which is correct -- it has nothing
to act on.
"""

def _guard(exc: Exception) -> ToolError:
    return ToolError(str(exc))


def register(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(name="evidence_append", description="Record one dated observation.")
    async def evidence_append_tool(
        symbol: Annotated[str, Field(max_length=10)],
        date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
        record: dict[str, Any],
    ) -> Appended:
        try:
            out = await ledgers.evidence_append(deps.store, symbol, _d(date), record)
        except LedgerError as e:
            raise _guard(e) from e
        return Appended(appended=True, detail=out.get("symbol", ""))
    ...
```

Return models, all `extra="forbid"`: `Appended(appended: bool, reason: str | None
= None, detail: str = "")`, `Raised(id: str)`, `SectorWritten(written, new,
retired)`, `DocWrite`/`DocView` reused from Task 4, `Rows(rows: list[dict])`,
`Symbols(symbols: list[str])`, `NamesPage(rows: list[NameRow], total: int)`,
`CohortOut(rows: list[CohortRow], date: str)`, `Alert(exists, acknowledged, body)`.

- [ ] **Step 4: Run the tests; gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/mcp/tools_research.py tests/engine/unit/test_tools_research.py
git commit -m "mcp: the research role writes only through validators, and a refusal tells the model what to do"
```

---

### Task 10: The runner — one Claude job at a time, gated twice

**Files:**
- Create: `runner/pyproject.toml`, `runner/tc_runner/__init__.py`, `runner/tc_runner/app.py`
- Test: `tests/runner/test_app.py`, `tests/runner/test_gate.py`, `tests/runner/conftest.py`

**Interfaces:**
- Produces:
  - `RunRequest(job, prompt, allowed_tools: list[str], mcp_role: Literal["research","decide"], mcp_role_token: str, output_schema: dict, max_turns: int = 40, timeout_s: float = 1500.0, agent: str | None = None)`
  - `RunResult(verdict_raw: dict | None, result_text: str | None, is_error: bool, subtype: str | None, num_turns: int, permission_denials: list[dict], usage: dict, duration_s: float, timed_out: bool)`
  - `make_gate(allowed: list[str]) -> HookCallback`
  - `tool_allowed(name: str, allowed: Sequence[str]) -> bool`
  - `build_options(req: RunRequest) -> ClaudeAgentOptions`
  - `app: Starlette` with `POST /run` (bearer `TC_RUNNER_TOKEN`) and `GET /health`
  - Module attribute `QUERY = claude_agent_sdk.query` — **the test seam.** Tests
    replace it; no test ever launches a real CLI.

Facts this is built on, all verified in `0c-sdk-facts.md`:

- **`cli_path` must be explicit.** The SDK bundles CLI 2.1.259 in the wheel and
  `_find_cli()` prefers it over `PATH`, so without `cli_path` the Dockerfile's
  2.1.234 pin buys nothing (§1.9).
- **A `PreToolUse` hook outranks `allowed_tools` AND `bypassPermissions`;
  `can_use_tool` does not** — it is only consulted when the CLI's own rules say
  "ask", so an allowlist silently shadows it. The gate is the hook (§1.3).
- **Deny shape**: `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", "permissionDecisionReason": "..."}}`; `{}` abstains.
  Denials land on `ResultMessage.permission_denials` (§1.3).
- **`setting_sources=["project"]` is required** to load `CLAUDE.md` and to
  discover `.claude/agents/*.md`; `None` resolves to `["user","project"]`, which
  would pull in the host's user settings (§1.2).
- **File-based agents are selected with `extra_args={"agent": name}`** — there is
  no first-class option, and `options.agents` is for programmatic definitions (§1.5).
- **Structured output** is `output_format={"type":"json_schema","schema": …}` →
  `ResultMessage.structured_output` (§1.6).
- **`is_error=True` can coexist with `subtype="success"`** — test `is_error` (§1.7).
  An error result is *yielded* and then `query()` raises, so the loop is wrapped.
- **`options.env` overlays, never replaces**, the process environment; `HOME` is
  load-bearing when no token is set (§2).
- **A cancelled run takes real time to reap the child** — a 6 s deadline measured
  11.5 s wall clock, which is why the engine adds `runner.slack_s` on its side (§1.8).

`permission_mode` stays `"default"`. `bypassPermissions` is not used anywhere:
the allowlist plus the hook is the gate, and the hook was verified to hold even
under bypass, so using bypass would only remove a second layer for no gain.

- [ ] **Step 1: Write `runner/pyproject.toml`**

```toml
[project]
name = "tc-runner"
version = "0.1.0"
description = "trade-challenge v3 Claude runner"
requires-python = ">=3.12"
dependencies = [
  # Pinned as ONE unit with Claude Code CLI 2.1.234 in docker/Dockerfile: the
  # SDK tracks the CLI closely and `cli_path` pins the CLI, so bumping either
  # alone is how you get a flag the other does not have.
  "claude-agent-sdk==0.2.152",
  "pydantic>=2.10,<3",
  "starlette>=0.40",
  "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.28", "mypy>=1.10", "ruff>=0.5"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["tc_runner*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "ASYNC", "S", "RUF"]
ignore = ["S101"]

[tool.ruff.lint.per-file-ignores]
"../tests/runner/**/*.py" = ["E501"]

[tool.mypy]
strict = true
files = ["tc_runner", "../tests/runner"]
python_version = "3.12"
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["../tests/runner"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write the failing gate tests**

Create `tests/runner/test_gate.py`:

```python
import pytest

from tc_runner.app import DENY_ALWAYS, make_gate, tool_allowed

ALLOWED = ["mcp__engine__quotes", "mcp__engine__doc_read", "WebSearch", "Read"]


@pytest.mark.parametrize(
    "name, ok",
    [
        ("mcp__engine__quotes", True),
        ("WebSearch", True),
        ("Read", True),
        ("mcp__engine__doc_replace", False),   # a real tool, not on THIS job's list
        ("Bash", False),
        ("Write", False),
        ("Glob", False),
    ],
)
def test_tool_allowed_is_exact(name, ok):
    assert tool_allowed(name, ALLOWED) is ok


def test_wildcard_entry_matches_the_server_prefix_only():
    allowed = ["mcp__engine__*"]
    assert tool_allowed("mcp__engine__quotes", allowed) is True
    assert tool_allowed("mcp__other__quotes", allowed) is False
    assert tool_allowed("Bash", allowed) is False


@pytest.mark.parametrize("name", DENY_ALWAYS)
async def test_always_denied_even_when_a_job_lists_them(name):
    gate = make_gate([*ALLOWED, name])         # a mistake in a job spec
    out = await gate({"tool_name": name, "tool_input": {}}, "tu_1", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert name in out["hookSpecificOutput"]["permissionDecisionReason"]


async def test_allowed_tool_gets_no_opinion():
    gate = make_gate(ALLOWED)
    assert await gate({"tool_name": "WebSearch", "tool_input": {}}, "tu_1", None) == {}


async def test_unlisted_tool_is_denied_with_the_reason_the_model_sees():
    gate = make_gate(ALLOWED)
    out = await gate({"tool_name": "mcp__engine__doc_replace", "tool_input": {}}, "tu_1", None)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "not on this job's allowlist" in hso["permissionDecisionReason"]
```

Create `tests/runner/test_app.py`:

```python
import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

import tc_runner.app as appmod

BODY = {
    "job": "scout",
    "prompt": "run the scout pass",
    "allowed_tools": ["mcp__engine__cohort", "WebSearch"],
    "mcp_role": "research",
    "mcp_role_token": "test-token-not-real",
    "output_schema": {"type": "object", "properties": {"cohort": {"type": "integer"}},
                      "required": ["cohort"], "additionalProperties": False},
    "max_turns": 5,
    "timeout_s": 5.0,
    "agent": "scout",
}
AUTH = {"Authorization": "Bearer runner-token-test"}


@dataclass
class FakeResult:
    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 3
    result: str | None = '{"cohort": 4}'
    structured_output: Any = field(default_factory=lambda: {"cohort": 4})
    permission_denials: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = field(default_factory=lambda: {"input_tokens": 10})
    duration_ms: int = 1200


def fake_query(seq, *, delay: float = 0.0, captured: dict | None = None):
    async def _q(*, prompt, options, **kw):
        if captured is not None:
            captured["prompt"] = prompt
            captured["options"] = options
        for msg in seq:
            if delay:
                await asyncio.sleep(delay)
            yield msg
    return _q


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "RUNNER_TOKEN", "runner-token-test")
    monkeypatch.setattr(appmod, "OAUTH_TOKEN", "test-token-not-real")
    monkeypatch.setattr(appmod, "ENGINE_URL", "http://127.0.0.1:8080")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=appmod.app), base_url="http://runner"
    )


async def test_health_is_open(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["busy"] is False


async def test_run_requires_the_bearer(client):
    assert (await client.post("/run", json=BODY)).status_code == 401
    r = await client.post("/run", json=BODY, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


async def test_run_returns_the_structured_verdict(client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    r = await client.post("/run", json=BODY, headers=AUTH)
    assert r.status_code == 200
    out = r.json()
    assert out["verdict_raw"] == {"cohort": 4}
    assert out["is_error"] is False and out["timed_out"] is False
    assert out["duration_s"] > 0


async def test_options_carry_the_pins_the_gate_depends_on(client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], captured=captured))
    await client.post("/run", json=BODY, headers=AUTH)
    o = captured["options"]
    assert o.cli_path == appmod.CLI_PATH               # never the wheel's bundled CLI
    assert o.setting_sources == ["project"]            # CLAUDE.md and .claude/agents
    assert o.cwd == appmod.REPO_DIR
    assert o.permission_mode == "default"              # bypassPermissions is never used
    assert o.strict_mcp_config is True
    assert set(appmod.DENY_ALWAYS) <= set(o.disallowed_tools)
    assert o.allowed_tools == BODY["allowed_tools"]
    assert o.output_format == {"type": "json_schema", "schema": BODY["output_schema"]}
    assert o.extra_args == {"agent": "scout"}
    assert o.env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-token-not-real"
    assert o.mcp_servers["engine"]["url"] == "http://127.0.0.1:8080/mcp/research/"
    assert o.mcp_servers["engine"]["headers"]["Authorization"] == "Bearer test-token-not-real"


async def test_a_missing_structured_output_is_reported_not_invented(client, monkeypatch):
    monkeypatch.setattr(
        appmod, "QUERY",
        fake_query([FakeResult(structured_output=None, result="I could not finish")]),
    )
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["verdict_raw"] is None
    assert out["result_text"] == "I could not finish"


async def test_is_error_with_subtype_success_is_still_an_error(client, monkeypatch):
    monkeypatch.setattr(
        appmod, "QUERY", fake_query([FakeResult(is_error=True, subtype="success")])
    )
    assert (await client.post("/run", json=BODY, headers=AUTH)).json()["is_error"] is True


async def test_a_query_that_raises_after_yielding_is_still_reported(client, monkeypatch):
    async def _q(*, prompt, options, **kw):
        yield FakeResult(is_error=True, subtype="error_max_turns", structured_output=None)
        raise RuntimeError("ResultError: exit code 1")

    monkeypatch.setattr(appmod, "QUERY", _q)
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["is_error"] is True and out["subtype"] == "error_max_turns"


async def test_timeout_is_a_result_not_an_exception(client, monkeypatch):
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.5))
    out = (await client.post("/run", json={**BODY, "timeout_s": 0.05}, headers=AUTH)).json()
    assert out["timed_out"] is True and out["is_error"] is True
    assert out["verdict_raw"] is None


async def test_only_one_run_at_a_time(client, monkeypatch):
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult()], delay=0.3))
    first = asyncio.create_task(client.post("/run", json=BODY, headers=AUTH))
    await asyncio.sleep(0.05)
    second = await client.post("/run", json=BODY, headers=AUTH)
    assert second.status_code == 409
    assert (await first).status_code == 200


async def test_permission_denials_are_relayed_verbatim(client, monkeypatch):
    denial = {"tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {"command": "ls"}}
    monkeypatch.setattr(appmod, "QUERY", fake_query([FakeResult(permission_denials=[denial])]))
    out = (await client.post("/run", json=BODY, headers=AUTH)).json()
    assert out["permission_denials"] == [denial]


async def test_body_rejects_unknown_fields(client):
    r = await client.post("/run", json={**BODY, "sudo": True}, headers=AUTH)
    assert r.status_code == 422
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd runner && pytest -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc_runner'`.

- [ ] **Step 4: Implement `runner/tc_runner/app.py`**

```python
"""The Claude runner: one job at a time, and nothing else in the container.

It holds CLAUDE_CODE_OAUTH_TOKEN and a per-role MCP bearer handed to it by the
engine. It has no Schwab credential, no database, and no Bash. If it is OOM-
killed mid-research it takes a research run with it and nothing else -- which is
the whole reason it is a second cgroup (spec §3).

Read-only is enforced TWICE, both times in Python (spec §4):

1. `allowed_tools` on the options, which is what the CLI auto-approves.
2. A PreToolUse hook that denies anything outside that list and denies Bash,
   Write, Edit, NotebookEdit and the agent-spawning tools unconditionally.

Two is not redundant. A hook outranks BOTH `allowed_tools` and
`bypassPermissions` -- verified by execution -- while `can_use_tool` does not,
because the CLI only consults it when its own rules evaluate to "ask", so an
allowlist silently shadows it. The hook is the gate; the allowlist is the
convenience.

There is no bash script path for Claude to be refused on, so the v2 failure
where the container's permission gate silently refused every `scripts/*.sh`
call with no approver present has nothing left to refuse.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query
from pydantic import BaseModel, ConfigDict, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# The SDK bundles its own CLI (2.1.259 in 0.2.152) and `_find_cli()` prefers it
# over PATH. Without an explicit cli_path the Dockerfile's 2.1.234 pin and its
# DISABLE_AUTOUPDATER buy exactly nothing for SDK-driven runs.
CLI_PATH = os.environ.get("TC_CLAUDE_CLI", "/usr/local/bin/claude")
REPO_DIR = os.environ.get("TC_REPO_DIR", "/app/repo")
ENGINE_URL = os.environ.get("TC_ENGINE_URL", "http://127.0.0.1:8080").rstrip("/")
RUNNER_TOKEN = os.environ.get("TC_RUNNER_TOKEN", "")
OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

# Denied for every job, whatever a job spec says. Bash/Write/Edit/NotebookEdit
# are the write surface the design removes; Task/Agent would let a job spawn a
# child whose allowlist this hook never sees.
DENY_ALWAYS: tuple[str, ...] = (
    "Bash", "BashOutput", "KillShell", "Write", "Edit", "NotebookEdit", "Task", "Agent",
)

# The test seam. Nothing in the suite launches a real CLI.
QUERY = query

_lock = asyncio.Lock()


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job: str
    prompt: str
    allowed_tools: list[str]
    mcp_role: Literal["research", "decide"]
    mcp_role_token: str
    output_schema: dict[str, Any]
    max_turns: int = Field(default=40, ge=1, le=200)
    timeout_s: float = Field(default=1500.0, gt=0, le=7200)
    agent: str | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict_raw: dict[str, Any] | None
    result_text: str | None
    is_error: bool
    subtype: str | None
    num_turns: int
    permission_denials: list[dict[str, Any]]
    usage: dict[str, Any]
    duration_s: float
    timed_out: bool


def tool_allowed(name: str, allowed: Sequence[str]) -> bool:
    if name in DENY_ALWAYS:
        return False
    for entry in allowed:
        if entry == name:
            return True
        if entry.endswith("*") and name.startswith(entry[:-1]):
            return True
    return False


def make_gate(allowed: Sequence[str]):  # -> HookCallback
    """The deny shape is exact and was verified live: permissionDecision "deny"
    inside hookSpecificOutput, with a reason the model reads. An empty dict is
    "no opinion" and falls through -- returning "allow" here would override
    decisions this gate has no business making."""

    async def gate(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        name = str(input_data.get("tool_name", ""))
        if name in DENY_ALWAYS:
            reason = f"{name} is denied for every engine job: this runner has no write surface."
        elif not tool_allowed(name, allowed):
            reason = (
                f"{name} is not on this job's allowlist. Use one of: {', '.join(allowed)}."
            )
        else:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return gate


def build_options(req: RunRequest) -> ClaudeAgentOptions:
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": OAUTH_TOKEN,
        "DISABLE_AUTOUPDATER": "1",
    }
    return ClaudeAgentOptions(
        cli_path=CLI_PATH,
        env=env,
        cwd=REPO_DIR,
        # "project" is REQUIRED: it is what loads CLAUDE.md and discovers
        # .claude/agents/*.md. The default (None) resolves to
        # ["user","project"], which would also pull in the host account's
        # settings -- not something an unattended run should inherit.
        setting_sources=["project"],
        allowed_tools=list(req.allowed_tools),
        disallowed_tools=list(DENY_ALWAYS),
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_gate(req.allowed_tools)])]},
        mcp_servers={
            "engine": {
                "type": "http",
                # Trailing slash on purpose: /mcp/<role> without it answers a
                # 307 on every message.
                "url": f"{ENGINE_URL}/mcp/{req.mcp_role}/",
                "headers": {"Authorization": f"Bearer {req.mcp_role_token}"},
            }
        },
        # Ignore .mcp.json, user settings and plugin servers: the engine's
        # server list must be exactly this one.
        strict_mcp_config=True,
        output_format={"type": "json_schema", "schema": req.output_schema},
        permission_mode="default",
        max_turns=req.max_turns,
        extra_args={"agent": req.agent} if req.agent else {},
    )


async def _consume(req: RunRequest) -> tuple[Any | None, bool]:
    """Drain the message stream, keeping the LAST ResultMessage.

    An error result is yielded and THEN query() raises, so a bare `async for`
    without this guard loses the very message that says what went wrong.
    """
    last: Any | None = None
    raised = False
    try:
        async for msg in QUERY(prompt=req.prompt, options=build_options(req)):
            if hasattr(msg, "is_error"):
                last = msg
    except Exception:
        raised = True
    return last, raised


async def run(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing bearer"}, status_code=401)
    if auth.split(" ", 1)[1].strip() != RUNNER_TOKEN or not RUNNER_TOKEN:
        return JSONResponse({"error": "bad token"}, status_code=403)
    try:
        req = RunRequest.model_validate(await request.json())
    except Exception as e:
        return JSONResponse({"error": f"bad request: {type(e).__name__}"}, status_code=422)

    if _lock.locked():
        # One run at a time is the memory contract (spec §3). A queue would
        # turn a slow job into a backlog of jobs whose windows have passed.
        return JSONResponse({"error": "runner busy"}, status_code=409)

    async with _lock:
        started = time.monotonic()
        timed_out = False
        try:
            last, raised = await asyncio.wait_for(_consume(req), timeout=req.timeout_s)
        except TimeoutError:
            last, raised, timed_out = None, True, True
        duration = time.monotonic() - started

    structured = getattr(last, "structured_output", None) if last is not None else None
    result = RunResult(
        verdict_raw=structured if isinstance(structured, dict) else None,
        result_text=getattr(last, "result", None) if last is not None else None,
        # is_error is the test, never subtype: is_error=True coexists with
        # subtype="success" for an API-level failure.
        is_error=bool(getattr(last, "is_error", True)) or raised or last is None,
        subtype=getattr(last, "subtype", None) if last is not None else None,
        num_turns=int(getattr(last, "num_turns", 0) or 0) if last is not None else 0,
        permission_denials=list(getattr(last, "permission_denials", None) or []),
        usage=dict(getattr(last, "usage", None) or {}),
        duration_s=duration,
        timed_out=timed_out,
    )
    return JSONResponse(result.model_dump())


async def health(request: Request) -> Response:
    return JSONResponse(
        {"ok": bool(OAUTH_TOKEN) and bool(RUNNER_TOKEN), "busy": _lock.locked(),
         "cli_path": CLI_PATH, "engine_url": ENGINE_URL}
    )


app = Starlette(routes=[Route("/run", run, methods=["POST"]), Route("/health", health)])
```

- [ ] **Step 5: Run the tests, then the runner gate**

Run: `cd runner && pytest -q && mypy && ruff check . ../tests/runner`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add runner/ tests/runner/
git commit -m "runner: one job at a time, gated by a hook that outranks the allowlist, and a timeout that is a result"
```

---

### Task 11: The runner container and its compose service

**Files:**
- Create: `docker/Dockerfile.runner`
- Modify: `docker/docker-compose.yml`, `host/README.md`
- Test: `tests/runner/test_dockerfile.py` (a text assertion, plus the local build below)

**Interfaces:** none in Python. What later tasks rely on: the runner answers on
`http://127.0.0.1:8090` on the host's loopback, and `/usr/local/bin/claude` is
2.1.234 inside it.

`Dockerfile.runner`:

```dockerfile
# The runner: claude-agent-sdk plus the pinned CLI, and nothing else.
#
# FROM the existing toolchain image on purpose. That image already carries the
# pinned `claude` 2.1.234 with DISABLE_AUTOUPDATER=1, the trust seed for the
# repo mount, and the unprivileged `trader` user at UID 1000 -- all of which the
# runner needs and none of which is worth reproducing in a second Dockerfile
# that would then drift from the first.
FROM ghcr.io/kilowhisky/trade-challenge:latest

USER root
# A dedicated venv so the SDK cannot collide with the image's uv-managed tools.
RUN uv venv --python 3.12 /opt/runner
COPY runner/pyproject.toml /srv/runner/pyproject.toml
COPY runner/tc_runner /srv/runner/tc_runner
RUN /opt/runner/bin/pip install --no-cache-dir /srv/runner \
    && chmod -R a+rX /opt/runner /srv/runner

USER trader
ENV PATH=/opt/runner/bin:$PATH \
    TC_CLAUDE_CLI=/usr/local/bin/claude \
    TC_REPO_DIR=/app/repo
WORKDIR /srv/runner
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=3).status==200 else 1)"
CMD ["uvicorn", "tc_runner.app:app", "--host", "127.0.0.1", "--port", "8090"]
```

Compose service, added beside `engine`:

```yaml
  # --- the Claude runner: one job at a time, its own memory fence -----------
  # A separate cgroup is the only reliable memory fence Docker offers on this
  # Pi, and `claude` is the one memory-hungry process on the box (~330MB peak
  # over a 17-hour window). An OOM here costs a research run; an OOM in the
  # engine would cost the token holder, a pending approval and the stop loop.
  # oom_score_adj 800 > the engine's 300 makes the kernel pick this one first.
  runner:
    profiles: ["engine"]
    build: {context: .., dockerfile: docker/Dockerfile.runner}
    image: ${TC_RUNNER_IMAGE:-ghcr.io/kilowhisky/trade-challenge-runner:latest}
    container_name: tc-runner
    restart: unless-stopped
    environment:
      TZ: America/New_York
      HOME: /home/trader
      # 127.0.0.1 is the ENGINE, reachable because both use host networking.
      TC_ENGINE_URL: http://127.0.0.1:8080
    env_file: [/srv/tc/.env]        # CLAUDE_CODE_OAUTH_TOKEN, TC_RUNNER_TOKEN
    volumes:
      # Read-only, and it is the ONLY filesystem the runner can see: prompts,
      # CLAUDE.md, strategy.md, rules.yml. No /data, no token, no database.
      - ..:/app/repo:ro
    network_mode: host
    mem_limit: 1g
    oom_score_adj: 800
```

`/srv/tc/.env` gains `CLAUDE_CODE_OAUTH_TOKEN`, `TC_RUNNER_TOKEN`,
`TC_MCP_RESEARCH_TOKEN`, `TC_MCP_DECIDE_TOKEN` — document all four in
`host/README.md`, with the note that the two MCP tokens are read by the **engine**
(to build the token→role map) and handed to the runner per request, never put in
the runner's environment.

- [ ] **Step 1: Write the failing text test**

`tests/runner/test_dockerfile.py`:

```python
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "Dockerfile.runner"
COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"


def test_runner_gets_no_data_mount_and_no_schwab_credential():
    body = COMPOSE.read_text()
    block = body.split("  runner:", 1)[1].split("\n  ", 1)[0] if "  runner:" in body else ""
    assert "runner:" in body
    runner = body[body.index("  runner:"):]
    runner = runner[: runner.index("\nvolumes:")] if "\nvolumes:" in runner else runner
    assert "/data" not in runner            # no database, no token, no store
    assert "SCHWAB" not in runner           # no broker credential, ever
    assert "..:/app/repo:ro" in runner      # the repo, read-only, and only that


def test_runner_is_built_on_the_pinned_toolchain_image():
    body = DOCKERFILE.read_text()
    assert body.splitlines()[0].startswith("#")
    assert "FROM ghcr.io/kilowhisky/trade-challenge:latest" in body
    assert "TC_CLAUDE_CLI=/usr/local/bin/claude" in body


def test_runner_binds_loopback_only():
    assert '"--host", "127.0.0.1"' in DOCKERFILE.read_text()
```

- [ ] **Step 2: Run, expect failure. Step 3: write the files. Step 4: run again.**

- [ ] **Step 5: Build locally if docker is present**

```bash
docker build -f docker/Dockerfile.runner -t tc-runner:dev .
docker run --rm tc-runner:dev python -c "import tc_runner.app; print('ok')"
docker run --rm tc-runner:dev claude --version   # must print 2.1.234
```

The `claude --version` check is the one that matters: it is the assertion that
`cli_path` points at the pin rather than at a wheel-bundled 2.1.259.

- [ ] **Step 6: Gate; commit**

```bash
cd runner && pytest -q && mypy && ruff check . ../tests/runner
git add docker/Dockerfile.runner docker/docker-compose.yml host/README.md tests/runner/test_dockerfile.py
git commit -m "runner: a container with the repo read-only and nothing else, on its own memory fence"
```

---

### Task 12: Job specs and verdict models

**Files:**
- Create: `engine/tc/jobs/__init__.py`, `engine/tc/jobs/spec.py`
- Test: `tests/engine/unit/test_jobs_spec.py`

**Interfaces:**
- Consumes: `tc.mcp.registry.ROLE_TOOLS`.
- Produces:
  - Verdict models (pydantic, `extra="forbid"`):

```python
class Escalation(BaseModel):
    symbol: str
    claim: str
    evidence_ids: list[str] = []

class HotFresh(BaseModel):
    symbol: str
    sleeve: Literal["core", "catalyst", "option"]
    ref: str                    # "48.99@2026-09-07T14:03:11Z" -- price AND its timestamp
    thesis: str

class ScoutVerdict(BaseModel):
    cohort: int; observed: int; escalations: list[Escalation]; summary: str

class CatalystVerdict(BaseModel):
    scanned: int; observed: int; escalations: list[Escalation]; summary: str

class DeepVerdict(BaseModel):
    kind: Literal["preopen", "postclose"]
    wrote: list[str]            # document kinds written this run
    hot_fresh: list[HotFresh]   # postclose only
    notes: str
    summary: str

class ResearchVerdict(BaseModel):
    hot: int; watch: int; tomb: int
    hot_fresh: list[HotFresh]
    standing_stale: bool
    summary: str

class SectorVerdict(BaseModel):
    names: int; tagged: int; new: int; retired: int; cohort: int; summary: str
```

  - `def output_schema(model: type[BaseModel]) -> dict[str, Any]` — `model_json_schema()`,
    which already emits `additionalProperties: false` because every model forbids extras.
  - `JobSpec` frozen dataclass: `name, agent, command, prompt, allowed_tools: tuple[str, ...], verdict: type[BaseModel], max_turns: int, timeout_s: float, window: tuple[time, time], role: str, noop_when: Callable[[BaseModel], bool] | None`
  - `JOB_SPECS: dict[str, JobSpec]` for `scout`, `catalyst`, `preopen`, `postclose`, `research`, `sector_tag`.
  - `def tools_for(*names: str) -> tuple[str, ...]` — prefixes each with `mcp__engine__`
    after asserting it is in `ROLE_TOOLS["research"]`, so a typo is a startup
    failure rather than a tool the model silently cannot call.

Every `summary` field is deliberate: the v2 return line ("`PASS 14:44 | HOT 2 |
WATCH 5 …`") was the human-readable half of the contract, and it survives *inside*
the JSON rather than beside it. Nothing greps stdout any more (spec §4), but the
one line Chris reads in Discord still exists.

Budgets and windows come from the v2 crontab and `scheduled-run.sh`
(`0c-writers-contract.md` §2.0), unchanged:

| job | agent | schedule (ET) | window | timeout | role |
|---|---|---|---|---|---|
| `scout` | `scout` | 07:12 weekdays | 07:00–08:00 | 2400 s | research |
| `catalyst` | `catalyst` | 18:33 weekdays | 18:00–19:30 | 2400 s | research |
| `preopen` | `deep-research` | 08:17 weekdays | 08:00–09:15 | 3600 s | research |
| `postclose` | `deep-research` | 16:22 weekdays | 16:15–18:00 | 3600 s | research |
| `research` | `research-scout` | hourly :57, 09:57–14:57 weekdays | 09:45–15:15 | 1500 s | research |
| `sector_tag` | `sector-tagger` | 09:40 sat | 09:00–13:00 | 3000 s | research |

Prompts are Python strings passed as `prompt=`, never interpolated into a shell
word — which is what removes the `--allowedTools` and `$900` classes by
construction. Each is two or three sentences naming the command file to follow,
the mode where there is one, and the instruction to return the JSON object
matching the schema. The command file carries the method.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from tc.jobs.spec import JOB_SPECS, DeepVerdict, ScoutVerdict, output_schema, tools_for
from tc.mcp.registry import ROLE_TOOLS


def test_every_spec_names_a_real_agent_file(repo_root):
    for spec in JOB_SPECS.values():
        assert (repo_root / ".claude" / "agents" / f"{spec.agent}.md").exists(), spec.name


def test_every_allowed_tool_is_reachable_by_that_role():
    for spec in JOB_SPECS.values():
        for tool in spec.allowed_tools:
            if tool.startswith("mcp__engine__"):
                assert tool.removeprefix("mcp__engine__") in ROLE_TOOLS[spec.role], tool
            else:
                assert tool in {"WebSearch", "WebFetch", "Read"}, tool


def test_no_job_is_allowed_a_write_surface_tool():
    banned = {"Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Task", "Agent"}
    for spec in JOB_SPECS.values():
        assert not (set(spec.allowed_tools) & banned), spec.name


def test_schema_forbids_extra_keys_so_a_stray_field_fails_the_verdict():
    schema = output_schema(ScoutVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {"cohort", "observed", "escalations", "summary"}


def test_tools_for_rejects_a_tool_no_role_has():
    with pytest.raises(KeyError):
        tools_for("doc_delete")


def test_noop_predicates_fire_only_on_a_genuinely_empty_pass():
    scout = JOB_SPECS["scout"]
    assert scout.noop_when(ScoutVerdict(cohort=0, observed=0, escalations=[], summary="")) is True
    assert scout.noop_when(ScoutVerdict(cohort=4, observed=0, escalations=[], summary="")) is False


def test_deep_verdict_accepts_a_postclose_with_no_hot_fresh():
    v = DeepVerdict(kind="postclose", wrote=["scorecard"], hot_fresh=[], notes="", summary="ok")
    assert v.hot_fresh == []
```

- [ ] **Step 2: Run, expect failure. Step 3: implement `spec.py`.**

`noop_when` predicates, and why each is where it is:

- `scout`: `cohort == 0`. An empty cohort is a correct answer between earnings
  seasons and before the first weekly sweep, and it is `noop`, not `done` —
  because `done` on sixty consecutive empty passes is exactly the "every job
  green, nothing ever happens" failure the design is built against, and a `noop`
  streak is what the §7 expectations check can see.
- `catalyst`: `scanned == 0` (no sectors tagged yet).
- `preopen`/`postclose`: `wrote == []`.
- `research` and `sector_tag`: `None` — zero HOT and zero new tags are ordinary
  results of a pass that did its work.

- [ ] **Step 4: Gate; commit**

```bash
git add engine/tc/jobs/ tests/engine/unit/test_jobs_spec.py
git commit -m "jobs: allowlists, budgets and verdicts are typed data, and a typo in a tool name fails at startup"
```

---

### Task 13: Dispatch — the runner client, verdict classification and the relays

**Files:**
- Create: `engine/tc/jobs/dispatch.py`
- Modify: `engine/tc/main.py`, `config.yml`, `engine/tc/rules/consistency.py`
- Test: `tests/engine/unit/test_jobs_dispatch.py`, `tests/engine/unit/test_main.py`, `tests/engine/unit/test_consistency.py`

**Interfaces:**
- Consumes: `JOB_SPECS`, `Settings.runner`, `Settings.runner_token`,
  `Settings.mcp_research_token`, `Notifier`, `Store.record_job_run`.
- Produces:
  - `class RunnerClient(base_url: str, token: str | None, client: httpx.AsyncClient, slack_s: float)`
    - `async health() -> bool`
    - `async run(spec: JobSpec, *, prompt_extra: str = "") -> RunnerReply`
  - `RunnerReply(result: RunResultView | None, transport_error: str | None, busy: bool)`
  - `class JobRunner(runner: RunnerClient, notifier: Notifier, clock)`
    - `async execute(job: str, now: datetime) -> tuple[Verdict, dict[str, Any]]`
  - `def classify(spec, reply) -> tuple[Verdict, BaseModel | None, dict[str, Any]]`

Classification, exhaustive and in this order:

| condition | verdict | detail |
|---|---|---|
| no runner configured (`url` unset or no token) | `noop` | `{"skipped": "no runner configured"}` |
| outside the spec's ET window | `noop` | `{"skipped": "outside window", "window": ...}` |
| HTTP 409 | `noop` | `{"skipped": "runner busy"}` |
| transport error / non-2xx | `failed` | `{"error": "<class name or status>"}` |
| `result.timed_out` | `timeout` | `{"duration_s": ...}` |
| `result.is_error` | `failed` | `{"subtype": ..., "denials": n}` |
| `verdict_raw is None` | `content_failed` | `{"reason": "no structured output", "text": first 200 chars}` |
| `verdict_raw` fails the spec's model | `content_failed` | `{"reason": "verdict did not validate", "errors": [...]}` |
| `noop_when(verdict)` true | `noop` | the verdict, dumped |
| otherwise | `done` | the verdict, dumped |

`content_failed` existing at all is the point: v2 recorded exit-0-with-no-content
as `{"verdict":"ok"}`, so a research run that produced nothing pinged green.
Here it pings `/fail`, and the §7 expectation `job_verdict_not: content_failed`
already watches for it.

Relays to Discord through the existing `Notifier` (shadow-prefixed in Phase 0):

- one `🔥 HOT-FRESH: <symbol> sleeve=<sleeve> ref=<ref> — <thesis>` per
  `hot_fresh` entry (`ResearchVerdict`, `DeepVerdict`);
- one `📌 ESCALATE: <symbol> — <claim>` per `escalations` entry
  (`ScoutVerdict`, `CatalystVerdict`);
- one `⚠️ <job> <verdict>: <reason>` on `content_failed`, `failed` and `timeout`;
- the verdict's `summary` line on `done` for the deep runs and `sector_tag`.

This closes the two live relay gaps the contract document found: v2's line-1
whitelist did not include `PASS`, `SCOUT`, `CATALYST` or `CLOSE`, and
`weekly-universe` was expected to emit a `UNIVERSE …` line that no file ever
defined. There is no line-matching whitelist here — there are fields.

Engine wiring in `main.py`:

```python
CLAUDE_JOBS: tuple[str, ...] = (
    "scout", "catalyst", "preopen", "postclose", "research", "sector_tag",
)
JOBS: tuple[str, ...] = (
    "tick", "session_close", "token_check", "expectations", "backup",
    "weekly_universe", *CLAUDE_JOBS,
)
```

`_execute` gains, before the `raise ValueError`:

```python
        if job in CLAUDE_JOBS:
            return await self._jobs.execute(job, now)
```

`build_engine` constructs `JobRunner(RunnerClient(...), notifier, clock)` and
`Engine.start()` sets `state.runner_ok = await runner.health()` so `/health` and
the host probe (Phase 0b task 12, which already reads `runner_ok`) report it.

`config.yml` gains the seven schedule entries:

```yaml
  scout:            "at 07:12 weekdays"     # 07:00-08:00 window, before the open
  catalyst:         "at 18:33 weekdays"     # the off-season half of the research loop
  preopen:          "at 08:17 weekdays"     # deep-research preopen; file-only, no pings
  postclose:        "at 16:22 weekdays"     # deep-research postclose; owns the POST window
  research:         "every 60m 09:57-14:57 weekdays"
  sector_tag:       "at 09:40 sat"
  weekly_universe:  "at 07:40 sat"          # engine code, not a Claude job
```

- [ ] **Step 1: Write the failing dispatch tests**

```python
from datetime import UTC, datetime

import httpx
import pytest

from tc.jobs.dispatch import JobRunner, RunnerClient
from tc.jobs.spec import JOB_SPECS

IN_WINDOW = datetime(2026, 9, 7, 11, 15, tzinfo=UTC)   # 07:15 ET, inside scout's window
OUT_WINDOW = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)   # 16:00 ET


def _runner(handler, token="runner-token-test"):
    return RunnerClient(
        "http://runner", token, httpx.AsyncClient(transport=httpx.MockTransport(handler)), 120.0
    )


def _ok(payload):
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return h


GOOD = {"verdict_raw": {"cohort": 4, "observed": 3, "escalations": [], "summary": "SCOUT ok"},
        "result_text": "{}", "is_error": False, "subtype": "success", "num_turns": 6,
        "permission_denials": [], "usage": {}, "duration_s": 12.0, "timed_out": False}


async def test_done_with_a_valid_verdict(notifier):
    jr = JobRunner(_runner(_ok(GOOD)), notifier, lambda: IN_WINDOW)
    verdict, detail = await jr.execute("scout", IN_WINDOW)
    assert verdict == "done" and detail["observed"] == 3


async def test_outside_the_window_is_a_noop_and_never_calls_the_runner(notifier):
    called = []

    def h(req):
        called.append(req)
        return httpx.Response(200, json=GOOD)

    jr = JobRunner(_runner(h), notifier, lambda: OUT_WINDOW)
    verdict, detail = await jr.execute("scout", OUT_WINDOW)
    assert verdict == "noop" and detail["skipped"] == "outside window"
    assert called == []


async def test_empty_cohort_is_noop_not_done(notifier):
    payload = {**GOOD, "verdict_raw": {"cohort": 0, "observed": 0, "escalations": [],
                                       "summary": "SCOUT cohort 0"}}
    jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
    assert (await jr.execute("scout", IN_WINDOW))[0] == "noop"


async def test_no_structured_output_is_content_failed(notifier):
    payload = {**GOOD, "verdict_raw": None, "result_text": "I ran out of turns"}
    jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
    verdict, detail = await jr.execute("scout", IN_WINDOW)
    assert verdict == "content_failed"
    assert "ran out of turns" in detail["text"]


async def test_a_verdict_with_the_wrong_shape_is_content_failed(notifier):
    payload = {**GOOD, "verdict_raw": {"cohort": "four", "observed": 3, "escalations": [],
                                       "summary": "x"}}
    jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
    assert (await jr.execute("scout", IN_WINDOW))[0] == "content_failed"


async def test_a_verdict_with_an_extra_key_is_content_failed(notifier):
    payload = {**GOOD, "verdict_raw": {**GOOD["verdict_raw"], "orders_placed": 1}}
    jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
    assert (await jr.execute("scout", IN_WINDOW))[0] == "content_failed"


async def test_timeout_and_is_error_are_distinct_verdicts(notifier):
    for payload, want in (
        ({**GOOD, "timed_out": True, "is_error": True, "verdict_raw": None}, "timeout"),
        ({**GOOD, "is_error": True, "verdict_raw": None}, "failed"),
    ):
        jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
        assert (await jr.execute("scout", IN_WINDOW))[0] == want


async def test_busy_runner_is_a_noop(notifier):
    jr = JobRunner(_runner(lambda r: httpx.Response(409, json={"error": "runner busy"})),
                   notifier, lambda: IN_WINDOW)
    verdict, detail = await jr.execute("scout", IN_WINDOW)
    assert verdict == "noop" and detail["skipped"] == "runner busy"


async def test_transport_failure_is_failed_and_names_only_the_class(notifier):
    def h(req):
        raise httpx.ConnectError("connection refused to 10.0.0.5:8090")

    jr = JobRunner(_runner(h), notifier, lambda: IN_WINDOW)
    verdict, detail = await jr.execute("scout", IN_WINDOW)
    assert verdict == "failed" and detail["error"] == "ConnectError"
    assert "10.0.0.5" not in repr(detail)


async def test_no_runner_configured_is_a_noop_not_a_failure(notifier):
    jr = JobRunner(_runner(_ok(GOOD), token=None), notifier, lambda: IN_WINDOW)
    assert (await jr.execute("scout", IN_WINDOW))[1]["skipped"] == "no runner configured"


async def test_escalations_and_hot_fresh_are_relayed_one_line_each(notifier):
    payload = {**GOOD, "verdict_raw": {
        "cohort": 4, "observed": 3, "summary": "SCOUT 1 escalation",
        "escalations": [{"symbol": "CSX", "claim": "queue times doubled", "evidence_ids": ["e1"]}]}}
    jr = JobRunner(_runner(_ok(payload)), notifier, lambda: IN_WINDOW)
    await jr.execute("scout", IN_WINDOW)
    assert any("ESCALATE: CSX" in m for m in notifier.posted)


async def test_the_read_timeout_is_the_job_budget_plus_slack(notifier):
    seen = {}

    def h(req):
        seen["timeout"] = req.extensions.get("timeout", {}).get("read")
        return httpx.Response(200, json=GOOD)

    jr = JobRunner(_runner(h), notifier, lambda: IN_WINDOW)
    await jr.execute("scout", IN_WINDOW)
    assert seen["timeout"] == JOB_SPECS["scout"].timeout_s + 120.0
```

Add a `notifier` fixture to `tests/engine/conftest.py` — a `Notifier` over a
`MockTransport` that appends every posted body to `notifier.posted`.

Add to `tests/engine/unit/test_main.py`:

```python
async def test_every_scheduled_job_is_a_known_job(settings_from_repo_config):
    from tc.main import JOBS
    assert set(settings_from_repo_config.schedule) <= set(JOBS)


async def test_claude_jobs_dispatch_through_the_job_runner(engine_with_fake_runner):
    verdict = await engine_with_fake_runner.run_job("scout")
    assert verdict in {"done", "noop"}
    rows = await engine_with_fake_runner._store.fetchall("SELECT job FROM job_runs")
    assert [r["job"] for r in rows] == ["scout"]
```

- [ ] **Step 2: Run, expect failure. Step 3: implement `dispatch.py` and wire `main.py`.**

`RunnerClient.run` posts `{job, prompt, allowed_tools, mcp_role, mcp_role_token,
output_schema, max_turns, timeout_s, agent}` with
`timeout=httpx.Timeout(spec.timeout_s + slack_s, connect=connect_timeout_s)` and
`headers={"Authorization": f"Bearer {token}"}`. It never raises: every
`httpx.HTTPError` becomes `RunnerReply(transport_error=type(e).__name__)`, and
only the class name is recorded — an exception message can carry a URL or a
token fragment.

- [ ] **Step 4: Add the schedule-vs-doc consistency check**

The bash checker's check 5 compared `docker/crontab` against the command files;
spec §9 says that becomes a `config.yml` concern here. In
`engine/tc/rules/consistency.py`:

```python
# job -> the command file that documents its time. A job whose schedule moves
# without its doc moving leaves every reader believing a time that has not been
# true for weeks -- the same drift class as check 1.
SCHEDULE_DOCS: dict[str, str] = {
    "scout": ".claude/commands/scout.md",
    "catalyst": ".claude/commands/catalyst.md",
    "preopen": ".claude/commands/deep-research.md",
    "postclose": ".claude/commands/deep-research.md",
    "research": ".claude/commands/research.md",
    "sector_tag": ".claude/commands/sector-tag.md",
}
SCHEDULED_LINE = re.compile(r"^Scheduled:.*$", re.MULTILINE)


def check_schedule_docs(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    """Every scheduled Claude job's ET time appears on a `Scheduled:` line in
    the command file that documents it."""
    cfg = yaml.safe_load((root / "config.yml").read_text()) or {}
    schedule = cfg.get("schedule") or {}
    findings: list[Finding] = []
    checked = 0
    for job, doc in SCHEDULE_DOCS.items():
        spec = schedule.get(job)
        if spec is None:
            findings.append(Finding(check="schedule-docs", path="config.yml",
                                    message=f"no schedule entry for {job!r}"))
            continue
        times = re.findall(r"\d\d:\d\d", spec)
        body = (root / doc).read_text()
        lines = SCHEDULED_LINE.findall(body)
        if not lines:
            findings.append(Finding(check="schedule-docs", path=doc,
                                    message=f"no 'Scheduled:' line documenting {job!r}"))
            continue
        checked += 1
        if not any(t in " ".join(lines) for t in times):
            findings.append(Finding(
                check="schedule-docs", path=doc,
                message=f"config.yml runs {job!r} at {times} but {doc} documents {lines}"))
    return findings, checked
```

Register it in `CHECKS`. **`scripts/check-consistency.sh` check 5 must keep
passing** while the old stack runs beside the new one: it greps
`.claude/commands/research.md` for the literal strings `hourly at :57` and
`hours 9-14`, and it greps `.claude/commands/tick.md` for `**15 min** baseline`.
So Task 16 keeps research.md's `§Scheduled` lines verbatim, and Task 17's tick.md
tombstone must **not** contain `**15 min** baseline` — with the string gone the
bash check skips the cadence comparison instead of failing it.

- [ ] **Step 5: Gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/jobs/dispatch.py engine/tc/main.py engine/tc/rules/consistency.py config.yml tests/engine/
git commit -m "jobs: a run with no structured verdict is content_failed, and an empty cohort is a noop the deadman can see"
```

---

### Task 14: The weekly universe becomes engine code

**Files:**
- Create: `engine/tc/loops/universe.py`
- Modify: `engine/tc/main.py` (`_job_weekly_universe`)
- Test: `tests/engine/unit/test_universe.py`
- Fixtures: `tests/engine/fixtures/nasdaq/{nasdaqtraded-sample.txt,nasdaqtraded-decoy.html}`

**Interfaces:**
- Consumes: `Broker.quotes_verbose`, `Rules`, `Store.replace_universe`, `DocStore.replace`.
- Produces:
  - `NASDAQ_URL`, `SANITY_FLOOR = 1000`, `CHUNK = 150`, `MAJOR_EXCHANGES = frozenset("NQAPZ")`, `NAME_EXCLUSIONS: tuple[str, ...]`
  - `class DirectoryUnavailable(RuntimeError)`
  - `async fetch_symbols(client: httpx.AsyncClient, url: str = NASDAQ_URL) -> list[str]`
  - `parse_directory(text: str) -> list[str]`
  - `UniverseRow(symbol, price, adv10, dollar_vol, pct_from_52wk_high, optionable, leverage, last_earnings, is_etf, session_range_pct: Decimal | None, description, qualified)`
  - `Counts(fetched, quoted, qualified, stub_filtered, nodata, ranked, dropped, skipped: list[str], chunks, chunks_failed)`
  - `filter_universe(quotes: dict[str, VerboseQuote], rules: Rules, rank_top: int) -> tuple[list[UniverseRow], Counts]`
  - `render_universe_md(rows, counts, asof: datetime) -> str`
  - `async run_weekly_universe(*, broker, store, docs, rules, client, now) -> Counts`

**`parse_directory`** ports `universe-fetch.sh` exactly: pipe-delimited
`csv.DictReader`; keep only `Listing Exchange` in `{N, Q, A, P, Z}`;
`Test Issue == "N"`; drop an empty symbol, a symbol containing `$`, or one longer
than 5 characters; drop a security whose lower-cased `Security Name` contains any
of `" warrant"`, `"% note"`, `" right"`, `" unit"`, `"preferred"`, `" depositary"`,
`"when issued"`, `" due 20"`; return `sorted(set(kept))`.

**`fetch_symbols`** raises `DirectoryUnavailable` on a non-2xx, an empty body, or
a parsed count below `SANITY_FLOOR`, and **writes nothing** in that case. The
real directory yields ~11,227 symbols; a WAF or decoy page parses to zero rows
and would otherwise install an empty universe and exit 0. A false trip is safe by
design: the caller keeps last week's universe.

**`filter_universe`** ports `universe-filter.sh`'s five gates in this exact
order, all thresholds from `rules.yml`:

1. `price < manual.min_share_price_usd` → drop.
2. `adv10 × price < manual.min_avg_daily_dollar_volume` → drop.
3. `adv10 < manual.min_avg_daily_volume` → drop.
4. `fund_leverage_factor not in (0, 100)` → drop. **Rejecting on `!= 0` would
   discard every ETF** — roughly half the directory — because a plain 1x fund
   reports 100.
5. Takeover-stub gate: `session_range_pct is not None and < strategy.min_session_range_pct`
   → drop. **The no-data branch runs first by construction:** `rng is None` means
   "no high/low data", not "0.00% pinned", and those rows are counted in `nodata`
   and **kept**. SPY reports `highPrice: 0, lowPrice: 0` alongside 34M shares of
   volume and would otherwise be false-rejected.

A symbol whose `price` is `None` is appended to `Counts.skipped` and skipped —
never silently, and never treated as zero.

Ranking: `gap = (week52_high − price) / week52_high × 100` when `week52_high`
is non-zero, else `999.0`; sort key `(gap, −dollar_vol)`; truncate to `rank_top`,
which defaults to `strategy.working_universe_size` and is never a literal.

**What the port deletes.** The v2 two-tier split — `universe-qualified.tsv` for
the scout, a ranked `universe.md` for the daily run, and the
`--emit-qualified-set`-only-on-the-weekly-sweep rule that existed to stop the
daily run overwriting a 3,196-name file with 500 of its own names — is one
`qualified` column here. Every qualifying name is a row; `qualified = 1` is the
scout tier; the ranked top N is `ORDER BY gap LIMIT n`. The rule that flag
protected is now unbreakable rather than documented, and `cohort` reads the
qualified set directly.

`render_universe_md` composes the header block and the fenced ten-column table
**server-side** and passes it to `doc_replace(kind="universe")` — replacing the
`{ printf …; cat /tmp/universe-ranked.tsv; … } | research-replace.sh universe`
compound whose whole purpose was keeping a 500-row table out of context. The
table never enters context here either, because no model is in this loop at all.

Chunking stays at 150 symbols per `quotes_verbose` call. It is no longer needed
to force the harness to spill a payload to a file, but it is still the batch size
Schwab answers reliably, and a chunk that errors is counted in
`Counts.chunks_failed` and skipped rather than aborting the sweep.

- [ ] **Step 1: Write the fixtures**

`nasdaqtraded-sample.txt` — the real header plus ~12 rows covering every filter:
a clean NASDAQ common stock, a clean NYSE one, an ETF on `P`, a test issue
(`Test Issue = Y`), a warrant, a unit, a right, a preferred, a `% note`, a
`due 20xx` bond, a `$`-bearing symbol, a six-character symbol, and the
`File Creation Time` trailer line the file really ends with.
`nasdaqtraded-decoy.html` — an HTML block-page body, which must parse to zero
rows.

- [ ] **Step 2: Write the failing tests**

```python
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from tc.loops.universe import (
    SANITY_FLOOR, DirectoryUnavailable, Counts, fetch_symbols, filter_universe,
    parse_directory, render_universe_md, run_weekly_universe,
)
from tc.rules.model import Rules

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "nasdaq"
RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")


def test_directory_filter_drops_every_non_tradeable_class():
    kept = parse_directory((FIX / "nasdaqtraded-sample.txt").read_text())
    assert kept == sorted(kept)
    assert "MPC" in kept and "CSX" in kept
    for bad in ("TESTQ", "WARRW", "UNITU", "RIGHTR", "PREFA", "TOOLONGX", "BRK$A"):
        assert bad not in kept


def test_a_decoy_page_parses_to_nothing():
    assert parse_directory((FIX / "nasdaqtraded-decoy.html").read_text()) == []


async def test_fetch_below_the_sanity_floor_raises_and_writes_nothing():
    body = (FIX / "nasdaqtraded-sample.txt").read_text()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    with pytest.raises(DirectoryUnavailable) as e:
        await fetch_symbols(client)
    assert str(SANITY_FLOOR) in str(e.value)


async def test_fetch_propagates_an_http_failure_as_directory_unavailable():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(DirectoryUnavailable):
        await fetch_symbols(client)


def test_gates_in_order(verbose_quotes):
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=500)
    kept = {r.symbol for r in rows}
    assert "MPC" in kept                      # clean stock
    assert "SPY" in kept                      # 1x fund, and 0/0 high/low is NO DATA
    assert "TQQQ" not in kept                 # leverage 300 -> dropped
    assert "PENNY" not in kept                # under the price floor
    assert "THIN" not in kept                 # under the dollar-volume floor
    assert "STUB" not in kept                 # session range below the stub threshold
    assert counts.skipped == ["NOPRICE"]      # unquotable, named, never zeroed
    assert counts.nodata == 1                 # SPY: kept, and counted


def test_a_one_times_fund_survives_and_a_leveraged_one_does_not(verbose_quotes):
    rows, _ = filter_universe(verbose_quotes, RULES, rank_top=500)
    spy = next(r for r in rows if r.symbol == "SPY")
    assert spy.leverage == D("1.0")           # the MULTIPLE in the row, 100 in the payload
    assert spy.session_range_pct is None      # "-" in the rendered table


def test_ranking_is_gap_then_dollar_volume_and_truncates(verbose_quotes):
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=2)
    assert len(rows) == 2
    assert [r.symbol for r in rows] == sorted(
        (r.symbol for r in rows),
        key=lambda s: next(r.pct_from_52wk_high for r in rows if r.symbol == s),
    )
    assert counts.dropped == counts.qualified - counts.ranked


def test_rank_top_defaults_to_the_rules_value(verbose_quotes):
    _, counts = filter_universe(verbose_quotes, RULES, rank_top=0)   # 0 = no truncation
    assert counts.ranked == counts.qualified


def test_rendered_document_passes_the_universe_validator(verbose_quotes):
    rows, counts = filter_universe(verbose_quotes, RULES, rank_top=500)
    body = render_universe_md(rows, counts, datetime(2026, 9, 12, 11, 56, tzinfo=UTC))
    assert body.splitlines()[0] == "# Fallback universe"
    assert "never a source for order parameters" in body
    assert "the §4 tilts are not applied here" in body
    assert "symbol\tprice\tadv10" in body
    assert body.count("```") == 2


async def test_the_sweep_writes_the_table_and_the_document(sweep_ctx):
    counts = await run_weekly_universe(**sweep_ctx)
    rows = await sweep_ctx["store"].universe_rows()
    assert {r["symbol"] for r in rows} and counts.qualified == len(rows)
    assert (await sweep_ctx["docs"].read("universe")).exists is True


async def test_an_unreachable_directory_keeps_last_weeks_universe(sweep_ctx_unreachable):
    before = await sweep_ctx_unreachable["store"].universe_asof()
    with pytest.raises(DirectoryUnavailable):
        await run_weekly_universe(**sweep_ctx_unreachable)
    assert await sweep_ctx_unreachable["store"].universe_asof() == before
```

- [ ] **Step 3: Run, expect failure. Step 4: implement `universe.py`.**

Module docstring to write:

```python
"""The weekly whole-market sweep, as code.

This was a Claude job -- `/weekly-universe` -- and it is the clearest case in
the system of a judgement call where none was needed (spec §2.2). Everything it
did was mechanical: fetch a directory, filter it by five numeric gates, quote
what survived in batches, rank by distance from the 52-week high, and write a
table. What made it a model's job was that the quote payloads were too large to
put in context, so the design put them in files the model was forbidden to read
and had it orchestrate scripts over paths. Here nothing is in context because
nothing is a model.

Two properties from that design are kept because they were right:

* **Degrade to stale, never to empty.** Below the sanity floor, or on any HTTP
  failure, this raises and writes NOTHING -- last week's universe stands. A WAF
  page parses to zero rows, and an empty universe leaves the scout with no
  cohort for a week (which is exactly what the 2026-08-29 sweep did).
* **The no-data branch of the stub gate runs first.** `session_range_pct is
  None` means the high/low fields were absent, not that the name is pinned at
  0.00%: SPY reports 0/0 high/low with 34M shares of volume.

And one is deleted: the two-tier `--emit-qualified-set` flag, whose entire job
was stopping the daily run from overwriting the scout's 3,196-name file with 500
of its own names. That is a `qualified` column now, and the daily run does not
write this table at all.
"""
```

`main.py` gains:

```python
    async def _job_weekly_universe(self, now: datetime) -> tuple[Verdict, dict[str, Any]]:
        try:
            counts = await run_weekly_universe(
                broker=self._broker, store=self._store, docs=self._docs,
                rules=self._rules, client=self._client, now=now,
            )
        except DirectoryUnavailable as e:
            # Not a crash: the directory was unreadable and last week's universe
            # stands. It is `failed` so the deadman sees it, with the reason.
            await self.notifier.post(f"⚠️ weekly_universe: {e}")
            return "failed", {"error": "DirectoryUnavailable", "detail": str(e)}
        await self.notifier.post(
            f"🗺️ universe {counts.fetched} fetched / {counts.qualified} qualified"
            f" / {counts.ranked} ranked ({counts.dropped} dropped)"
        )
        return "done", counts.model_dump()
```

- [ ] **Step 5: Gate; commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/loops/universe.py engine/tc/main.py tests/engine/unit/test_universe.py tests/engine/fixtures/nasdaq/
git commit -m "engine: the weekly sweep is five numeric gates in Python, and a decoy page keeps last week's universe"
```

---

### Task 15: `tc import-research` — nothing in the store is lost

**Files:**
- Modify: `engine/tc/cli.py`
- Create: `engine/tc/research/importer.py`
- Test: `tests/engine/unit/test_import_research.py`
- Fixtures: `tests/engine/fixtures/store/` (a miniature, redacted copy of the live store)

**Interfaces:**
- Produces:
  - `ImportReport(documents: int, evidence: int, escalations: int, sectors: int, tombstones: int, screen: int, iv: int, oi: int, events: int, universe: int, skipped: list[str])`
  - `async import_research(store: Store, docs: DocStore, src: Path) -> ImportReport`
  - CLI `tc import-research --store-dir /data/store [--dry-run]`

What it reads out of the v2 store and where each lands:

| source | destination |
|---|---|
| `research/candidates.md`, `standing.md`, `scorecard.md`, `options-roster.md`, `universe.md` | `doc_replace(kind, body)` — through the validators, so a malformed legacy file is *reported*, not imported |
| `research/preopen/YYYY-MM-DD.md` | `doc_replace("preopen", body, d)` |
| `research/tombstones.jsonl` | `tombstones` |
| `research/screen/*.jsonl`, `iv/*.jsonl`, `oi/*.jsonl` | `screen_rows`, `iv_series`, `oi_snapshots` |
| `status/data/*-events.jsonl` | `events` |
| `research/evidence/*.jsonl` | `evidence` (absent in the live store today — the importer must handle that) |
| `research/escalations.jsonl` | `escalations`, raises and scores in file order |
| `research/sectors.tsv` | `sectors` |
| `research/universe.md`'s fenced TSV | the `universe` table, `asof` from the `Assembled:` line, `qualified = 1` |

Rules the importer holds to:

- **It writes through the same validators as the tools.** A legacy row that would
  be refused today is refused now, listed in `skipped` with the reason, and the
  import continues. Importing invalid rows to "not lose anything" would mean the
  first thing in the new store is data the new store's rules reject.
- **It is idempotent.** Re-running imports nothing new: documents are content-
  compared before a replace, `oi` is already unique per symbol per day, and an
  escalation raise that exists is skipped rather than refused.
- **A NUL byte in a legacy ledger line is a skip, not a crash** — docker's
  `json.log` put NUL bytes in a live JSONL ledger on 2026-08-31 and grep went
  binary-silent for four days. This is spec §10's replay item: rejected on read,
  named in `skipped`.
- `--dry-run` prints the report and writes nothing.

- [ ] **Step 1: Build the fixture store**

`tests/engine/fixtures/store/` — three or four files per kind, copied in shape
from the live samples in `0c-writers-contract.md` §3 with synthetic numbers, plus
one deliberately broken file of each class: a `candidates.md` missing its banner,
a `screen` line with no `src`, a JSONL line containing `\x00`, and an
`escalations.jsonl` score for an id that was never raised.

- [ ] **Step 2: Write the failing tests**

```python
async def test_documents_import_through_the_validators(store, docs, fixture_store):
    report = await import_research(store, docs, fixture_store)
    assert (await docs.read("candidates")).exists is True
    assert (await docs.read("standing")).verified_as_of is not None
    assert any("candidates-broken" in s for s in report.skipped)


async def test_every_ledger_lands_in_its_table(store, docs, fixture_store):
    report = await import_research(store, docs, fixture_store)
    assert report.screen > 0 and report.iv > 0 and report.oi > 0 and report.events > 0
    assert len(await store.ledger_rows("iv")) == report.iv


async def test_a_nul_byte_line_is_skipped_with_a_reason(store, docs, fixture_store):
    report = await import_research(store, docs, fixture_store)
    assert any("NUL" in s for s in report.skipped)


async def test_universe_md_becomes_rows_with_the_assembled_date(store, docs, fixture_store):
    await import_research(store, docs, fixture_store)
    assert await store.universe_asof() == date(2026, 8, 29)
    assert all(r["qualified"] for r in await store.universe_rows())


async def test_a_score_without_a_raise_is_skipped_not_orphaned(store, docs, fixture_store):
    report = await import_research(store, docs, fixture_store)
    assert any("no such raise" in s for s in report.skipped)


async def test_import_is_idempotent(store, docs, fixture_store):
    first = await import_research(store, docs, fixture_store)
    second = await import_research(store, docs, fixture_store)
    assert second.screen == 0 and second.oi == 0 and second.evidence == 0
    assert first.screen > 0


async def test_dry_run_writes_nothing(store, docs, fixture_store, capsys):
    from tc.cli import main
    rc = main(["--config", ..., "import-research", "--store-dir", str(fixture_store), "--dry-run"])
    assert rc == 0
    assert await store.ledger_rows("iv") == []
```

- [ ] **Step 3: Run, expect failure. Step 4: implement. Step 5: gate; commit.**

```bash
git add engine/tc/research/importer.py engine/tc/cli.py tests/engine/unit/test_import_research.py tests/engine/fixtures/store/
git commit -m "engine: the v2 store imports through the new validators, and what it refuses it names"
```

---

### Task 16: Rewrite the research prompts — `research` and `deep-research`

**Files:**
- Modify: `.claude/commands/research.md`, `.claude/agents/research-scout.md`,
  `.claude/commands/deep-research.md`, `.claude/agents/deep-research.md`
- Test: `tests/engine/unit/test_prompts.py`

**Interfaces:** no Python. What Task 13 relies on: each agent file's `name:`
matches the `JobSpec.agent` it is selected by (`--agent <name>`), and every tool
in its `tools:` frontmatter is in that job's `allowed_tools`.

**The mapping table, used by all four files and by Task 17.** Every left-hand
entry disappears from every prompt in this repo.

| v2 call | replacement |
|---|---|
| `scripts/research-write.sh --expect-last-pass '<line>'` | `mcp__engine__doc_replace(kind="candidates", body=…, expect_last_pass="<line>")` |
| `scripts/research-replace.sh roster` | `mcp__engine__doc_replace(kind="options-roster", body=…)` |
| `scripts/research-replace.sh preopen DATE` | `mcp__engine__doc_replace(kind="preopen", date="DATE", body=…)` |
| `scripts/research-replace.sh scorecard\|standing\|universe` | `mcp__engine__doc_replace(kind=…, body=…)` |
| `scripts/research-append.sh screen\|iv\|tombstones DATE '<json>'` | `mcp__engine__ledger_append(name=…, date=…, record=…)` |
| `scripts/oi-append.sh DATE '<json>'` | `mcp__engine__ledger_append(name="oi", …)`; `appended: false` = already snapshotted, skip |
| `scripts/data-append.sh events DATE '<json>'` | `mcp__engine__ledger_append(name="events", …)` |
| `scripts/evidence-append.sh SYM DATE '<json>'` | `mcp__engine__evidence_append(symbol=…, date=…, record=…)` |
| `scripts/escalation-log.sh raise SYM DATE '<json>'` | `mcp__engine__escalation_raise(...)` |
| `scripts/sector-write.sh [--batch]` | `mcp__engine__sector_write(rows=[{symbol, sector}, …], date=…)` |
| `scripts/cohort.sh DATE` | `mcp__engine__cohort(date=…)` |
| `scripts/latest-status.sh [--hwm]` | `mcp__engine__status_latest()` |
| `scripts/universe-fetch.sh`, `universe-filter.sh`, `check-consistency.sh` | **gone** — the sweep is engine code (Task 14) |
| `Read research/candidates.md\|standing.md\|scorecard.md\|options-roster.md\|universe.md\|preopen/DATE.md` | `mcp__engine__doc_read(kind=…[, date=…])` |
| `Read research/evidence/<SYM>.jsonl` | `mcp__engine__evidence_read(symbol=…)` |
| `Read research/sectors.tsv` | `mcp__engine__sectors_read()` |
| `Read research/screen\|iv\|oi/DATE.jsonl` | `mcp__engine__ledger_read(name=…, date=… \| latest_before=…)` |
| the `sed \| grep \| tail \| cut` universe pipeline | `mcp__engine__universe_symbols()` |
| chunked `Read research/universe-names.tsv` | `mcp__engine__universe_names_page(offset=…, limit=…)` |
| `Read ALERT.md` | `mcp__engine__alert_read()` |
| `Read status/ticks/DATE.tsv` last row, sixth column | `mcp__engine__status_latest().last_tick.level` |
| every `mcp__schwab__get_*` | the `mcp__engine__` read tool of the same job (Task 8) |
| the "invoke every repo script as a BARE RELATIVE PATH" paragraph | **delete** — there are no scripts to invoke |
| the "record the returned payload path; do not read the file" paragraph | **delete** — no payload reaches a prompt |

**What must not change in any file:** the §-numbered structure and every
methodology section. The qualification checklists, the §B-opt IV/HV bar, the
six source types, the four-part escalation bar, the scorecard's forward-marking
rules, the SEC EDGAR User-Agent requirement, the "empty is a correct answer"
paragraphs, and every "never" list stay word for word except where a sentence
names a script. **Do not shorten the research methodology.** This is a
transport change, not an editorial pass.

- [ ] **Step 1: Write the failing prompt tests**

`tests/engine/unit/test_prompts.py` — these run against the repo's own files and
are the regression net for both prompt tasks:

```python
import re
from pathlib import Path

import pytest
import yaml

from tc.jobs.spec import JOB_SPECS
from tc.mcp.registry import ROLE_TOOLS

ROOT = Path(__file__).resolve().parents[3]
LIVE_COMMANDS = ["research", "deep-research", "scout", "catalyst", "sector-tag"]
LIVE_AGENTS = ["research-scout", "deep-research", "scout", "catalyst", "sector-tagger"]
RETIRED = [
    ".claude/commands/weekly-universe.md", ".claude/commands/tick.md",
    ".claude/agents/weekly-universe.md", ".claude/agents/tick-watch.md",
    ".claude/agents/session-close.md", ".claude/agents/trader.md",
]


def frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1])


def live_files():
    return (
        [ROOT / ".claude" / "commands" / f"{n}.md" for n in LIVE_COMMANDS]
        + [ROOT / ".claude" / "agents" / f"{n}.md" for n in LIVE_AGENTS]
    )


@pytest.mark.parametrize("path", live_files(), ids=lambda p: p.name)
def test_no_live_prompt_mentions_a_script_or_a_schwab_tool(path):
    body = path.read_text()
    assert "scripts/" not in body, f"{path} still calls a shell script"
    assert "mcp__schwab__" not in body, f"{path} still names the old broker"
    assert "TC_RESEARCH_DIR" not in body


@pytest.mark.parametrize("name", LIVE_AGENTS)
def test_agent_tools_are_engine_tools_only(name):
    fm = frontmatter(ROOT / ".claude" / "agents" / f"{name}.md")
    tools = [t.strip() for t in fm["tools"].split(",")]
    banned = {"Bash", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Task", "Agent"}
    assert not (set(tools) & banned), f"{name}: {sorted(set(tools) & banned)}"
    for t in tools:
        if t.startswith("mcp__engine__"):
            assert t.removeprefix("mcp__engine__") in ROLE_TOOLS["research"], t
        else:
            assert t in {"Read", "WebSearch", "WebFetch"}, t


def test_every_job_spec_agent_exists_and_its_tools_are_a_subset():
    for spec in JOB_SPECS.values():
        fm = frontmatter(ROOT / ".claude" / "agents" / f"{spec.agent}.md")
        assert fm["name"] == spec.agent
        tools = {t.strip() for t in fm["tools"].split(",")}
        assert tools <= set(spec.allowed_tools), f"{spec.name}: {sorted(tools - set(spec.allowed_tools))}"


@pytest.mark.parametrize("name", LIVE_AGENTS)
def test_every_agent_states_its_verdict_contract(name):
    body = (ROOT / ".claude" / "agents" / f"{name}.md").read_text()
    assert "Return the JSON object matching" in body
    assert "summary" in body


@pytest.mark.parametrize("rel", RETIRED)
def test_retired_files_are_tombstones_that_point_at_the_engine(rel):
    body = (ROOT / rel).read_text()
    assert "RETIRED" in body.splitlines()[0] or "RETIRED" in body[:400]
    assert "scripts/" not in body
    assert re.search(r"engine|Plan 1|tc/loops|tc/jobs", body)


def test_tick_tombstone_does_not_carry_the_cadence_string_check_5_greps():
    # scripts/check-consistency.sh check 5 greps tick.md for "**15 min** baseline"
    # and compares it against the crontab. With the string gone it skips the
    # comparison; with the string present in a tombstone it would compare a
    # cadence no document owns any more.
    assert "**15 min** baseline" not in (ROOT / ".claude/commands/tick.md").read_text()


def test_research_md_keeps_the_schedule_strings_check_5_greps():
    body = (ROOT / ".claude/commands/research.md").read_text()
    assert "hourly at :57" in body and "hours 9-14" in body
```

- [ ] **Step 2: Run, expect failure** (every live file still names `scripts/`).

- [ ] **Step 3: `.claude/agents/research-scout.md`**

- [ ] Frontmatter `tools:` becomes exactly:
  `Read, WebSearch, WebFetch, mcp__engine__get_datetime, mcp__engine__market_hours, mcp__engine__quotes, mcp__engine__movers, mcp__engine__instruments, mcp__engine__option_chain, mcp__engine__expiration_chain, mcp__engine__price_history, mcp__engine__status_latest, mcp__engine__rules, mcp__engine__alert_read, mcp__engine__doc_read, mcp__engine__doc_replace, mcp__engine__ledger_read, mcp__engine__ledger_append`
- [ ] `model: opus` stays (v2 pinned the model in frontmatter because
  `scheduled-run.sh` passed no `--model`; the SDK passes none either).
- [ ] `description:` loses "via scripts/research-write.sh", gains "via
  `mcp__engine__doc_replace`".
- [ ] Delete the "resolve paths relative to the repo root" paragraph: the tools
  take kinds, not paths.
- [ ] Delete the bare-relative-path rule and the "no Write/Edit by construction,
  every write goes through a whitelisted script" sentence; replace with "no write
  surface exists: `Bash`, `Write` and `Edit` are denied by the runner's hook for
  every job, and the only writes are `doc_replace` and `ledger_append`."
- [ ] `Read` is retained **only** for repo files — `strategy.md`, `CLAUDE.md` —
  and the file says so.
- [ ] Return contract replaced with: "Return the JSON object matching the
  `ResearchVerdict` schema: `hot`, `watch`, `tomb`, `hot_fresh[]`,
  `standing_stale`, and `summary` — the one line a human reads, in the form
  `PASS <ET time> | HOT n | WATCH n | TOMB n | new: <symbols or ->`."

- [ ] **Step 4: `.claude/commands/research.md`**

- [ ] §A.1 cadence gate: "read `Last pass:` from `research/candidates.md`" →
  "`doc_read(kind='candidates').last_pass`"; keep the note that the scheduled
  server run does not apply the 45-minute gate.
- [ ] §A.2 halt gate: the "last row of `status/ticks/DATE.tsv`, sixth column" →
  `status_latest().last_tick.level`; `HALT` still means no pass.
- [ ] §A.3 `ALERT.md` → `alert_read()`; unacknowledged still suppresses §E and
  emits no `hot_fresh`.
- [ ] §B.1 ground: `doc_read('candidates')` + `doc_read('standing')`; keep the
  staleness rail verbatim, reading `verified_as_of` as a field instead of
  parsing prose.
- [ ] §B.2–§B.4: `get_quotes` → `quotes`, `get_movers` → `movers`,
  `get_advanced_price_history` → `price_history`, the chain tools → `option_chain`
  / `expiration_chain`. Keep the ~8 Schwab calls + ~4 web fetches budget: the
  budget was about the account's rate limits, which have not changed.
- [ ] §B-opt: unchanged except tool names. **Keep the IV/HV ~1.3 reject default
  and the ladder-health and exit-realism requirements word for word.**
- [ ] §B-oi: `oi-append.sh` → `ledger_append(name='oi', …)`; state that
  `appended: false` means already snapshotted today and the correct response is
  to skip that underlying, which is what exit 4 meant.
- [ ] §C tier rules: unchanged. The header banner sentence stays verbatim —
  `doc_replace` still refuses a body without it.
- [ ] §D write: the `--expect-last-pass` heredoc becomes
  `doc_replace(kind='candidates', body=…, expect_last_pass=…)`; keep
  "on a CAS refusal re-read, merge onto the fresh copy, retry **once**; never
  retry with the stale copy", pointing at the error message the tool returns.
- [ ] §G never-list: keep every entry; "never write `research/standing.md`"
  becomes "never call `doc_replace(kind='standing')` — that is the deep run's
  only write path".
- [ ] Keep the `§Scheduled` block's `hourly at :57` and `hours 9-14` strings
  exactly (`scripts/check-consistency.sh` check 5 greps them), and add a
  `Scheduled:` line naming `09:57` for the new Python check.

- [ ] **Step 5: `.claude/agents/deep-research.md` and `.claude/commands/deep-research.md`**

- [ ] Agent `tools:`: the research-scout list **minus** `mcp__engine__movers`,
  **plus** `mcp__engine__evidence_read` is *not* added (the deep run never read
  evidence). Exactly:
  `Read, WebSearch, WebFetch, mcp__engine__get_datetime, mcp__engine__market_hours, mcp__engine__quotes, mcp__engine__instruments, mcp__engine__option_chain, mcp__engine__expiration_chain, mcp__engine__price_history, mcp__engine__status_latest, mcp__engine__rules, mcp__engine__alert_read, mcp__engine__doc_read, mcp__engine__doc_replace, mcp__engine__ledger_read, mcp__engine__ledger_append, mcp__engine__tombstone, mcp__engine__universe_symbols`
- [ ] §A preconditions: §A.5's "postclose no-op if `research/oi/DATE.jsonl` has
  today's rows and `screen/DATE.jsonl` exists" →
  `ledger_read(name='oi', date=today)` and `ledger_read(name='screen', date=today)`;
  §A.6's preopen existence check → `doc_read(kind='preopen', date=today).exists`.
- [ ] §P preopen: `research-replace.sh preopen DATE` →
  `doc_replace(kind='preopen', date=…)`. **Keep the catch-up stamp sentence and
  the header banner verbatim.** Keep "no pings, no §E, no HOT promotions, no
  candidates writes, ever".
- [ ] §D.1 OI: as §B-oi above.
- [ ] §D.2 scorecard: `research-replace.sh scorecard` →
  `doc_replace(kind='scorecard')`; tombstone writes →
  `tombstone(symbol, date, gate, reason, ref_price[, hypo_qty, hypo_stop])`;
  ingestion reads → `ledger_read(name='screen'|'tombstones', …)`. **Keep the
  stop-HIT rule (`session low ≤ stop`, filled at `min(stop, open)`), the SPY
  control, the 5-session horizon, and the Friday weekly synthesis with its
  n-below-10 banner, word for word.**
- [ ] §D.3 screens: **delete the three "mandatory mechanics"** — the extraction
  pipeline, the 150-symbol chunking, and `verbose=True` / never `fields=` /
  never `--emit-qualified-set`. All three were properties of a payload the model
  had to route around. Replace with: "`universe_symbols()` returns the working
  universe as a list; quote it in batches with `quotes` and rank as below."
  **Keep** the qualification list (§1.4 floors, above 50-day SMA, positive 3-
  and 6-month returns, within ~10% of the 52-week high, the §3.1-derived
  unsizeable line), the top-3-to-5 combined WATCH cap shared with §D.5, the
  Yahoo v8 bar source with its retry-after-backoff and the cross-check against
  one Schwab series, the Stooq rejection, and the FMP drift screen including
  "the endpoint has no bmo/amc field — record `report_time: unknown` rather
  than guess" and "name the variable `FMP_API_KEY`, never a value".
- [ ] §D.4 roster: `research-replace.sh roster` →
  `doc_replace(kind='options-roster')`; keep TTL-expired-first, the ~20–30 cap,
  and the ladder verdict read from `rules`.
- [ ] §D.5 ETF track: unchanged but for tool names. **Keep "leveraged/inverse
  funds are NEVER surfaced".**
- [ ] §D.6 vetting: unchanged, SEC EDGAR User-Agent requirement included.
- [ ] §D.7 IV: `research-append.sh iv` → `ledger_append(name='iv', …)`.
- [ ] §D.8 standing: `research-replace.sh standing` →
  `doc_replace(kind='standing')`; keep "this step is the file's only write path"
  and the `Verified as of:` refresh rule.
- [ ] §W write whitelist: rewritten as a tool table, same rows, plus the note
  that `universe` is not on it — Task 14's engine job owns it now.
- [ ] Return contract: "Return the JSON object matching `DeepVerdict`: `kind`,
  `wrote[]`, `hot_fresh[]` (postclose only), `notes`, `summary` — the summary in
  the form `DEEP <mode> <ET time> | screened n | roster n/M | cohorts n |
  skipped: <features or ->`."
- [ ] Add `Scheduled: 08:17 ET (preopen) / 16:22 ET (postclose)`.
- [ ] Keep the CAS merge-and-retry-once paragraph.

- [ ] **Step 6: Run the prompt tests, then the gate**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_prompts.py -k "research or deep"`
Expected: PASS for the four files touched here; the `scout`/`catalyst`/
`sector-tag` parametrisations still fail until Task 17.
Also run `bash scripts/check-consistency.sh` and confirm it still reports
`CONSISTENT` — check 5 reads research.md.

- [ ] **Step 7: Commit**

```bash
git add .claude/commands/research.md .claude/commands/deep-research.md \
        .claude/agents/research-scout.md .claude/agents/deep-research.md \
        tests/engine/unit/test_prompts.py
git commit -m "prompts: the research loop calls engine tools, and the methodology is unchanged"
```

---

### Task 17: Rewrite `scout`, `catalyst`, `sector-tag`; retire the four v2 documents

**Files:**
- Modify: `.claude/commands/scout.md`, `.claude/agents/scout.md`,
  `.claude/commands/catalyst.md`, `.claude/agents/catalyst.md`,
  `.claude/commands/sector-tag.md`, `.claude/agents/sector-tagger.md`
- Replace with tombstones: `.claude/commands/weekly-universe.md`,
  `.claude/commands/tick.md`, `.claude/agents/weekly-universe.md`,
  `.claude/agents/tick-watch.md`, `.claude/agents/session-close.md`,
  `.claude/agents/trader.md`
- Test: `tests/engine/unit/test_prompts.py` (written in Task 16; the remaining
  parametrisations go green here)

Use the same mapping table as Task 16. Same rule: transport only, methodology
untouched.

- [ ] **Step 1: `.claude/agents/scout.md`**

- [ ] `tools:` becomes exactly:
  `Read, WebSearch, WebFetch, mcp__engine__get_datetime, mcp__engine__market_hours, mcp__engine__quotes, mcp__engine__instruments, mcp__engine__option_chain, mcp__engine__cohort, mcp__engine__evidence_read, mcp__engine__evidence_append, mcp__engine__escalation_raise, mcp__engine__sector_write, mcp__engine__status_latest, mcp__engine__alert_read, mcp__engine__rules`
- [ ] Delete the "resolve the account hash with `get_accounts`" paragraph
  entirely. The scout had `get_accounts` only to resolve a hash the prompt could
  not carry because `CLAUDE.md` redacts it; the engine resolves the hash and the
  scout has no account tool at all.
- [ ] Delete the bare-relative-path rule and the "three write paths through
  whitelisted scripts" sentence; replace with the three tool names.
- [ ] Return contract → "Return the JSON object matching `ScoutVerdict`:
  `cohort`, `observed`, `escalations[]` (each `{symbol, claim, evidence_ids}`),
  and `summary` in the form `SCOUT <ET date> | cohort n | observed n |
  escalated n | <symbols or ->`."
- [ ] Keep "a clean pass relays nothing" — but say why it is still true: the
  engine relays one Discord line per `escalations` entry and none when the list
  is empty.

- [ ] **Step 2: `.claude/commands/scout.md`**

- [ ] §A: `ALERT.md` → `alert_read()`; keep `CLOSING-ONLY` in the summary line.
  **Keep "empty cohort → emit `cohort 0` and stop, do not widen the window"** —
  and note that this is the `noop` verdict, not a failure.
- [ ] §B1: `get_datetime` → `mcp__engine__get_datetime`.
- [ ] §B2: `scripts/cohort.sh <date>` → `cohort(date=…)`; keep "the first 2–3
  rows not already observed today".
- [ ] §B3: `Read research/evidence/<SYMBOL>.jsonl` → `evidence_read(symbol=…)`.
  **Keep "read before searching — the delta is the signal; a first observation
  is never a delta" verbatim.**
- [ ] §B4: unchanged. **Keep every measured constraint from the 2026-08-30
  probe** — WebSearch is the workhorse; publisher article fetches are unreliable
  and an extraction failure is recorded rather than dropped; employer-review
  sites 403 so `employee` must never be required for a bar clear; aggregators
  are pointers of unknown provenance.
- [ ] §B5: `evidence-append.sh` → `evidence_append(...)`; keep "for **every**
  observation, including nulls".
- [ ] §C: `escalation-log.sh raise` → `escalation_raise(...)`. **Keep the
  four-part bar exactly**: 2+ distinct source types, a specific falsifiable
  claim, no mainstream coverage, a plausible link to a financial line item.
- [ ] Add `Scheduled: 07:12 ET, weekdays`.

- [ ] **Step 3: `.claude/agents/catalyst.md` and `.claude/commands/catalyst.md`**

- [ ] `tools:` = the scout list **minus** `mcp__engine__option_chain` and
  `mcp__engine__cohort`, **plus** `mcp__engine__sectors_read`.
- [ ] Delete the `get_accounts` hash paragraph, as for the scout.
- [ ] §A.2: "`research/sectors.tsv` absent → emit `scanned 0` and stop" →
  "`sectors_read()` returns an empty list → `scanned 0` and stop". The v2 gate
  was file existence; an empty table is the same condition and cannot be
  confused with an unreadable path.
- [ ] §B2: the three in-scope sectors and the "search the sectors, then map back
  to a tagged symbol; tag an untagged company that plausibly belongs via
  `sector_write` and proceed" rule stay verbatim.
- [ ] §B3 taxonomy, §B4 source types, §B5/§C bar: unchanged but for tool names.
- [ ] Return contract → `CatalystVerdict` with `summary` in the form
  `CATALYST <ET date> | scanned n | observed n | escalated n | <symbols or ->`.
- [ ] Add `Scheduled: 18:33 ET, weekdays`.

- [ ] **Step 4: `.claude/agents/sector-tagger.md` and `.claude/commands/sector-tag.md`**

- [ ] `tools:` becomes exactly:
  `Read, WebSearch, mcp__engine__get_datetime, mcp__engine__universe_names_page, mcp__engine__sectors_read, mcp__engine__sector_write, mcp__engine__cohort`
- [ ] §A: "both `universe-qualified.tsv` and `universe-names.tsv` must exist,
  else emit `names 0` and stop" → "`universe_names_page(offset=0, limit=1).total`
  is 0 → emit `names 0` and stop". Keep "tags carry forward; this pass adds and
  corrects".
- [ ] §B: the chunked `Read` with `offset`/`limit` → `universe_names_page(offset,
  limit)` at the same ~400-row page size. **Keep the classification table, the
  closed set of three tags, and — verbatim — "out-of-scope names are left
  untagged, not tagged `other`; `other` exists only to retire a previously
  in-scope tag", the bare-ticker/fund-name rule, the "when genuinely unsure leave
  it untagged" rule, the "WebSearch is for a handful of unfamiliar names, not a
  per-row lookup" rule, and "a name already tagged keeps its tag absent a
  specific reason".**
- [ ] §C: `sector-write.sh --batch DATE` heredoc → `sector_write(rows=[…],
  date=…)`. **Delete the entire "heredoc on the bare script path is the one
  multi-line form the permission gate accepts" paragraph** — that constraint was
  a property of the container's Bash gate and there is no Bash. Keep the ≤200-row
  batches (still a sensible request size) and keep "every row is validated before
  any row is written, so a refused batch has changed nothing" — which is now
  enforced in `sector_write`, not merely asked for.
- [ ] §D: `cohort.sh DATE` → `cohort(date=…)`; return the `SectorVerdict` with
  `summary` in the form `SECTORS <date> | names N | tagged T (+n new, r retired)
  | cohort C | <- or note>`. `new` and `retired` now come back **from the write
  tool** rather than being counted by the model.
- [ ] Add `Scheduled: 09:40 ET, Saturdays`.

- [ ] **Step 5: Write the six tombstones**

Each file is replaced entirely by a short document: an H1 marked `RETIRED`, one
paragraph saying what replaced it and where, and nothing else. No procedure, no
tool list, no schedule line — a retired document that still carries a procedure
is a document someone will follow.

- [ ] `.claude/commands/weekly-universe.md` and `.claude/agents/weekly-universe.md`:

```markdown
# /weekly-universe — RETIRED (v3 Plan 0c, 2026-09-08)

The weekly whole-market sweep is engine code: `engine/tc/loops/universe.py`,
scheduled in `config.yml` as `weekly_universe` at 07:40 ET on Saturdays. It
fetches the Nasdaq Trader directory over HTTP, applies the same five gates
against `rules.yml`, writes the `universe` table and renders `universe.md`
through the same document validator this command used to satisfy. Nothing about
the sweep required judgement, and the elaborate machinery this file described —
150-symbol chunks chosen to force the harness to spill a payload to disk, a
filter script the agent was forbidden to feed by hand, a flag whose only job was
stopping the daily run from overwriting the weekly file — existed to keep a
model away from data it did not need. There is no model in that loop now.
```

- [ ] `.claude/commands/tick.md` and `.claude/agents/tick-watch.md`: the same
  shape, pointing at `engine/tc/loops/tick.py` (the seven watches, one ledger
  row per sweep, `BLIND` as a state) and `engine/tc/loops/clocks.py` (§3.3/§3.5),
  scheduled as `tick` in `config.yml`. **The tombstone must not contain the
  string `**15 min** baseline`** — `scripts/check-consistency.sh` check 5 greps
  for it and would then compare a cadence no document owns.
- [ ] `.claude/agents/session-close.md`: points at `engine/tc/loops/session.py`
  — the §7.2 close and the one irreversible number, the high-water-mark ratchet,
  including the pre-2026-08-31 basis conversion this file specified.
- [ ] `.claude/agents/trader.md`: points at **Plan 1** — `engine/tc/orders/`,
  where the order path becomes deterministic code and the model's only
  write-shaped tools are `propose_*`. State plainly that the agent is not being
  ported: "Claude never holds `place`, `cancel`, `replace` or `preview`" is the
  design decision, and a contract test enforces it (spec §5, §10).

Each tombstone ends with: *"Read `CHANGELOG.md` and
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` for why.
Do not re-create this file."*

- [ ] **Step 6: Run the whole prompt suite and the bash checker**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_prompts.py`
Expected: PASS, every parametrisation.
Run: `bash scripts/check-consistency.sh`
Expected: `CONSISTENT`. If check 5 fails on `tick`, the tombstone still carries
`**15 min** baseline`; if it fails on `research`, the `§Scheduled` strings were
edited.
Run: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`

- [ ] **Step 7: Commit**

```bash
git add .claude/commands/ .claude/agents/ tests/engine/unit/test_prompts.py
git commit -m "prompts: scout, catalyst and the tagger call engine tools; four v2 documents become tombstones"
```

---

### Task 18: Bootstrap and the Plan 0c checklist

**Files:**
- Modify: `HANDOFF.md`, `host/README.md`
- Create: `docs/superpowers/plans/2026-09-08-v3-plan0c-runbook.md`

No code. This is the document Chris and the next session work from.

- [ ] **Step 1: Write the runbook**

Contents, in this order:

1. **Secrets.** Add to `/srv/tc/.env`, on the Pi, by hand:
   `CLAUDE_CODE_OAUTH_TOKEN` (the same subscription token the old scheduler uses
   — note the memory that server jobs share the laptop subscription, so heavy
   laptop sessions during 09:30–16:30 ET will starve these jobs, and builds
   belong outside market hours), `TC_RUNNER_TOKEN`, `TC_MCP_RESEARCH_TOKEN`,
   `TC_MCP_DECIDE_TOKEN`. Generate each with
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Never commit
   them; never print them into a Discord message or a log line.
2. **Build and start.**
   ```bash
   cd /srv/tc/trade-challenge/docker
   docker compose --profile engine build engine runner
   docker compose --profile engine up -d engine runner
   curl -s http://127.0.0.1:8090/health | jq .    # runner: ok true, busy false
   curl -s http://127.0.0.1:8080/health | jq .    # engine: runner_ok true
   ```
3. **Import the v2 store — first, before any job runs.**
   ```bash
   docker compose exec engine tc --config /app/repo/config.yml \
       import-research --store-dir /data/store --dry-run
   docker compose exec engine tc --config /app/repo/config.yml \
       import-research --store-dir /data/store
   ```
   Read the `skipped` list. Every entry is a legacy row the new validators
   refuse; that is information, not damage.
4. **Seed the universe and the tags, in this order** — each depends on the last:
   ```bash
   docker compose exec engine tc ... run --once weekly_universe   # ~11k fetched, ~3k qualified
   docker compose exec engine tc ... run --once sector_tag        # tags the qualified names
   docker compose exec engine tc ... run --once postclose         # the first deep run
   ```
5. **Verify the cohort is non-empty.** This is the single check that says the
   chain works end to end, because the scout has reported `cohort 0` every day
   since 2026-09-01 for want of `universe-qualified.tsv` and `sectors.tsv`:
   ```bash
   docker compose exec engine sqlite3 /data/engine.db \
     "SELECT COUNT(*) FROM universe WHERE qualified=1;
      SELECT COUNT(*) FROM sectors WHERE sector != 'other';"
   docker compose exec engine tc ... run --once scout             # must NOT report cohort 0
   ```
   A `cohort 0` after all four steps means the universe has no in-scope tagged
   name whose estimated next print falls 21–42 days out — which is a legitimate
   answer between earnings seasons. Check the two counts above before concluding
   anything is broken.
6. **Exit checks for Plan 0c**, as checkboxes:
   - [ ] Every role's `list_tools()` matches `ROLE_TOOLS` and contains nothing
         matching `place|cancel|replace|order` (the contract test, run on the Pi).
   - [ ] A research token on `/mcp/decide/` gets 403; no token gets 401.
   - [ ] One `scout`, one `catalyst`, one `research`, one `preopen` and one
         `postclose` run recorded `done` or `noop` with a **structured** verdict —
         zero `content_failed` across five consecutive sessions (spec §11's Phase 0
         exit criterion).
   - [ ] `ResultMessage.permission_denials` is empty across those runs, or every
         denial is explained: a non-empty list means a prompt still asks for a tool
         its job does not have.
   - [ ] `docker stats` shows the runner peaking under 1 GB during a full
         postclose deep run.
   - [ ] `weekly_universe` completed with `qualified` in the low thousands, and
         `universe.md` renders with its ten-column table.
   - [ ] Stopping the runner container makes `/health` report `runner_ok: false`
         and the next Claude job record `failed` with `ConnectError` — proving the
         watchdog sees the runner from outside it.
7. **Rollback.** `docker compose --profile engine stop runner` leaves the engine
   ticking with every Claude job recording `noop: no runner configured`. The old
   scheduler container is untouched throughout Plan 0c and remains the live
   research path until Phase 1 says otherwise.
8. **What is deliberately still on the old stack:** `tick`, `sessionclose` and
   `execute` in `docker/crontab`. This plan retires their *command files*, not
   their cron entries — the engine's own `tick` and `session_close` loops have
   run in shadow since Phase 0b, and Phase 1 is where the old ones stop.

- [ ] **Step 2: Update `HANDOFF.md`** with the current state: what Plan 0c
  landed, the four new secrets, the two new containers, and the one command that
  proves the chain (`run --once scout` reporting a non-zero cohort).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-09-08-v3-plan0c-runbook.md HANDOFF.md host/README.md
git commit -m "docs: the Plan 0c runbook — import first, then universe, tags, deep run, and check the cohort"
```

---

## Self-review

**1. Spec coverage.**

| spec section | task |
|---|---|
| §3 runner container, 1 GB, `oom_score_adj` 800, `POST /run`, no credential | 10, 11 |
| §3 scheduler: explicit ET times, per-job lock, missed ≠ late | 13 (reuses Phase 0b's `Scheduler`) |
| §4 `cli_path` + version pin, `CLAUDE_CODE_OAUTH_TOKEN` via `env` | 10, 11 |
| §4 typed allowlists per job, prompts as Python strings | 12 |
| §4 read-only enforced twice, `PreToolUse` deny, no `bypassPermissions` | 10 |
| §4 structured verdicts, `content_failed` on no structured result | 12, 13 |
| §4 prompt reuse via `setting_sources=["project"]`, frontmatter rewritten once | 10, 16, 17 |
| §4 job list (`scout`, `catalyst`, deep preopen/postclose, weekly-universe) | 12, 13, 14 |
| §4 `decide` is trigger-driven, not scheduled | **not built here** — no `decide` job, no `propose_*`; the decide role exists with read tools only (Task 7) and Plan 1 adds the trigger, the gates and the proposals. Recorded as deferred, not missed. |
| §4 `tc run-job --via-cli` break-glass | **not built** — see gaps below |
| §6 research artifacts split: ledgers → tables via validating tools | 3, 5, 9 |
| §6 documents → files under `/data/research/docs` via `doc_replace` with CAS | 4, 9 |
| §6 cohort as a pure function | 6 |
| §6 tables `evidence, escalations, sectors, universe, tombstones, screen_rows, iv_series, oi_snapshots, events, artifacts` | 3 |
| §7 pings on `done/noop` vs `content_failed/failed/timeout` | 13 (through Phase 0b's `Pinger`) |
| §7 `runner_ok` on `/health` for the host probe | 13 |
| §9 config, secrets, fail-fast | 1 |
| §9 consistency: schedule-vs-doc over `config.yml`, tool-registry check | 7, 13 |
| §10 contract test: no role sees `place\|cancel\|replace\|order` | 7 |
| §10 replay: exit 0 with no structured verdict is `content_failed` | 13 |
| §10 replay: NUL bytes in a legacy ledger rejected on read | 15 |
| §11 Phase 0 bootstrap and exit criteria | 18 |

Writers-contract coverage: every writer in §1 is ported (research-write →
`doc_replace` CAS; research-replace → `doc_replace` per-kind; research-append,
oi-append, data-append → `ledger_append`; evidence-append, escalation-log,
sector-write → their own tools; universe-fetch + universe-filter + cohort →
Tasks 14 and 6; latest-status → `status_latest`). Every §4.5 "behaviour to
preserve verbatim" item has a named test: empty-is-an-answer (6, 12, 13),
degrade-to-stale (14), CAS-once (4, 16), `Last pass:` carried forward (16),
`--emit-qualified-set` (14 — deleted, with the invariant it protected made
structural), HWM basis (untouched, Phase 0b), account_value vs comp_capital
(untouched), one-prediction-one-row (3, 5), `mainstream` never counts (5),
never read a payload into context (2, 14).

**Deliberately not ported, each with its reason stated in the plan:**
`status-write.sh` and `tick-append.sh` (their consumers became engine loops in
Phase 0b; a `status_write` tool would give a model a way to write the file the
engine owns), `trade-log-append.sh` (Plan 1, the order path), `escalation-log.sh
score`'s host-clock ET date (the tool takes the date explicitly), the mkdir lock
and the numeric exit codes (Task 4), the bare-relative-path rule and the
heredoc-only rule (no Bash exists), `create_option_symbol` (order path only).

**2. Gaps I could not place.**

- **`tc run-job <name> --via-cli` (spec §4's break-glass path).** It shells out
  to `claude -p`, and this plan's global constraint forbids `subprocess` anywhere
  under `engine/`. It is not built, and I did not add a task for it: the
  debugging need is met by `tc run --once <job>` against the real runner plus the
  runner's own `/health`. If Chris wants it, it belongs in `host/`, outside the
  engine, and needs its own decision.
- **`corpus_append` kinds other than `events`.** The contract doc's tool table
  proposes `corpus_append(kind ∈ orders|quotes|decisions|events|counterfactuals)`.
  Only `events` is reachable here, through `ledger_append`. `orders` and
  `decisions` are order-path records (Plan 1); `quotes` had no fixed schema at
  all (free-form symbol→price keys) and `counterfactuals` is a research artifact
  I could not place under any prompt this plan rewrites — it appears in the
  status corpus but no command file writes it. Flagged rather than invented.
- **Reader tools beyond the controller's enumeration.** `doc_read`,
  `evidence_read`, `escalations_read`, `ledger_read`, `sectors_read`,
  `universe_symbols`, `universe_names_page` and `alert_read` are in
  `RESEARCH_TOOLS` because the prompts cannot run without them — the contract
  document's §4.2 lists each one against the `Read`/Bash call it replaces. I
  added them rather than leaving the rewritten prompts with no way to read.
- **`market_hours` and `MarketWindow.is_trading_day`.** The contract doc flags
  that `MarketWindow.from_payload` returns `is_trading_day=False` unless `isOpen`
  is true, while `tick.md` §B1 warns never to gate on `isOpen` (it reads `true`
  at 23:20 ET). Task 8 works around it by reporting the window and the flag
  separately, but the underlying model is Phase 0b's and I did not change it.
  If `is_trading_day` is wrong on an after-hours read, the engine's own
  trading-day gate is wrong too — worth a look in Plan 1.
- **`universe-names.tsv`'s separate existence.** v2 kept descriptions out of the
  ten-column file so nothing that indexed positionally would move. Here
  `description` is a column on the `universe` table and `universe_names_page`
  reads it; nothing indexes positionally any more. Stated, not preserved.
- **The `research` job's 45-minute cadence gate.** Kept in the prompt as
  documentation, but on the server it is the schedule that enforces cadence and
  the prompt says so — unchanged from v2's own instruction.

**3. Type consistency.** `VerboseQuote` (Task 2) is consumed by
`filter_universe` (Task 14) with the same field names. `UniverseRow` (14) matches
`Store.replace_universe`'s row dict keys (3) and the fields `cohort` reads (6).
`CohortRow` is defined once, in Task 6, and re-exported by the `cohort` tool (9).
`DocView`/`DocWrite` are defined in Task 4 and reused by the tools in Task 9 —
not redefined. `RunRequest`/`RunResult` field names in the runner (10) equal the
JSON keys `RunnerClient` sends and reads (13), including `timed_out`,
`verdict_raw` and `permission_denials`. `JobSpec.agent` is asserted equal to the
agent file's `name:` frontmatter by a test in Task 16 that Tasks 16 and 17 must
both satisfy. `ROLE_TOOLS` (7) is the one table Tasks 8, 9, 12 and 16 all read;
`tools_for` (12) fails at import on a name that is not in it. `Verdict` is Phase
0b's `Literal` in `store/db.py`, and `classify` (13) returns only its members.
