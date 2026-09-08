# v3 Plan 1 — The Order Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the one deterministic order path — typed intents from Claude, pure gates over `rules.yml`, a state machine with reconcile-by-query idempotency, a persisted Discord approval gate with protective-stop auto-approve, the stop and fill loops, and the `trade-log.csv` / `status/DATE.md` exports — so that `orders.enabled` is the only switch between protective-only and full trading.

**Architecture:** Writes enter the broker through a **new** `BrokerWriter` protocol (`tc/broker/writer.py`); the Phase 0a read-only `Broker` protocol is untouched, and a contract test asserts no MCP role ever sees a write tool. Claude's only write-shaped surface is `propose_entry` / `propose_exit` / `propose_option_close` / `get_proposal`. Everything after a proposal — gates, preview, the three-way compare, placement, the id resolution, the §3.4 stop, the ratchet, the exit ordering — is Python with tests. `FakeBroker` gains scripted fills so the whole path runs in paper mode with no network.

**Tech Stack:** Python 3.12, `schwab-py` 1.5.1 (`OrderBuilder`, `OptionSymbol`, `preview_order` / `place_order` / `replace_order` / `cancel_order`), `discord.py`, `pydantic` 2 + `pydantic-settings`, `aiosqlite`, `httpx`, `starlette`, `hypothesis`, `pytest` + `pytest-asyncio`, `mypy --strict`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` — §4 (the `decide` job), §5 (intents, gates, state machine, approval, idempotency), §6 (tables), §10 (testing), §11 Phase 2 exit criteria. The binding contract research — every gate, every trader.md rule, the schwab-py order API with file:line cites, the trade-log columns and the Discord approval semantics — is `.superpowers/research/1-order-path-contract.md` and is quoted inside the tasks.

## Global Constraints

- Python **3.12**; gate after every task: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`. A path argument to pytest drops `asyncio_mode`; focused runs use `pytest -q -c pyproject.toml ../tests/engine/unit/test_x.py`.
- Money is `decimal.Decimal`. Caps **floor** to cents via `tc.money.floor_cents` / `tc.rules.arith.cap_dollars`; a cap never rounds up (`pre-order-check.sh:47-49`: "§3.1 is unamendable core; the permissive direction is the wrong direction"). Never `float` for money — schwab-py's `set_price`/`set_stop_price` get **strings**, never floats (`schwab/orders/generic.py:37-44`).
- Every model: `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")`. Frozen dataclasses only where a non-pydantic value (`Rules`) has to travel inside; each such case says why.
- **No rule number is hard-coded** anywhere under `engine/`: every threshold comes through `tc.rules.model.Rules`. The 900.00 reserve comes from `Settings.engine.reserve_usd`. Operational constants that are not §9 rules (the 600 s approval timeout, 15:30 / 15:55 ET, the 60 s stop deadline, the 3/2 retry bounds) live in `config.yml` under `orders:`, never inline. `scripts/check-consistency.sh`'s `HARDCODE` regex (`* 35 / 100`-shaped literals) and `UNGATED` flag must stay unmatched in every file this plan writes.
- **The read-only `Broker` protocol in `tc/broker/client.py` is not modified.** Writes live only in `tc/broker/writer.py::BrokerWriter`; chain and fundamental reads live only in `tc/broker/market.py::MarketReader`. `grep -rn "place_order\|replace_order\|cancel_order" engine/tc/broker/client.py` must stay empty.
- **No MCP role may expose a write tool.** A contract test asserts that for every role in the registry, no tool name matches `place|cancel|replace|order` (spec §5, §10).
- No `subprocess`, no shelling out, anywhere under `engine/`.
- Never commit an account number, account hash, order id, token or secret (`CLAUDE.md §7.4`). Fixtures use `HASH_REDACTED` and synthetic order ids in the **1000000000001** range.
- `mypy --strict` covers `tc` **and** `../tests/engine` (`engine/pyproject.toml` `files`). A test that needs to poke a private attribute uses `cast(Any, obj)`, never a bare `# type: ignore`.
- Work on branch `feat/v3-engine-1` from `main`, in a git worktree created via `superpowers:using-git-worktrees`. Stage explicit paths; never `git add -A` (memory: `store-commits-never-add-all`).
- Commit trailer on every commit: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **This plan ships with `orders.enabled: false`.** False means protective-only: stops, stop replaces, orphan cancels and §3.3/§3.5 forced closes run; no entry is placed. Phase 2 flips one flag (spec §11).
- Plan 0c is **not written yet.** Where this plan needs the Claude runner it defines the interface it consumes (`JobRunner.run_job(name, params) -> JobResult`) and marks it *consumed from Plan 0c*; nothing here calls a runner.

## File Structure

```
engine/tc/
  broker/writer.py        CREATE: BrokerWriter protocol, PreviewResult, extract_order_id; impls on SchwabBroker + FakeBroker
  broker/market.py        CREATE: MarketReader protocol, ChainQuote, InstrumentFundamental; impls on both brokers
  broker/fake.py          MODIFY: scripted fills, synthetic order ids, write + market surfaces
  broker/client.py        MODIFY: SchwabBroker gains the writer/market methods (the Broker protocol is untouched)
  orders/__init__.py      CREATE
  orders/intent.py        CREATE: OptionSpec, EntryIntent, ExitIntent, client_key
  orders/specs.py         CREATE: OrderSpec + the five builders + is_protective_stop
  orders/snapshot.py      CREATE: BookSnapshot, CorrelationView, build_snapshot
  orders/gates.py         CREATE: GateResult, GateContext, GATE_ORDER, the 22 gates, run_pre_preview/run_post_preview
  orders/machine.py       CREATE: OrderState, OrderMachine, resolve_placing_rows
  orders/approval.py      CREATE: Approver protocol, ApprovalRequest/Decision, AutoApprover, protective auto-approve
  discord/__init__.py     CREATE
  discord/bot.py          CREATE: DiscordBot — embeds, reactions, /status /ack /halt /resume, Poster
  loops/stops.py          CREATE: ensure_stops, ratchet_stops, cancel_orphans, handle_trips
  loops/fills.py          CREATE: poll_fills, cancel_working_entries
  mcp/__init__.py         CREATE
  mcp/tools_propose.py    CREATE: propose_* + get_proposal + book; ROLE_TOOLS registry
  jobs/__init__.py        CREATE
  jobs/spec.py            CREATE: JobSpec, JobRunner protocol (Plan 0c), DECIDE, decide_due
  store/schema.sql        MODIFY: proposals, orders, order_events(+triggers), stops, approvals, trade_log columns
  store/db.py             MODIFY: `_transaction` -> public `transaction`
  store/orders_db.py      CREATE: OrderStore — every order-path read and write
  store/export.py         CREATE: render_trade_log_csv, render_status_md, export_day
  notify.py               MODIFY: Poster protocol, FirstWorking
  config.py               MODIFY: OrdersConfig; Secrets discord bot token/channel/approver
  rules/model.py          MODIFY: four option-quality accessors
  main.py                 MODIFY: order-path wiring, trips -> stops, EngineState fields
config.yml                MODIFY: orders: section
engine/pyproject.toml     MODIFY: discord.py
.claude/agents/decide.md  CREATE (trader.md §1-§2 ported; §3 workflow now lives in the engine)
tests/engine/unit/        test_writer.py test_market.py test_intent.py test_specs.py test_orders_db.py
                          test_snapshot.py test_gates_pure.py test_gates_network.py test_machine.py
                          test_approval.py test_discord_bot.py test_stops.py test_fills.py
                          test_tools_propose.py test_jobs_spec.py test_export.py
tests/engine/property/    test_gate_props.py
tests/engine/contract/    test_mcp_roles_readonly.py
tests/engine/paper/       __init__.py test_entry_fill_stop.py
tests/engine/fixtures/broker/  add chain-AMH.json, fundamental-AMH.json, fills.json,
                               preview-equity.json, preview-clamped.json, preview-option.json
```

---

### Task 1: `BrokerWriter` and `MarketReader` — the broker's write and chain surfaces

**Files:**
- Create: `engine/tc/broker/writer.py`, `engine/tc/broker/market.py`
- Modify: `engine/tc/broker/client.py` (methods on `SchwabBroker` only — the `Broker` protocol is untouched), `engine/tc/broker/fake.py`
- Test: `tests/engine/unit/test_writer.py`, `tests/engine/unit/test_market.py`; fixtures `chain-AMH.json`, `fundamental-AMH.json`, `preview-equity.json`

**Interfaces:**
- Consumes: `tc.broker.client.SchwabBroker._c/_guard`, `tc.broker.models.OrderRow`, `tc.money.D`.
- Produces:
  - `PreviewResult(accepted: bool, status: str, order_value: Decimal | None, price: Decimal | None, commission: Decimal, fees: dict[str, Decimal], raw: dict[str, Any])`.
  - `class BrokerWriter(Protocol)`: `async preview_order(account_hash, spec: dict) -> PreviewResult`; `async place_order(account_hash, spec: dict) -> int | None`; `async replace_order(account_hash, order_id: int, spec: dict) -> int | None`; `async cancel_order(account_hash, order_id: int) -> None`; `async get_order(account_hash, order_id: int) -> OrderRow`; property `last_location_header: str | None`.
  - `extract_order_id(resp: httpx.Response, account_hash: str) -> tuple[int | None, str | None]` — `(order_id, raw Location header)`.
  - `ChainQuote(option_symbol, underlying, expiry: date, strike: Decimal, kind: Literal["C","P"], delta: Decimal, open_interest: int, bid: Decimal, ask: Decimal, underlying_price: Decimal)` with `mid` and `spread_pct_of_mid` properties.
  - `InstrumentFundamental(symbol, description, exchange, avg_daily_volume: int)`.
  - `class MarketReader(Protocol)`: `async option_quote(underlying, expiry: date, strike: Decimal, kind) -> ChainQuote | None`; `async fundamental(symbol) -> InstrumentFundamental | None`.
  - `SchwabBroker` and `FakeBroker` satisfy both protocols (static conformance assignments under `TYPE_CHECKING`, as `client.py` already does for `Broker`).

Three traps from the contract research that this task exists to absorb, all inside the wrapper so no caller can hit them:

1. **`cancel_order` and `get_order` take `(order_id, account_hash)` in schwab-py — the opposite order from `place_order` / `replace_order` / `preview_order`** (`schwab/client/base.py:184,189` vs `:307,320,330`; research §7.6 calls it "an easy, silent bug"). Every method here is `(account_hash, …)`; the flip happens once, in the wrapper.
2. **`place_order` returns no JSON body.** The id is in the `Location` header only, and `Utils.extract_order_id` returns `None` — not an exception — when the header is missing or malformed (`schwab/utils.py:133-140`). `None` therefore means *unknown status*, never *not placed*: the caller falls through to the §5.5 reconcile query.
3. **The preview may echo a different limit than you sent.** On 2026-08-24 a 34.80 buy came back at 34.79 with `orderValue 1008.91` — Schwab clamped the marketable limit to the NBBO ask, and the $0.29 gap would have failed the $0.01 three-way. `PreviewResult.price` is the price the *preview* returned, and Task 7's gate compares against that, not against what was sent.

- [ ] **Step 1: Write the failing writer tests**

Create `tests/engine/unit/test_writer.py`:

```python
"""The write surface. Nothing here touches the network: a stub client with
the five schwab-py methods is bound directly to SchwabBroker._client."""

from __future__ import annotations

from decimal import Decimal as D
from typing import Any, cast

import httpx
import pytest

from tc.broker.client import BrokerError, BrokerUnauthorized, SchwabBroker
from tc.broker.writer import PreviewResult, extract_order_id

HASH = "HASH_REDACTED"
OID = 1000000000001


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.responses: dict[str, httpx.Response] = {}

    def _resp(self, name: str) -> httpx.Response:
        return self.responses.get(name, httpx.Response(200, json={}))

    async def preview_order(self, account_hash: str, spec: Any) -> httpx.Response:
        self.calls.append(("preview_order", (account_hash, spec)))
        return self._resp("preview_order")

    async def place_order(self, account_hash: str, spec: Any) -> httpx.Response:
        self.calls.append(("place_order", (account_hash, spec)))
        return self._resp("place_order")

    async def replace_order(self, account_hash: str, order_id: int, spec: Any) -> httpx.Response:
        self.calls.append(("replace_order", (account_hash, order_id, spec)))
        return self._resp("replace_order")

    async def cancel_order(self, order_id: int, account_hash: str) -> httpx.Response:
        self.calls.append(("cancel_order", (order_id, account_hash)))
        return self._resp("cancel_order")

    async def get_order(self, order_id: int, account_hash: str) -> httpx.Response:
        self.calls.append(("get_order", (order_id, account_hash)))
        return self._resp("get_order")


def _broker(stub: _StubClient) -> SchwabBroker:
    b = SchwabBroker.__new__(SchwabBroker)
    cast(Any, b)._store = None
    cast(Any, b)._app_key = "k"
    cast(Any, b)._app_secret = "s"
    cast(Any, b)._client = stub
    cast(Any, b)._location = None
    return b


def _location(order_id: int) -> dict[str, str]:
    return {"Location": f"https://api.schwabapi.com/trader/v1/accounts/{HASH}/orders/{order_id}"}


def test_extract_order_id_reads_the_location_header() -> None:
    resp = httpx.Response(201, headers=_location(OID))
    assert extract_order_id(resp, HASH) == (OID, _location(OID)["Location"])


def test_extract_order_id_is_none_without_the_header() -> None:
    # schwab/utils.py:133-136 returns None, not an exception. Unknown status,
    # never "not placed" — the caller must reconcile by query (§5.5).
    order_id, loc = extract_order_id(httpx.Response(201), HASH)
    assert order_id is None and loc is None


def test_extract_order_id_refuses_another_accounts_hash() -> None:
    resp = httpx.Response(201, headers={"Location": f"https://api.schwabapi.com/trader/v1/accounts/OTHER/orders/{OID}"})
    with pytest.raises(BrokerError):
        extract_order_id(resp, HASH)


async def test_place_order_returns_the_id_and_records_the_header() -> None:
    stub = _StubClient()
    stub.responses["place_order"] = httpx.Response(201, headers=_location(OID))
    b = _broker(stub)
    assert await b.place_order(HASH, {"orderType": "LIMIT"}) == OID
    assert b.last_location_header == _location(OID)["Location"]
    assert stub.calls[0] == ("place_order", (HASH, {"orderType": "LIMIT"}))


async def test_place_order_without_a_header_is_none_not_an_error() -> None:
    stub = _StubClient()
    stub.responses["place_order"] = httpx.Response(201)
    assert await _broker(stub).place_order(HASH, {}) is None


async def test_place_order_401_is_unauthorized() -> None:
    stub = _StubClient()
    stub.responses["place_order"] = httpx.Response(401, text="invalid_grant")
    with pytest.raises(BrokerUnauthorized):
        await _broker(stub).place_order(HASH, {})


async def test_cancel_and_get_flip_the_argument_order_exactly_once() -> None:
    stub = _StubClient()
    stub.responses["get_order"] = httpx.Response(200, json={
        "orderId": OID, "status": "WORKING", "orderType": "STOP_LIMIT",
        "duration": "GOOD_TILL_CANCEL", "enteredTime": "2026-09-08T14:00:00+0000",
        "quantity": 29, "filledQuantity": 0, "price": "30.40", "stopPrice": "32.01",
        "orderLegCollection": [
            {"instruction": "SELL", "quantity": 29,
             "instrument": {"symbol": "AMH", "assetType": "EQUITY"}}
        ],
    })
    b = _broker(stub)
    await b.cancel_order(HASH, OID)
    row = await b.get_order(HASH, OID)
    # schwab-py takes (order_id, account_hash) on both — the flip lives here.
    assert stub.calls[0] == ("cancel_order", (OID, HASH))
    assert stub.calls[1] == ("get_order", (OID, HASH))
    assert row.stop_price == D("32.01") and row.order_id == OID


async def test_preview_parses_value_price_commission_and_fees() -> None:
    stub = _StubClient()
    stub.responses["preview_order"] = httpx.Response(200, json={
        "orderStrategy": {
            "status": "ACCEPTED", "orderValue": 1008.91, "price": "34.79",
            "orderLegCollection": [{"instruction": "BUY"}],
        },
        "orderBalance": {"projectedCommission": 0.65, "projectedAvailableFund": 850.34},
        "commissionAndFee": {"feeLegs": [
            {"feeValues": [{"feeType": "OPT_REG_FEE", "feeValue": 0.01},
                           {"feeType": "SEC", "feeValue": 0}]}
        ]},
    })
    r: PreviewResult = await _broker(stub).preview_order(HASH, {})
    assert r.accepted is True and r.status == "ACCEPTED"
    assert r.order_value == D("1008.91")
    # The CLAMP: the limit sent was 34.80; the preview came back 34.79.
    assert r.price == D("34.79")
    assert r.commission == D("0.65") and r.fees["OPT_REG_FEE"] == D("0.01")


async def test_preview_rejected_is_not_accepted_and_does_not_raise() -> None:
    stub = _StubClient()
    stub.responses["preview_order"] = httpx.Response(200, json={
        "orderStrategy": {"status": "REJECTED", "orderValue": 0},
        "orderValidationResult": {"rejects": [{"message": "oversold position"}]},
    })
    r = await _broker(stub).preview_order(HASH, {})
    assert r.accepted is False and "oversold" in r.status.lower() + str(r.raw)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_writer.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.broker.writer'`.

- [ ] **Step 3: Implement `engine/tc/broker/writer.py`**

```python
"""The broker's WRITE surface — deliberately a separate protocol from the
read-only `Broker` in client.py.

Three schwab-py facts are absorbed here so that no caller can hit them:

* `cancel_order` and `get_order` take `(order_id, account_hash)`; every other
  order method takes `(account_hash, ...)` (schwab/client/base.py:184,189 vs
  :307,320,330). Every method below is (account_hash, ...) and flips once.
* `place_order` returns no JSON body. The order id is in the `Location`
  response header only, and a missing header yields None — never an
  exception (schwab/utils.py:133-140). None means UNKNOWN STATUS: the caller
  reconciles by query (spec §5.5) and never resubmits (CLAUDE.md §4.6).
* The preview may carry a DIFFERENT limit than the one sent — Schwab clamps a
  marketable buy limit to the NBBO (observed 2026-08-24: sent 34.80, previewed
  34.79). `PreviewResult.price` is the previewed price, and it is what the
  §4.10 three-way must compare against.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Protocol

import httpx

from tc.broker.client import BrokerError
from tc.broker.models import OrderRow
from tc.money import D

LOCATION_RE = re.compile(
    r"https://api\.schwabapi\.com/trader/v1/accounts/(\w+)/orders/(\d+)"
)


class PreviewResult:  # noqa: D101 -- documented by the model below
    pass


def extract_order_id(resp: httpx.Response, account_hash: str) -> tuple[int | None, str | None]:
    """`(order_id, raw Location)`. A missing or unparseable header is
    (None, None) — the §5.5 reconcile query's job, not an error."""
    loc = resp.headers.get("Location")
    if loc is None:
        return None, None
    m = LOCATION_RE.match(loc)
    if m is None:
        return None, None
    if m.group(1) != account_hash:
        raise BrokerError("order Location names a different account hash")
    return int(m.group(2)), loc
```

Then the pydantic models and the protocol (same file):

```python
from pydantic import BaseModel, ConfigDict


class PreviewResult(BaseModel):  # noqa: F811 -- replaces the forward stub above
    model_config = ConfigDict(extra="forbid")
    accepted: bool
    status: str
    order_value: Decimal | None
    price: Decimal | None
    commission: Decimal
    fees: dict[str, Decimal]
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> PreviewResult:
        strategy = body.get("orderStrategy") or {}
        balance = body.get("orderBalance") or {}
        status = str(strategy.get("status", ""))
        fees: dict[str, Decimal] = {}
        for leg in (body.get("commissionAndFee") or {}).get("feeLegs") or []:
            for fv in leg.get("feeValues") or []:
                fees[str(fv.get("feeType", "?"))] = D(str(fv.get("feeValue", 0)))
        raw_value = strategy.get("orderValue")
        raw_price = strategy.get("price")
        return cls(
            accepted=status.upper() == "ACCEPTED",
            status=status,
            order_value=None if raw_value is None else D(str(raw_value)),
            price=None if raw_price is None else D(str(raw_price)),
            commission=D(str(balance.get("projectedCommission", 0))),
            fees=fees,
            raw=body,
        )


class BrokerWriter(Protocol):
    async def preview_order(self, account_hash: str, spec: dict[str, Any]) -> PreviewResult: ...
    async def place_order(self, account_hash: str, spec: dict[str, Any]) -> int | None: ...
    async def replace_order(
        self, account_hash: str, order_id: int, spec: dict[str, Any]
    ) -> int | None: ...
    async def cancel_order(self, account_hash: str, order_id: int) -> None: ...
    async def get_order(self, account_hash: str, order_id: int) -> OrderRow: ...

    @property
    def last_location_header(self) -> str | None:
        """The raw `Location` of the most recent place/replace, for the
        `orders.location_header` audit column. Safe as writer state because
        spec §5.3 allows exactly one order in flight at a time; if that
        invariant ever moves, this must move with it."""
        ...
```

Delete the forward stub class once the real `PreviewResult` is in place (it exists above only so the module reads top-down; a single class is what ships).

Add to `SchwabBroker` in `client.py` (methods only — the `Broker` protocol above them is not edited):

```python
    _location: str | None = None

    @property
    def last_location_header(self) -> str | None:
        return self._location

    async def preview_order(self, account_hash: str, spec: dict[str, Any]) -> PreviewResult:
        data = await self._guard(await self._c().preview_order(account_hash, spec))
        assert isinstance(data, dict)
        return PreviewResult.from_payload(data)

    async def place_order(self, account_hash: str, spec: dict[str, Any]) -> int | None:
        resp = await self._c().place_order(account_hash, spec)
        if resp.status_code == 401:
            await self.close()
            raise BrokerUnauthorized(resp.text[:200])
        if resp.status_code >= 400:
            raise BrokerError(f"{resp.status_code}: {resp.text[:200]}")
        order_id, self._location = extract_order_id(resp, account_hash)
        return order_id

    async def replace_order(
        self, account_hash: str, order_id: int, spec: dict[str, Any]
    ) -> int | None:
        resp = await self._c().replace_order(account_hash, order_id, spec)
        ...  # identical status handling to place_order
        new_id, self._location = extract_order_id(resp, account_hash)
        return new_id

    async def cancel_order(self, account_hash: str, order_id: int) -> None:
        await self._guard(await self._c().cancel_order(order_id, account_hash))

    async def get_order(self, account_hash: str, order_id: int) -> OrderRow:
        data = await self._guard(await self._c().get_order(order_id, account_hash))
        assert isinstance(data, dict)
        return OrderRow.from_payload(data)
```

`place_order` and `replace_order` do **not** go through `_guard`'s `.json()` path: those responses carry no body (`base.py:307` docstring), so parsing one would raise on success. Factor the shared status handling into a small `_status_only(resp)` helper rather than repeating it.

Add `_writer_conforms: BrokerWriter = cast(SchwabBroker, None)` beside the existing `_schwab_conforms` under `TYPE_CHECKING`.

- [ ] **Step 4: Run the writer tests**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_writer.py`
Expected: PASS.

- [ ] **Step 5: Write the failing market-reader tests and fixtures**

Fixture `tests/engine/fixtures/broker/chain-AMH.json` — a trimmed real-shape chain (`get_option_chain(strategy=SINGLE)`), one expiry, one strike:

```json
{
 "underlyingPrice": 34.79,
 "callExpDateMap": {
  "2026-10-16:38": {
   "30.0": [{"symbol": "AMH   261016C00030000", "bid": 5.10, "ask": 5.40,
             "openInterest": 1240, "delta": 0.62, "daysToExpiration": 38}]
  }
 },
 "putExpDateMap": {}
}
```

Fixture `fundamental-AMH.json`:

```json
{"AMH": {"symbol": "AMH", "exchange": "NYSE",
         "description": "AMERICAN HOMES 4 RENT",
         "fundamental": {"avg10DaysVolume": 2100000, "avg1YearVolume": 1850000}}}
```

`tests/engine/unit/test_market.py`:

```python
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from tc.broker.fake import FakeBroker
from tc.broker.market import ChainQuote

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = __import__("datetime").datetime(2026, 9, 8, 14, 0, tzinfo=__import__("datetime").UTC)


async def test_option_quote_reads_greeks_oi_and_spread() -> None:
    q = await FakeBroker(FIX, NOW).option_quote("AMH", date(2026, 10, 16), D("30"), "C")
    assert q is not None
    assert q.option_symbol == "AMH   261016C00030000"
    assert q.delta == D("0.62") and q.open_interest == 1240
    assert q.mid == D("5.25")
    # (5.40 - 5.10) / 5.25 = 5.71% — inside the §3.2 10% ceiling.
    assert q.spread_pct_of_mid == D("5.71")
    assert q.underlying_price == D("34.79")


async def test_option_quote_is_none_for_a_strike_the_chain_does_not_carry() -> None:
    assert await FakeBroker(FIX, NOW).option_quote("AMH", date(2026, 10, 16), D("31"), "C") is None


async def test_fundamental_reports_average_volume_and_exchange() -> None:
    f = await FakeBroker(FIX, NOW).fundamental("AMH")
    assert f is not None and f.exchange == "NYSE" and f.avg_daily_volume == 2100000
```

- [ ] **Step 6: Implement `engine/tc/broker/market.py` and the two impls**

`ChainQuote.spread_pct_of_mid` = `cents((ask - bid) / mid * 100)`; `mid` = `cents((bid + ask) / 2)`; a zero mid returns `Decimal("100")` (an unquotable contract fails the §3.2 spread floor rather than dividing by zero). `avg_daily_volume` prefers `avg10DaysVolume`, falling back to `avg1YearVolume`, so a newly listed name is not silently zero.

`SchwabBroker.option_quote` calls
`get_option_chain(underlying, contract_type=CALL|PUT, strategy=SINGLE, from_date=expiry, to_date=expiry, include_underlying_quote=True)` and walks `callExpDateMap`/`putExpDateMap`, matching the strike as a `Decimal` (the map keys are strings like `"30.0"`, so compare `D(key) == strike`, never the raw string). `SchwabBroker.fundamental` calls `get_instruments([symbol], projection=Client.Instrument.Projection.FUNDAMENTAL)`.

`FakeBroker` reads `chain-<UNDERLYING>.json` and `fundamental-<SYMBOL>.json` through its existing `_load`, so the `unauthorized` marker still turns every read into `BrokerUnauthorized`.

- [ ] **Step 7: Implement `FakeBroker`'s write surface (scripted fills)**

```python
    # Paper mode (spec §10). Placed orders live in memory and fill according
    # to fills.json: {"<SYMBOL>:<INSTRUCTION>": {"after_polls": 1, "filled": 29,
    #                 "price": "34.79"}}. A symbol with no entry never fills,
    #  which is how the "working entry cancelled at 15:55" path is tested.
    NEXT_ORDER_ID = 1000000000001
```

`place_order` assigns `self._next_id` (starting at `1000000000001`), stores the spec, sets `last_location_header` to a synthetic `Location`, and returns the id. `get_order` returns an `OrderRow` built from the stored spec, advancing `filledQuantity` on each call once `after_polls` polls have happened. `cancel_order` marks the stored order `CANCELED`. `replace_order` cancels and stores a new one with a new id. `preview_order` returns `PreviewResult` from `preview-<kind>.json`, or a computed `ACCEPTED` result when no fixture exists.

- [ ] **Step 8: Gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/broker/writer.py engine/tc/broker/market.py engine/tc/broker/client.py \
        engine/tc/broker/fake.py tests/engine/unit/test_writer.py \
        tests/engine/unit/test_market.py tests/engine/fixtures/broker
git commit -m "engine: writes are their own protocol, and an order id that did not arrive is unknown, not absent"
```

---

### Task 2: `tc/orders/intent.py` — the proposal schema and `client_key`

**Files:**
- Create: `engine/tc/orders/__init__.py`, `engine/tc/orders/intent.py`
- Test: `tests/engine/unit/test_intent.py`

**Interfaces:**
- Produces:
  - `Instrument = Literal["equity", "etf", "leveraged_etf", "option"]`, `Sleeve = Literal["core", "catalyst", "options", "leveraged"]`, `OrderKind = Literal["entry", "exit", "stop", "stop_replace", "cancel", "option_close", "forced_close"]`.
  - `OptionSpec(expiry: date, strike: Decimal, kind: Literal["C","P"])`.
  - `EntryIntent(instrument, symbol, option: OptionSpec | None, quantity: PositiveInt, limit_price: Decimal, intent_notional: Decimal, sleeve, thesis_ref: str, rationale: str (≤1200), earnings_date: date | None, corporate_action_note: str | None)` — spec §5.1 verbatim.
  - `ExitIntent(position_id: str, quantity: PositiveInt, limit_price: Decimal, reason: str (≤600))`.
  - `client_key(*parts: str) -> str` — `sha256` hex of `"\x1f".join(parts)`.
  - `entry_key(session_date, symbol, side, thesis_ref)`, `stop_key(session_date, symbol, position_key, attempt)`, `stop_replace_key(session_date, symbol, stop_id, replace_no)`, `cancel_key(session_date, symbol, order_id)`.

Engine-assigned, never Claude-supplied (spec §5.1): side (entries are BUY only), time in force, stop geometry, the OSI symbol, and the client key. The intent models therefore carry **no** `side`, `duration`, `stop_trigger` or `option_symbol` field, and `extra="forbid"` makes supplying one a validation error rather than a silently ignored suggestion.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from tc.orders.intent import (
    EntryIntent, ExitIntent, OptionSpec, client_key, entry_key, stop_key,
)


def _entry(**over: object) -> EntryIntent:
    base: dict[str, object] = dict(
        instrument="equity", symbol="AMH", option=None, quantity=29,
        limit_price=D("34.79"), intent_notional=D("1008.91"), sleeve="catalyst",
        thesis_ref="esc-2026-09-08-1", rationale="post-print drift, gapped and held",
        earnings_date=date(2026, 11, 5), corporate_action_note=None,
    )
    base.update(over)
    return EntryIntent(**base)  # type: ignore[arg-type]


def test_entry_intent_round_trips() -> None:
    e = _entry()
    assert e.quantity == 29 and e.limit_price == D("34.79")


def test_side_and_symbol_are_engine_assigned_not_claude_supplied() -> None:
    with pytest.raises(ValidationError):
        _entry(side="BUY")
    with pytest.raises(ValidationError):
        _entry(option_symbol="AMH   261016C00030000")


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _entry(quantity=0)


def test_rationale_is_capped_at_1200() -> None:
    with pytest.raises(ValidationError):
        _entry(rationale="x" * 1201)


def test_option_entry_requires_an_option_spec() -> None:
    with pytest.raises(ValidationError):
        _entry(instrument="option")
    ok = _entry(instrument="option", option=OptionSpec(expiry=date(2026, 10, 16), strike=D("30"), kind="C"))
    assert ok.option is not None and ok.option.kind == "C"


def test_equity_entry_refuses_an_option_spec() -> None:
    with pytest.raises(ValidationError):
        _entry(option=OptionSpec(expiry=date(2026, 10, 16), strike=D("30"), kind="C"))


def test_exit_intent_reason_is_capped_at_600() -> None:
    ExitIntent(position_id="AMH", quantity=29, limit_price=D("35.10"), reason="stall rule")
    with pytest.raises(ValidationError):
        ExitIntent(position_id="AMH", quantity=29, limit_price=D("35.10"), reason="x" * 601)


def test_client_key_is_deterministic_and_field_separated() -> None:
    a = client_key("2026-09-08", "AMH", "BUY", "esc-1")
    assert a == client_key("2026-09-08", "AMH", "BUY", "esc-1")
    assert a != client_key("2026-09-08", "AMH", "BUY", "esc-2")
    # No "AB"+"C" == "A"+"BC" collision: the parts are separated, not joined.
    assert client_key("AB", "C") != client_key("A", "BC")
    assert len(a) == 64


def test_protective_keys_vary_by_attempt_so_a_retry_is_a_new_row() -> None:
    assert stop_key("2026-09-08", "AMH", "AMH", 1) != stop_key("2026-09-08", "AMH", "AMH", 2)
    assert entry_key("2026-09-08", "AMH", "BUY", "esc-1") == client_key(
        "2026-09-08", "AMH", "BUY", "esc-1"
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_intent.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.orders'`.

- [ ] **Step 3: Implement**

```python
"""What Claude may ask for — and only that (spec §5.1).

Every field the engine assigns is ABSENT here, and `extra="forbid"` turns a
supplied one into a validation error rather than a suggestion the engine
silently ignores: side (entries are BUY, exits SELL/SELL_TO_CLOSE), duration
(§4.2 DAY for entries, GTC for stops), the §3.4 stop geometry, the OSI option
symbol (built only by `OptionSymbol`, CLAUDE.md §4.10) and the client key.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, model_validator

Instrument = Literal["equity", "etf", "leveraged_etf", "option"]
Sleeve = Literal["core", "catalyst", "options", "leveraged"]
OrderKind = Literal[
    "entry", "exit", "stop", "stop_replace", "cancel", "option_close", "forced_close"
]
SEP = "\x1f"


class OptionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiry: date
    strike: Decimal
    kind: Literal["C", "P"]


class EntryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument: Instrument
    symbol: str                      # underlying ticker only
    option: OptionSpec | None
    quantity: PositiveInt
    limit_price: Decimal
    intent_notional: Decimal         # Claude's own arithmetic; the engine compares
    sleeve: Sleeve
    thesis_ref: str
    rationale: str
    earnings_date: date | None
    corporate_action_note: str | None

    @model_validator(mode="after")
    def _option_spec_matches_instrument(self) -> EntryIntent:
        if len(self.rationale) > 1200:
            raise ValueError("rationale exceeds 1200 characters")
        if (self.instrument == "option") != (self.option is not None):
            raise ValueError("instrument 'option' requires an option spec, and only it may carry one")
        return self


class ExitIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_id: str
    quantity: PositiveInt
    limit_price: Decimal
    reason: str

    @model_validator(mode="after")
    def _reason_length(self) -> ExitIntent:
        if len(self.reason) > 600:
            raise ValueError("reason exceeds 600 characters")
        return self


def client_key(*parts: str) -> str:
    """`sha256` over separator-joined parts (spec §5.1). The separator is what
    keeps ("AB","C") and ("A","BC") distinct — an idempotency key that can
    collide is worse than none."""
    return hashlib.sha256(SEP.join(parts).encode()).hexdigest()


def entry_key(session_date: str, symbol: str, side: str, thesis_ref: str) -> str:
    return client_key(session_date, symbol, side, thesis_ref)


def stop_key(session_date: str, symbol: str, position_key: str, attempt: int) -> str:
    # The attempt number is part of the key: attempt 2 is a NEW order and must
    # not collide with attempt 1's row, while a retry OF attempt 1 reuses the
    # key and hits the UNIQUE constraint — which is the duplicate-submit guard.
    return client_key(session_date, symbol, "SELL", f"stop:{position_key}:{attempt}")


def stop_replace_key(session_date: str, symbol: str, stop_id: int, replace_no: int) -> str:
    return client_key(session_date, symbol, "SELL", f"stop_replace:{stop_id}:{replace_no}")


def cancel_key(session_date: str, symbol: str, order_id: int) -> str:
    return client_key(session_date, symbol, "CANCEL", f"cancel:{order_id}")
```

`rationale`/`reason` length is enforced in the validator rather than with `constr` so the error text names the rule; the spec's `constr(max_length=…)` is satisfied either way.

- [ ] **Step 4: Run to verify it passes, then gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/orders/__init__.py engine/tc/orders/intent.py tests/engine/unit/test_intent.py
git commit -m "engine: an intent carries only what Claude may decide; side, duration and the OSI symbol are the engine's"
```

---

### Task 3: `tc/orders/specs.py` — order specs built in Python

**Files:**
- Create: `engine/tc/orders/specs.py`
- Test: `tests/engine/unit/test_specs.py`

**Interfaces:**
- Consumes: `schwab.orders.generic.OrderBuilder`, `schwab.orders.common` enums, `schwab.orders.options.OptionSymbol`, `schwab.orders.equities.equity_buy_limit/equity_sell_limit`, `schwab.orders.options.option_buy_to_open_limit/option_sell_to_close_limit`, `tc.orders.intent`.
- Produces:
  - `OrderSpec(symbol, underlying, asset_type, instruction, order_type, duration, session, quantity, limit_price, stop_price, payload: dict)` with `multiplier`, `notional` and `order_value` properties, and a validator that the typed fields agree with `payload`.
  - `osi_symbol(underlying: str, spec: OptionSpec) -> str`.
  - `equity_buy_limit_day(symbol, qty, limit) -> OrderSpec`
  - `equity_sell_limit_day(symbol, qty, limit) -> OrderSpec`
  - `equity_sell_market(symbol, qty) -> OrderSpec` — the ONE permitted market order (§4.1(b), a gapped-through stop)
  - `gtc_stop_limit_sell(symbol, qty, trigger, limit) -> OrderSpec`
  - `option_buy_to_open_limit(osi, qty, limit, underlying) -> OrderSpec`
  - `option_sell_to_close_limit(osi, qty, limit, underlying) -> OrderSpec`
  - `is_protective_stop(spec: OrderSpec, held: int) -> bool`

Why the stop is hand-built: **schwab-py has no stop template of any kind** — `schwab/orders/equities.py` ships eight functions and none is a stop, and every template hard-codes `Duration.DAY` (research §3.1). The §3.4 GTC stop-limit is therefore `OrderBuilder` directly. Prices are passed as **strings**: a non-`str` goes through `truncate_float()`, which emits a `DeprecationWarning` and truncates (`generic.py:37-44`).

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from tc.orders.intent import OptionSpec
from tc.orders.specs import (
    equity_buy_limit_day, equity_sell_limit_day, equity_sell_market,
    gtc_stop_limit_sell, is_protective_stop, option_buy_to_open_limit,
    option_sell_to_close_limit, osi_symbol,
)


def test_equity_buy_is_a_day_limit_single_normal() -> None:
    s = equity_buy_limit_day("AMH", 29, D("34.79"))
    assert s.payload["orderType"] == "LIMIT"
    assert s.payload["duration"] == "DAY"          # §4.2: never a GTC buy
    assert s.payload["session"] == "NORMAL"
    assert s.payload["orderStrategyType"] == "SINGLE"
    assert s.payload["price"] == "34.79"           # a STRING, never a float
    leg = s.payload["orderLegCollection"][0]
    assert leg["instruction"] == "BUY" and leg["quantity"] == 29
    assert leg["instrument"] == {"assetType": "EQUITY", "symbol": "AMH"}
    assert s.notional == D("1008.91") and s.multiplier == 1


def test_stop_limit_is_gtc_sell_with_both_prices_as_strings() -> None:
    s = gtc_stop_limit_sell("AMH", 29, D("32.01"), D("30.40"))
    assert s.payload["orderType"] == "STOP_LIMIT"
    assert s.payload["duration"] == "GOOD_TILL_CANCEL"
    assert s.payload["stopPrice"] == "32.01" and s.payload["price"] == "30.40"
    assert s.payload["orderLegCollection"][0]["instruction"] == "SELL"
    # The preview values a stop-limit at the LIMIT, not the trigger
    # (status/2026-08-24.md:127): 29 x 30.40 = 881.60.
    assert s.order_value == D("881.60")


def test_osi_symbol_is_built_by_schwab_py_not_typed() -> None:
    assert osi_symbol("F", OptionSpec(expiry=date(2026, 9, 18), strike=D("14"), kind="C")) == (
        "F     260918C00014000"
    )
    assert osi_symbol("AMH", OptionSpec(expiry=date(2026, 10, 16), strike=D("30"), kind="C")) == (
        "AMH   261016C00030000"
    )


def test_option_orders_are_buy_to_open_and_sell_to_close_only() -> None:
    osi = "AMH   261016C00030000"
    b = option_buy_to_open_limit(osi, 1, D("5.25"), "AMH")
    c = option_sell_to_close_limit(osi, 1, D("6.00"), "AMH")
    assert b.payload["orderLegCollection"][0]["instruction"] == "BUY_TO_OPEN"
    assert c.payload["orderLegCollection"][0]["instruction"] == "SELL_TO_CLOSE"
    assert b.payload["orderLegCollection"][0]["instrument"]["assetType"] == "OPTION"
    assert b.multiplier == 100 and b.notional == D("525.00")   # premium dollars
    assert b.underlying == "AMH"


def test_market_sell_exists_only_for_the_gapped_stop_exit() -> None:
    s = equity_sell_market("AMH", 29)
    assert s.payload["orderType"] == "MARKET" and s.payload["duration"] == "DAY"
    assert s.limit_price is None


def test_spec_refuses_a_payload_that_disagrees_with_its_typed_fields() -> None:
    s = equity_buy_limit_day("AMH", 29, D("34.79"))
    with pytest.raises(ValidationError):
        s.model_copy(update={"quantity": 30}).model_validate(s.model_dump())


@pytest.mark.parametrize(
    "spec,held,expected",
    [
        (gtc_stop_limit_sell("AMH", 29, D("32.01"), D("30.40")), 29, True),
        (gtc_stop_limit_sell("AMH", 29, D("32.01"), D("30.40")), 28, False),  # qty > held
        (equity_sell_limit_day("AMH", 29, D("35.10")), 29, False),            # not a stop
        (equity_buy_limit_day("AMH", 29, D("34.79")), 29, False),             # a BUY
        (option_sell_to_close_limit("AMH   261016C00030000", 1, D("6.00"), "AMH"), 1, False),
    ],
)
def test_is_protective_stop_matches_only_a_covered_equity_sell_stop(
    spec: object, held: int, expected: bool
) -> None:
    from tc.orders.specs import OrderSpec
    assert is_protective_stop(OrderSpec.model_validate(spec), held) is expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_specs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.orders.specs'`.

- [ ] **Step 3: Implement**

```python
"""Order payloads, built in Python and validated before they are sent.

schwab-py ships templates for equity and option LIMIT orders but **no stop
template of any kind** (schwab/orders/equities.py, complete list at research
§3.1), and every template hard-codes Duration.DAY — so the §3.4 GTC
stop-limit is built by hand with OrderBuilder. Prices go in as STRINGS: a
non-str goes through truncate_float(), which deprecation-warns and truncates
(generic.py:37-44), and money that has been through a float is money the
three-way compare can no longer trust.

Nothing here forbidden by CLAUDE.md exists as a function: no SELL_TO_OPEN, no
BUY_TO_CLOSE, no SELL_SHORT, no vertical. §1.2, §1.3 and §1.5 are enforced by
the absence of the builder, not by a check on the way past one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, model_validator
from schwab.orders.common import (
    Duration, EquityInstruction, OptionInstruction, OrderStrategyType, OrderType, Session,
)
from schwab.orders.generic import OrderBuilder
from schwab.orders.options import OptionSymbol

from tc.money import cents
from tc.orders.intent import OptionSpec

STOP_ORDER_TYPES = {"STOP", "STOP_LIMIT"}


def _price(d: Decimal) -> str:
    """schwab-py wants a string; `str(Decimal)` is exact and keeps the cents."""
    return str(d)


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str                       # OSI for options, ticker for equities
    underlying: str                   # the ticker either way — §1.4 compares this for options
    asset_type: Literal["EQUITY", "OPTION"]
    instruction: Literal["BUY", "SELL", "BUY_TO_OPEN", "SELL_TO_CLOSE"]
    order_type: Literal["LIMIT", "STOP_LIMIT", "MARKET"]
    duration: Literal["DAY", "GOOD_TILL_CANCEL"]
    session: Literal["NORMAL"]
    quantity: PositiveInt
    limit_price: Decimal | None
    stop_price: Decimal | None
    payload: dict[str, Any]

    @property
    def multiplier(self) -> int:
        return 100 if self.asset_type == "OPTION" else 1

    @property
    def notional(self) -> Decimal:
        """qty x price x multiplier — the §4.10 three-way's own arithmetic and
        the §5 settled-cash figure (pre-order-check.sh:243-245)."""
        if self.limit_price is None:
            raise ValueError("a market order has no notional to compare")
        return cents(Decimal(self.quantity) * self.limit_price * self.multiplier)

    @property
    def order_value(self) -> Decimal:
        """What the PREVIEW values this at: qty x the limit price, which for a
        stop-limit is the limit and not the trigger (status/2026-08-24.md:127)."""
        return self.notional

    @model_validator(mode="after")
    def _payload_agrees(self) -> OrderSpec:
        legs = self.payload.get("orderLegCollection") or []
        if len(legs) != 1:
            raise ValueError("every permitted order is single-leg (§1.3: no spreads)")
        leg = legs[0]
        if (
            self.payload.get("orderType") != self.order_type
            or self.payload.get("duration") != self.duration
            or self.payload.get("session") != self.session
            or leg.get("instruction") != self.instruction
            or leg.get("quantity") != self.quantity
            or (leg.get("instrument") or {}).get("symbol") != self.symbol
            or (leg.get("instrument") or {}).get("assetType") != self.asset_type
        ):
            raise ValueError("payload disagrees with the typed fields")
        return self


def osi_symbol(underlying: str, option: OptionSpec) -> str:
    """CLAUDE.md §4.10: option symbols are built ONLY by the library, never
    typed. `strike_price_as_string` must be a str — a float or Decimal raises
    (schwab/orders/options.py:89-98)."""
    return OptionSymbol(underlying, option.expiry, option.kind, str(option.strike)).build()


def _spec(builder: OrderBuilder, **fields: Any) -> OrderSpec:
    return OrderSpec(payload=builder.build(), **fields)
```

Then the six builders. `equity_buy_limit_day` / `equity_sell_limit_day` wrap `schwab.orders.equities.equity_buy_limit` / `equity_sell_limit` (already DAY/NORMAL/SINGLE) after `.set_price(_price(limit))` so the price is a string; `option_buy_to_open_limit` / `option_sell_to_close_limit` wrap the option builders the same way; `equity_sell_market` wraps `equity_sell_market`. The stop is built directly:

```python
def gtc_stop_limit_sell(symbol: str, quantity: int, trigger: Decimal, limit: Decimal) -> OrderSpec:
    """CLAUDE.md §3.4's resting protective order. GOOD_TILL_CANCEL is the one
    place a non-DAY duration is permitted (§4.2 bars only GTC *buys*)."""
    b = (
        OrderBuilder()
        .set_order_type(OrderType.STOP_LIMIT)
        .set_stop_price(_price(trigger))
        .set_price(_price(limit))
        .set_session(Session.NORMAL)
        .set_duration(Duration.GOOD_TILL_CANCEL)
        .set_order_strategy_type(OrderStrategyType.SINGLE)
        .add_equity_leg(EquityInstruction.SELL, symbol, quantity)
    )
    return _spec(b, symbol=symbol, underlying=symbol, asset_type="EQUITY", instruction="SELL",
                 order_type="STOP_LIMIT", duration="GOOD_TILL_CANCEL", session="NORMAL",
                 quantity=quantity, limit_price=limit, stop_price=trigger)


def is_protective_stop(spec: OrderSpec, held: int) -> bool:
    """The auto-approve predicate (spec §5.4). Three clauses, and the third —
    `quantity <= held` — is the one the 2026-08-14 site-packages patch does
    NOT have (docs/archive/schwab-mcp-auto-approve-stops.md:38-53).

    Safety argument, from that same file: the account is CASH, so the broker
    rejects any SELL stop that would open a short — the bypass cannot create a
    position, only protect one. The held clause makes that structural rather
    than inherited from the broker's own refusal.
    """
    legs = spec.payload.get("orderLegCollection") or []
    return (
        spec.payload.get("orderType") in STOP_ORDER_TYPES
        and bool(legs)
        and all(
            leg.get("instruction") == "SELL"
            and (leg.get("instrument") or {}).get("assetType") == "EQUITY"
            for leg in legs
        )
        and spec.quantity <= held
    )
```

`DO_NOT_REDUCE` is deliberately **not** set on the stop: today's resting stops do not use it and Schwab's ex-dividend reduction is whitelisted by the drift detector in Task 10 instead (research §4.4, §7.9). Changing that is a strategy decision for Chris, not a refactor.

- [ ] **Step 4: Run to verify it passes, then gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/orders/specs.py tests/engine/unit/test_specs.py
git commit -m "engine: the forbidden order shapes have no builder, and the §3.4 stop is hand-built because schwab-py has none"
```

---

### Task 4: Store — the order-path tables and `OrderStore`

**Files:**
- Modify: `engine/tc/store/schema.sql`, `engine/tc/store/db.py` (`_transaction` → public `transaction`; two call sites)
- Create: `engine/tc/store/orders_db.py`
- Test: `tests/engine/unit/test_orders_db.py`

**Interfaces:**
- Consumes: `Store.execute/fetchone/fetchall/transaction`, `tc.orders.specs.OrderSpec`, `tc.orders.intent.OrderKind`.
- Produces:
  - `OrderState = Literal["proposed","validated","rejected","awaiting_approval","approved","denied","expired","placing","working","partially_filled","filled","cancelled","broker_rejected","stop_pending","stop_resting","stop_failed","closing","flat"]` (spec §5.3, exactly).
  - `ProposalRow`, `OrderRecord`, `OrderEventRow`, `StopRow`, `ApprovalRow`, `TradeLogRow` — pydantic, `extra="forbid"`.
  - `class OrderStore(store: Store)` with:
    `insert_proposal(p) -> None`; `proposal(proposal_id) -> ProposalRow | None`;
    `insert_order(o) -> None`; `order_by_key(client_key) -> OrderRecord | None`;
    `transition(client_key, to_state, event, actor, detail, **fields) -> OrderRecord` (one transaction: UPDATE `orders` + INSERT `order_events`);
    `orders_in_flight() -> list[OrderRecord]`; `placing_without_id() -> list[OrderRecord]`;
    `orders_for_symbol(symbol, session_date) -> list[OrderRecord]`;
    `events_for(client_key) -> list[OrderEventRow]`;
    `upsert_stop(s) -> int`; `stop_for(symbol) -> StopRow | None`; `stops_in_state(state) -> list[StopRow]`;
    `insert_approval(a) -> int`; `decide_approval(client_key, decision, approver_id, at) -> None`; `pending_approvals() -> list[ApprovalRow]`;
    `append_trade_log(row: TradeLogRow) -> None`; `trade_log_rows() -> list[TradeLogRow]`.

`proposals` is append-only and its `state` column is the **terminal state of validation** (`validated` or `rejected`), never the order's live state — the live state lives in `orders`. Spec §6 calls proposals append-only and also lists a `state` column; recording a moving state in an append-only table is the contradiction, and this is how it resolves.

- [ ] **Step 1: Write the failing store tests**

```python
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import AsyncIterator

import pytest

from tc.store.db import Store
from tc.store.orders_db import OrderRecord, OrderStore, StopRow, TradeLogRow

NOW = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)
KEY = "a" * 64
OID = 1000000000001


@pytest.fixture
async def os_(tmp_path: Path) -> AsyncIterator[OrderStore]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield OrderStore(s)
    await s.close()


def _order(**over: object) -> OrderRecord:
    base: dict[str, object] = dict(
        client_key=KEY, proposal_id=None, parent_order_id=None,
        account_hash="HASH_REDACTED", order_id=None, state="placing", kind="entry",
        is_protective=False, spec_json="{}", order_type="LIMIT", duration="DAY",
        session="NORMAL", symbol="AMH", asset_type="EQUITY", side="BUY", quantity=29,
        limit_price=D("34.79"), stop_price=None, filled_quantity=0, avg_fill_price=None,
        preview_order_value=D("1008.91"), preview_commission=D("0"), preview_fees_json="{}",
        placing_at=NOW, placed_at=None, entered_at=None, closed_at=None,
        location_header=None, attempts=1, ceiling_hit=False,
        session_date="2026-09-08", error=None,
    )
    base.update(over)
    return OrderRecord(**base)  # type: ignore[arg-type]


async def test_client_key_is_unique(os_: OrderStore) -> None:
    await os_.insert_order(_order())
    with pytest.raises(Exception):
        await os_.insert_order(_order())


async def test_transition_updates_the_order_and_appends_an_event(os_: OrderStore) -> None:
    await os_.insert_order(_order())
    row = await os_.transition(KEY, "working", "place_confirmed", "engine",
                               {"http": 201}, order_id=OID, placed_at=NOW)
    assert row.state == "working" and row.order_id == OID
    events = await os_.events_for(KEY)
    assert [e.event for e in events] == ["place_confirmed"]
    assert events[0].from_state == "placing" and events[0].to_state == "working"


async def test_order_events_is_append_only(os_: OrderStore) -> None:
    await os_.insert_order(_order())
    await os_.transition(KEY, "working", "place_confirmed", "engine", {})
    with pytest.raises(Exception):
        await os_.store.execute("UPDATE order_events SET event='x'")
    with pytest.raises(Exception):
        await os_.store.execute("DELETE FROM order_events")


async def test_placing_without_id_is_what_startup_resolves(os_: OrderStore) -> None:
    await os_.insert_order(_order())
    assert [o.client_key for o in await os_.placing_without_id()] == [KEY]
    await os_.transition(KEY, "working", "id_resolved_by_query", "engine", {}, order_id=OID)
    assert await os_.placing_without_id() == []


async def test_orders_in_flight_is_the_one_action_invariant(os_: OrderStore) -> None:
    await os_.insert_order(_order(state="working"))
    await os_.insert_order(_order(client_key="b" * 64, state="filled"))
    assert [o.client_key for o in await os_.orders_in_flight()] == [KEY]


async def test_orders_for_symbol_is_scoped_to_the_session(os_: OrderStore) -> None:
    await os_.insert_order(_order(state="filled"))
    await os_.insert_order(_order(client_key="b" * 64, session_date="2026-09-07"))
    got = await os_.orders_for_symbol("AMH", "2026-09-08")
    assert [o.client_key for o in got] == [KEY]


async def test_stop_round_trips_with_its_ratchet_and_replace_counters(os_: OrderStore) -> None:
    sid = await os_.upsert_stop(StopRow(
        id=None, symbol="AMH", position_key="AMH", order_id=OID, client_key=KEY,
        state="resting", quantity=29, trigger_price=D("32.01"), limit_price=D("30.40"),
        entry_price=D("34.79"), atr_pct_at_entry=D("1.89"), trigger_pct=D("8"),
        ratchet_stage="none", replaces_today=0, replaces_reset_date="2026-09-08",
        attempts=1, placed_at=NOW, verified_at=NOW, naked_since=None, adjustment_note=None,
    ))
    got = await os_.stop_for("AMH")
    assert got is not None and got.id == sid and got.trigger_price == D("32.01")


async def test_trade_log_carries_the_export_columns_and_stays_append_only(os_: OrderStore) -> None:
    await os_.append_trade_log(TradeLogRow(
        date="2026-09-08", time_et="10:08", action="PRETRADE", symbol="AMH",
        instrument="equity", quantity=29, limit_price="34.79", fill_price="-",
        gross="-", fees="-", net="-", pct_of_comp_capital_after="-",
        stop_trigger="32.01", stop_limit="30.40", settled_cash_before="1500.00",
        account_value_after="-", reserve="900.00", comp_capital_after="-",
        high_water_mark="3800.00", drawdown_pct="-1.88", cum_option_premium="0.00",
        rule_check="§3.1 ok; §5 ok", rationale="post-print drift", order_id=None,
    ))
    rows = await os_.trade_log_rows()
    assert rows[0].rule_check.startswith("§3.1")
    with pytest.raises(Exception):
        await os_.store.execute("UPDATE trade_log SET action='x'")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_orders_db.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tc.store.orders_db'`.

- [ ] **Step 3: Extend `schema.sql`**

Append (the whole file is already idempotent and applied by `Store.open()`):

```sql
-- ---- Plan 1: the order path (spec §6) --------------------------------------
-- Money is TEXT holding Decimal strings; timestamps ISO TEXT; booleans INTEGER.
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  proposal_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, session_date TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('entry','exit','option_close','stop_place','stop_amend','cancel')),
  agent TEXT NOT NULL, intent_json TEXT NOT NULL,
  instrument TEXT NOT NULL CHECK (instrument IN ('equity','etf','leveraged_etf','option')),
  symbol TEXT NOT NULL, option_symbol TEXT, side TEXT NOT NULL, quantity INTEGER NOT NULL,
  limit_price TEXT NOT NULL, intent_notional TEXT NOT NULL, sleeve TEXT NOT NULL,
  thesis_ref TEXT NOT NULL, rationale TEXT NOT NULL,
  earnings_date TEXT, corporate_action_note TEXT,
  accepted INTEGER NOT NULL, gates_json TEXT NOT NULL,
  -- The TERMINAL state of validation ('validated' or 'rejected'), never the
  -- order's live state: this table is append-only and a moving value cannot
  -- live in it. The live state is orders.state.
  state TEXT NOT NULL, client_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  client_key TEXT NOT NULL UNIQUE,          -- written BEFORE the HTTP call (§5.5)
  proposal_id TEXT REFERENCES proposals(proposal_id),
  parent_order_id INTEGER, account_hash TEXT NOT NULL, order_id INTEGER,
  state TEXT NOT NULL CHECK (state IN ('proposed','validated','rejected','awaiting_approval',
    'approved','denied','expired','placing','working','partially_filled','filled','cancelled',
    'broker_rejected','stop_pending','stop_resting','stop_failed','closing','flat')),
  kind TEXT NOT NULL CHECK (kind IN ('entry','exit','stop','stop_replace','cancel','option_close','forced_close')),
  is_protective INTEGER NOT NULL DEFAULT 0, spec_json TEXT NOT NULL,
  order_type TEXT NOT NULL, duration TEXT NOT NULL, session TEXT NOT NULL,
  symbol TEXT NOT NULL, asset_type TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL,
  limit_price TEXT, stop_price TEXT, filled_quantity INTEGER NOT NULL DEFAULT 0,
  avg_fill_price TEXT, preview_order_value TEXT, preview_commission TEXT, preview_fees_json TEXT,
  placing_at TEXT, placed_at TEXT, entered_at TEXT, closed_at TEXT, location_header TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, ceiling_hit INTEGER NOT NULL DEFAULT 0,
  session_date TEXT NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS orders_symbol_session ON orders(symbol, session_date);
CREATE INDEX IF NOT EXISTS orders_state ON orders(state);
CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, client_key TEXT NOT NULL, order_id INTEGER,
  from_state TEXT, to_state TEXT, event TEXT NOT NULL, detail_json TEXT NOT NULL,
  actor TEXT NOT NULL CHECK (actor IN ('engine','claude','broker','approver'))
);
CREATE INDEX IF NOT EXISTS order_events_key ON order_events(client_key, id);
CREATE TABLE IF NOT EXISTS stops (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, position_key TEXT NOT NULL UNIQUE,
  order_id INTEGER, client_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','resting','replaced','consumed','cancelled','orphaned','failed')),
  quantity INTEGER NOT NULL, trigger_price TEXT NOT NULL, limit_price TEXT NOT NULL,
  entry_price TEXT NOT NULL, atr_pct_at_entry TEXT NOT NULL, trigger_pct TEXT NOT NULL,
  ratchet_stage TEXT NOT NULL CHECK (ratchet_stage IN ('none','breakeven','entry_plus_8')),
  replaces_today INTEGER NOT NULL DEFAULT 0, replaces_reset_date TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, placed_at TEXT, verified_at TEXT,
  naked_since TEXT, adjustment_note TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY, client_key TEXT NOT NULL, requested_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, channel_id TEXT NOT NULL, message_id TEXT, summary TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('discord','auto_protective')), auto_reason TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('pending','approved','denied','expired')),
  decided_at TEXT, approver_id TEXT, rejected_reactions_json TEXT NOT NULL DEFAULT '[]',
  latency_s INTEGER
);
CREATE INDEX IF NOT EXISTS approvals_pending ON approvals(decision, expires_at);

CREATE TRIGGER IF NOT EXISTS proposals_no_update BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT, 'proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposals_no_delete BEFORE DELETE ON proposals BEGIN SELECT RAISE(ABORT, 'proposals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_events_no_update BEFORE UPDATE ON order_events BEGIN SELECT RAISE(ABORT, 'order_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_events_no_delete BEFORE DELETE ON order_events BEGIN SELECT RAISE(ABORT, 'order_events is append-only'); END;
```

`trade_log` already exists with eight columns and its append-only triggers. The nineteen export columns are added by an idempotent migration in `Store.open()` (SQLite has no `ADD COLUMN IF NOT EXISTS`, so read `PRAGMA table_info` first):

```python
TRADE_LOG_ADDED: tuple[str, ...] = (
    "date", "time_et", "instrument", "limit_price", "fill_price", "gross", "fees", "net",
    "pct_of_comp_capital_after", "stop_trigger", "stop_limit", "settled_cash_before",
    "account_value_after", "reserve", "comp_capital_after", "high_water_mark",
    "drawdown_pct", "cum_option_premium", "rule_check", "rationale",
)

    async def _migrate_trade_log(self) -> None:
        """The §7.1 log's 23 export columns (trade-log-append.sh:19-23), added
        to the Phase 0a table rather than replacing it: the table is
        append-only by trigger and dropping it to widen it would destroy the
        audit trail the trigger exists to protect."""
        rows = await self.fetchall("PRAGMA table_info(trade_log)")
        have = {r["name"] for r in rows}
        for col in TRADE_LOG_ADDED:
            if col not in have:
                await self.execute(f"ALTER TABLE trade_log ADD COLUMN {col} TEXT")  # noqa: S608
```

Called from `Store.open()` after `executescript(schema)`. Bump `SCHEMA_VERSION` to `2`. Rename `Store._transaction` → `Store.transaction` and update its two call sites (`record_account`, `record_orders`).

- [ ] **Step 4: Implement `orders_db.py`**

`OrderStore` holds `self.store: Store` (public, so tests can assert on triggers) and nothing else. `transition` is one `store.transaction()` block: `SELECT state FROM orders WHERE client_key=?` → `UPDATE orders SET state=?, <fields> WHERE client_key=?` → `INSERT INTO order_events(...)` with `from_state`/`to_state`. A `client_key` with no row raises `KeyError` — a transition on an order that was never written is the §5.5 invariant broken, not a no-op. Decimals go in as `str(...)` and come back through the model's `Decimal` fields (pydantic coerces the strings).

- [ ] **Step 5: Run to verify it passes, then gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/store/schema.sql engine/tc/store/db.py engine/tc/store/orders_db.py \
        tests/engine/unit/test_orders_db.py
git commit -m "engine: the order ledger is five tables, an append-only transition log, and a client key written before the call"
```

---

### Task 5: `tc/orders/snapshot.py` — the world a gate is allowed to see

**Files:**
- Create: `engine/tc/orders/snapshot.py`
- Test: `tests/engine/unit/test_snapshot.py`

**Interfaces:**
- Consumes: `BookView`, `MarketWindow`, `Quote`, `AlertRow`, `ChainQuote`, `InstrumentFundamental`, `OrderRecord`, `Broker`, `MarketReader`, `Store`, `OrderStore`, `tc.clock.phase_for`.
- Produces:
  - `CorrelationView(cluster_of: dict[str, str], exposure: dict[str, Decimal])` — symbol → cluster key, cluster key → dollars already in it.
  - `BookSnapshot` (all fields below) and
    `async build_snapshot(*, broker, market, store, order_store, view, window, now, session_date, rules, reserve, hwm, symbol, underlying, option: OptionSpec | None, leveraged: set[str], correlation: CorrelationView | None) -> BookSnapshot`.

```python
class BookSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    now: datetime                     # aware; the BROKER's clock, never the machine's
    session_date: str
    phase: SessionPhase
    window: MarketWindow
    view: BookView
    hwm: Decimal
    reserve: Decimal
    quote: Quote | None               # of `underlying`
    security_status: str | None       # §3.7 halt gate: "Normal" or it is not tradable
    fundamental: InstrumentFundamental | None
    chain: ChainQuote | None
    underlying_price: Decimal | None
    orders_today: list[OrderRecord]   # engine rows for this symbol this session
    broker_orders_today: list[OrderRow]  # the same window read from the broker
    replaces_today: int
    open_alerts: list[AlertRow]
    correlation: CorrelationView | None
    existing_position_value: Decimal
    existing_option_premium: Decimal
    open_option_premium: Decimal
    leveraged_aggregate: Decimal
    held_quantity: int
```

Why a snapshot at all: spec §5.2 says the gates are **pure functions over `Rules` and a `BookSnapshot`**. Every network read therefore happens here, once, and a gate never awaits anything. That is what makes the gate suite testable without a broker and what makes the recorded gate numbers reproducible from the stored snapshot.

`orders_today` ∪ `broker_orders_today` is the §4.10 counting basis — `trader.md:116`: counts are "derived from `get_orders`, never from memory". The engine's own rows are the second half because an order the engine placed a second ago may not be in the broker's list yet.

- [ ] **Step 1: Write the failing tests**

Cover, with `FakeBroker` on the existing fixture directory and a seeded `Store`:
- a snapshot for `AMH` carries `held_quantity == 29`, `existing_position_value == 948.59` (the fixture's `marketValue`) and `phase == "RTH"` at 14:00 UTC on a fixture trading day;
- `security_status` comes from the quote payload's `securityStatus` and is `None` when the symbol did not quote;
- `underlying_price` is the underlying's `last` for an equity and the chain's `underlyingPrice` for an option;
- `open_option_premium` sums `abs(quantity) * average_price * 100` over `asset_type == "OPTION"` positions and is `0` on an equity-only book;
- `leveraged_aggregate` sums `market_value` over positions whose symbol (or whose OSI underlying) is in `leveraged`;
- `orders_today` excludes a stored order with a different `session_date`, and `broker_orders_today` excludes a broker order entered before today;
- `correlation=None` passes straight through (Task 6's gate turns it into pass-with-note).

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError: No module named 'tc.orders.snapshot'`.

- [ ] **Step 3: Implement.** `build_snapshot` performs at most four broker reads — `quotes([underlying])`, `market.fundamental(underlying)`, `market.option_quote(...)` when `option is not None`, and `broker.orders(hash, midnight_et, now+1d)` — and never raises on a missing one: an absent chain or fundamental is `None`, and the gate that needs it fails with the reason "no chain read", which is the correct answer to "may I place this option order?".

- [ ] **Step 4: Gate and commit** — `git add engine/tc/orders/snapshot.py tests/engine/unit/test_snapshot.py` — "engine: every gate input is read once, up front, so a gate is arithmetic and not a network call".

---

### Task 6: `tc/orders/gates.py` I — floors, caps, posture

**Files:**
- Create: `engine/tc/orders/gates.py` (the pure half; Task 7 completes the module)
- Modify: `engine/tc/rules/model.py` (four accessors)
- Test: `tests/engine/unit/test_gates_pure.py`, `tests/engine/property/test_gate_props.py`

**Interfaces:**
- Produces:
  - `GateResult(name: str, passed: bool, detail: str, numbers: dict[str, str])`.
  - `GateContext` — frozen dataclass (not pydantic: it carries `Rules`, a frozen dataclass, and is never serialised as a unit) with `rules: Rules`, `snapshot: BookSnapshot`, `spec: OrderSpec`, `kind: OrderKind`, `intent: EntryIntent | ExitIntent | None`, `preview: PreviewResult | None`, `orders_enabled: bool`.
  - `is_buy(ctx) -> bool`, and the gates:
    `gate_price_floor`, `gate_instrument_permitted`, `gate_liquidity_floor`, `gate_position_cap`, `gate_option_caps`, `gate_leveraged_cap`, `gate_correlation_cap`, `gate_drawdown_halt`, `gate_settled_cash`, `gate_reserve_invariant`, `gate_earnings_recorded`, `gate_corporate_actions_recorded`, `gate_entry_cutoff`, `gate_alert_posture` — each `(ctx: GateContext) -> GateResult`.
  - `Rules.option_min_delta`, `Rules.option_max_delta`, `Rules.option_min_open_interest`, `Rules.option_max_spread_pct_of_mid`, `Rules.min_avg_daily_dollar_volume`, `Rules.min_avg_daily_volume`, `Rules.correlation_cap_pct` accessors.

The gate order is spec §5.2's, exactly, and it is fixed in Task 7's `GATE_ORDER`. Note the deviation from the research's proposed §6 ordering, which put posture first: `trader.md` §1's refusals ("checked FIRST") are handled **before** a proposal is validated at all, in `machine.py` (Task 8); `gate_alert_posture` and `gate_drawdown_halt` here are the recorded re-checks that make the stored gate list complete.

Formulas, all ported verbatim from `pre-order-check.sh` with `AV` = `view.account.liquidation_value`:

| Gate | Rule | Formula |
|---|---|---|
| `price_floor` | §1.4 | option: `underlying_price ≥ min_share_price_usd`; else `limit_price ≥ min_share_price_usd`. Exact — `4.9999` fails, `5.00` passes (`:287-301`) |
| `instrument_permitted` | §1.2/§1.3/§1.5 | single leg; instruction ∈ {BUY, SELL, BUY_TO_OPEN, SELL_TO_CLOSE}; assetType ∈ {EQUITY, OPTION} |
| `liquidity_floor` | §2 | `avg_daily_volume × price ≥ min_avg_daily_dollar_volume` **and** `avg_daily_volume ≥ min_avg_daily_volume` |
| `position_cap` | §3.1 | `existing_position_value + qty×price ≤ cap_dollars(single_position_pct, AV)` |
| `option_caps` | §3.2 | single: `existing_option_premium + qty×price×100 ≤ cap_dollars(option_single_position_pct, AV)`; aggregate: `open_option_premium + qty×price×100 ≤ cap_dollars(option_open_premium_pct, AV)` |
| `leveraged_cap` | §3.5 | `leveraged_aggregate + qty×price×mult ≤ cap_dollars(leveraged_aggregate_pct, AV)`, for `leveraged_etf` **and** an option on a leveraged underlying |
| `correlation_cap` | §3.8 | cluster exposure after the fill `≤ cap_dollars(correlation_cap_pct, AV)`; `correlation is None` → **pass with note** |
| `drawdown_halt` | §3.6 | buys only: `not arith.is_halted(AV, hwm, rules)`; closes always pass |
| `settled_cash` | §5 | `qty×price×mult ≤ cash_available_for_trading − unsettled_cash` (`strategy.md:265-268`, "wrong only in the safe direction") |
| `reserve_invariant` | header | `arith.reserve_cash(...) − qty×price×mult ≥ reserve` |
| `earnings_recorded` | §3.7 | **always passes**; records the date or "not supplied". §3.7's earnings bar was deleted 2026-08-31 — this gate exists so the §4.9 log has the field, not to refuse |
| `corporate_actions_recorded` | §3.7 | always passes; records the note |
| `entry_cutoff` | §4.2 | buys after `orders.entry_cutoff_et` ET are refused; closes always pass |
| `alert_posture` | trader.md §1.1/§1.2 | any unacked alert → **buys refused**, exits and §3.5 forced closes allowed; `restricted` (cashCall ≠ 0 or closing-only) → **everything refused, exits included** |

- [ ] **Step 1: Write the failing pure-gate tests**

```python
"""The §3 caps, at the cent. Every boundary here is one the bash suite
asserted (test-pre-order-check.sh) and must keep asserting: exactly at the
cap passes, one cent over fails, and 1000.0001 x 35% floors to 350.0000."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal as D
from pathlib import Path

import pytest

from tc.clock import ET
from tc.orders.gates import (
    GateContext, gate_alert_posture, gate_drawdown_halt, gate_entry_cutoff,
    gate_leveraged_cap, gate_option_caps, gate_position_cap, gate_price_floor,
    gate_reserve_invariant, gate_settled_cash,
)
from tc.orders.specs import equity_buy_limit_day, option_buy_to_open_limit
from tc.rules.model import Rules

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")


def ctx(**over: object) -> GateContext:
    """A context whose account value is 1000.00, so the caps are the same
    round numbers the bash suite used: §3.1 350.00, §3.2 single 100.00,
    §3.2 open 300.00, §3.5 200.00 (test-pre-order-check.sh:13-15)."""
    ...  # see conftest helper below


def test_price_floor_boundary_passes_at_exactly_five() -> None:
    assert gate_price_floor(ctx(spec=equity_buy_limit_day("X", 1, D("5.00")))).passed
    assert not gate_price_floor(ctx(spec=equity_buy_limit_day("X", 1, D("4.9999")))).passed


def test_price_floor_for_an_option_compares_the_underlying_not_the_premium() -> None:
    spec = option_buy_to_open_limit("X     261016C00030000", 1, D("1.00"), "X")
    assert not gate_price_floor(ctx(spec=spec, underlying_price=D("4.99"))).passed
    assert gate_price_floor(ctx(spec=spec, underlying_price=D("5.00"))).passed


def test_position_cap_at_the_cap_passes_and_one_cent_over_fails() -> None:
    assert gate_position_cap(ctx(spec=equity_buy_limit_day("X", 35, D("10.00")))).passed
    r = gate_position_cap(ctx(spec=equity_buy_limit_day("X", 35, D("10.01"))))
    assert not r.passed and "350.00" in r.detail


def test_position_cap_counts_prior_adds() -> None:
    assert not gate_position_cap(
        ctx(spec=equity_buy_limit_day("X", 1, D("10.00")), existing_position_value=D("341.00"))
    ).passed
    assert gate_position_cap(
        ctx(spec=equity_buy_limit_day("X", 1, D("10.00")), existing_position_value=D("340.00"))
    ).passed


def test_position_cap_floors_never_rounds_up() -> None:
    # 1000.0001 x 35% = 350.000035 must floor to 350.0000.
    over = ctx(spec=equity_buy_limit_day("X", 1, D("350.0001")), account_value=D("1000.0001"))
    at = ctx(spec=equity_buy_limit_day("X", 1, D("350.0000")), account_value=D("1000.0001"))
    assert gate_position_cap(at).passed and not gate_position_cap(over).passed


def test_option_single_cap_is_ten_percent_not_the_old_twenty() -> None:
    spec = option_buy_to_open_limit("X     261016C00030000", 1, D("2.00"), "X")
    r = gate_option_caps(ctx(spec=spec, underlying_price=D("30")))
    assert not r.passed and "100.00" in r.detail          # 200.00 premium vs a 100.00 cap


def test_option_aggregate_cap_names_itself() -> None:
    spec = option_buy_to_open_limit("X     261016C00030000", 1, D("0.50"), "X")
    r = gate_option_caps(ctx(spec=spec, underlying_price=D("30"), open_option_premium=D("299.99")))
    assert not r.passed and "aggregate" in r.detail and "300.00" in r.detail


def test_leveraged_cap_applies_to_an_option_on_a_leveraged_underlying() -> None:
    spec = option_buy_to_open_limit("TQQQ  261016C00030000", 1, D("1.00"), "TQQQ")
    r = gate_leveraged_cap(
        ctx(spec=spec, underlying_price=D("60"), leveraged={"TQQQ"}, leveraged_aggregate=D("150.00"))
    )
    assert not r.passed and "200.00" in r.detail


def test_settled_cash_uses_the_conservative_form() -> None:
    # strategy.md:265-268 — every buy gates on cashAvailableForTrading - unsettledCash.
    c = ctx(spec=equity_buy_limit_day("X", 10, D("10.00")), settled=D("150.00"), unsettled=D("60.00"))
    r = gate_settled_cash(c)
    assert not r.passed and "unsettled funds cannot be used" in r.detail


def test_reserve_invariant_refuses_an_order_that_would_spend_the_float() -> None:
    c = ctx(spec=equity_buy_limit_day("X", 1, D("50.00")), cash_balance=D("930.00"),
            settled=D("930.00"), unsettled=D("0"))
    assert not gate_reserve_invariant(c).passed


def test_drawdown_halt_refuses_buys_and_permits_closes() -> None:
    halted = dict(account_value=D("3000.00"), hwm=D("3800.00"))
    assert not gate_drawdown_halt(ctx(spec=equity_buy_limit_day("X", 1, D("10")), **halted)).passed
    from tc.orders.specs import gtc_stop_limit_sell
    assert gate_drawdown_halt(
        ctx(spec=gtc_stop_limit_sell("X", 1, D("9"), D("8.55")), kind="stop", **halted)
    ).passed


def test_entry_cutoff_refuses_a_buy_after_1530_and_never_a_close() -> None:
    late = datetime.combine(date(2026, 9, 8), time(15, 31), tzinfo=ET)
    assert not gate_entry_cutoff(ctx(spec=equity_buy_limit_day("X", 1, D("10")), now=late)).passed
    from tc.orders.specs import equity_sell_limit_day
    assert gate_entry_cutoff(
        ctx(spec=equity_sell_limit_day("X", 1, D("10")), kind="exit", now=late)
    ).passed


def test_alert_posture_refuses_buys_but_allows_a_forced_close() -> None:
    from tc.store.db import AlertRow
    alert = AlertRow(id=1, opened_at=datetime(2026, 9, 8, tzinfo=ET), kind="naked", message="x", acked_at=None)
    assert not gate_alert_posture(ctx(spec=equity_buy_limit_day("X", 1, D("10")), alerts=[alert])).passed
    from tc.orders.specs import equity_sell_limit_day
    assert gate_alert_posture(
        ctx(spec=equity_sell_limit_day("X", 1, D("10")), kind="forced_close", alerts=[alert])
    ).passed


def test_restriction_refuses_even_an_exit() -> None:
    from tc.orders.specs import equity_sell_limit_day
    r = gate_alert_posture(ctx(spec=equity_sell_limit_day("X", 1, D("10")), kind="exit", restricted=True))
    assert not r.passed and "including exits" in r.detail
```

The `ctx(...)` helper builds a `BookSnapshot` from keyword overrides; put it in a new `tests/engine/unit/conftest.py` so Task 7's tests reuse it verbatim rather than defining a second, drifting copy.

- [ ] **Step 2: Run to verify it fails.** Expected: `ImportError: cannot import name 'GateContext'`.

- [ ] **Step 3: Implement the pure gates**

Module head:

```python
"""The §4.9 / §4.10 gate suite: pure functions over `Rules` and a
`BookSnapshot` (spec §5.2), each returning a named pass/fail with the numbers
that decided it.

This module absorbs BOTH halves of `scripts/pre-order-check.sh`: the four
gates it evaluated, and the nine it printed under NOT-CHECKED as "the
operator's §4.9/§4.10 duty" (`:264-279`). There is no operator here. Every
line of that block is a gate below — earnings and corporate actions as
recorders (§3.7's bar was deleted 2026-08-31 and they gate nothing), the
other seven as refusals.

Two invariants inherited from the bash gate:
* **Caps are floored, never rounded up.** `arith.cap_dollars` is the only way
  a cap is computed here (`pre-order-check.sh:47-49`).
* **One variable per rule, never shared.** §3.2's aggregate and §3.5's
  leveraged aggregate were both 20% before 2026-08-17 and shared a variable;
  that is how the drift happened (`:249-251`).
"""
```

Each gate is six to twelve lines: compute, compare, build `GateResult` with `numbers` carrying the operands as strings. `gate_position_cap`:

```python
def gate_position_cap(ctx: GateContext) -> GateResult:
    if not is_buy(ctx) or ctx.spec.asset_type == "OPTION":
        return _skip("position_cap", "not an equity buy")
    av = ctx.snapshot.view.account.liquidation_value
    cap = arith.cap_dollars(ctx.rules.single_position_pct, av)
    after = ctx.snapshot.existing_position_value + ctx.spec.notional
    return GateResult(
        name="position_cap",
        passed=after <= cap,
        detail=f"§3.1: {after} vs cap {cap} ({ctx.rules.single_position_pct}% of {av})",
        numbers={"after": str(after), "cap": str(cap), "account_value": str(av)},
    )
```

`_skip(name, why)` returns `GateResult(passed=True, detail=f"n/a: {why}")` — a gate that does not apply passes, and says why, so the recorded list has one row per gate on every order.

- [ ] **Step 4: Write the hypothesis property tests**

`tests/engine/property/test_gate_props.py`:

```python
from decimal import Decimal as D
from pathlib import Path

from hypothesis import given, strategies as st

from tc.money import floor_cents
from tc.rules import arith
from tc.rules.model import Rules

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
money = st.decimals(min_value=D("0.01"), max_value=D("10000000"), places=4)
pcts = st.sampled_from([RULES.single_position_pct, RULES.option_single_position_pct,
                        RULES.option_open_premium_pct, RULES.leveraged_aggregate_pct])


@given(av=money, pct=pcts)
def test_a_cap_never_rounds_up(av: D, pct: D) -> None:
    cap = arith.cap_dollars(pct, av)
    assert cap <= av * pct / D(100)
    assert cap == floor_cents(cap)


@given(av=money, pct=pcts)
def test_a_cap_is_monotone_in_capital(av: D, pct: D) -> None:
    assert arith.cap_dollars(pct, av) <= arith.cap_dollars(pct, av + D("1"))


@given(qty=st.integers(min_value=1, max_value=1000000), price=money)
def test_an_order_exactly_at_the_cap_passes_and_a_cent_over_does_not(qty: int, price: D) -> None:
    notional = arith.notional(qty, price, 1)
    av = notional * D(100) / RULES.single_position_pct
    cap = arith.cap_dollars(RULES.single_position_pct, av)
    assert (notional <= cap) or (notional - cap <= D("0.01"))
```

- [ ] **Step 5: Gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/orders/gates.py engine/tc/rules/model.py tests/engine/unit/conftest.py \
        tests/engine/unit/test_gates_pure.py tests/engine/property/test_gate_props.py
git commit -m "engine: every line of the old NOT-CHECKED block is a gate, because there is no operator to carry it"
```

---

### Task 7: `tc/orders/gates.py` II — chain, ceilings, preview, the three-way

**Files:**
- Modify: `engine/tc/orders/gates.py`
- Test: `tests/engine/unit/test_gates_network.py`

**Interfaces:**
- Produces:
  - `gate_option_quality`, `gate_rate_ceiling`, `gate_preview_accepted`, `gate_notional_three_way`, `gate_quote_freshness`, `gate_halt_status`, `gate_identifier_round_trip`, `gate_stop_geometry_valid`.
  - `GATE_ORDER: tuple[str, ...]` and `GATES: dict[str, Callable[[GateContext], GateResult]]`.
  - `run_pre_preview(ctx) -> list[GateResult]` — every gate up to and including `rate_ceiling`, short-circuiting on the first failure.
  - `run_post_preview(ctx) -> list[GateResult]` — `preview_accepted` onward; requires `ctx.preview is not None`.
  - `first_failure(results) -> GateResult | None`.
  - `count_symbol_orders(snapshot, symbol) -> int` and `SYMBOL_ORDER_KINDS` — the §4.10 counting rules, exported so `stops.py` can ask before it acts.

`GATE_ORDER`, exactly (spec §5.2's sequence, with the ported NOT-CHECKED items in their §4.9 positions):

```python
GATE_ORDER = (
    "price_floor", "instrument_permitted", "liquidity_floor",          # §1.4 / §2
    "position_cap", "option_caps", "leveraged_cap", "correlation_cap", # §3.1 §3.2 §3.5 §3.8
    "drawdown_halt",                                                   # §3.6
    "settled_cash", "reserve_invariant",                               # §5 + header
    "option_quality",                                                  # §3.2, live chain
    "earnings_recorded", "corporate_actions_recorded",                 # §3.7, recorded
    "entry_cutoff",                                                    # §4.2
    "alert_posture",                                                   # ALERT / closing-only
    "rate_ceiling",                                                    # §4.10
    "preview_accepted",                                                # ---- preview runs here
    "notional_three_way",                                              # §4.10
    "quote_freshness", "halt_status", "identifier_round_trip",         # §4.10 + §3.7
    "stop_geometry_valid",                                             # §3.4
)
```

§4.10 counting rules, from `CLAUDE.md` §4.10 "Counting:" and `tick.md:319-329`, all four of them:
- **4 orders per symbol per session**, tripping on the attempt to exceed — placing the 4th is legal, the trip is the would-be 5th.
- **A stop that has never rested is a NEW order** and consumes a per-symbol slot. After an entry fill, the first stop attempt is that symbol's second routine order of four.
- **A cancel + immediate re-place of the same protective intent is ONE replace** against the 3-per-stop ceiling, and **not** two per-symbol orders. With `replace_order` now available (schwab-py has it; the MCP did not) it is literally one call — a behaviour change to announce, not a refactor.
- **An entry re-price (cancel + new order) is a new order** and consumes a per-symbol slot.

- [ ] **Step 1: Write the failing tests**

```python
def test_option_quality_enforces_the_manual_band_and_the_tighter_strategy_floor() -> None:
    # manual §3.2: 0.45 <= delta <= 0.75; strategy.md §6: delta >= 0.50.
    assert gate_option_quality(ctx_chain(delta=D("0.62"))).passed
    below_manual = gate_option_quality(ctx_chain(delta=D("0.44")))
    assert not below_manual.passed and "0.45" in below_manual.detail
    above = gate_option_quality(ctx_chain(delta=D("0.76")))
    assert not above.passed and "0.75" in above.detail
    strategy = gate_option_quality(ctx_chain(delta=D("0.47")))
    assert not strategy.passed and "strategy" in strategy.detail.lower()


def test_option_quality_enforces_dte_open_interest_and_spread() -> None:
    assert not gate_option_quality(ctx_chain(dte=17)).passed          # floor is 18
    assert gate_option_quality(ctx_chain(dte=18)).passed
    assert not gate_option_quality(ctx_chain(open_interest=499)).passed
    assert not gate_option_quality(ctx_chain(bid=D("5.00"), ask=D("5.60"))).passed  # 11.3% of mid


def test_option_quality_without_a_chain_read_fails_closed() -> None:
    r = gate_option_quality(ctx_chain(chain=None))
    assert not r.passed and "no chain" in r.detail


def test_rate_ceiling_trips_on_the_would_be_fifth_not_the_fourth() -> None:
    assert gate_rate_ceiling(ctx_orders(symbol_orders=3)).passed        # this is the 4th
    r = gate_rate_ceiling(ctx_orders(symbol_orders=4))                  # this would be the 5th
    assert not r.passed and "4" in r.detail


def test_a_never_rested_stop_consumes_a_per_symbol_slot() -> None:
    # tick.md:319-323 — after the entry fill, the first stop attempt is the
    # symbol's SECOND routine order of four.
    assert count_symbol_orders(snapshot_with(entry=1, stop_attempts=1), "AMH") == 2


def test_a_stop_replace_is_one_replace_and_not_two_symbol_orders() -> None:
    snap = snapshot_with(entry=1, stop_attempts=1, stop_replaces=2)
    assert count_symbol_orders(snap, "AMH") == 2
    r = gate_rate_ceiling(ctx_orders(snapshot=snap, kind="stop_replace", replaces_today=2))
    assert r.passed                                                    # this is the 3rd replace
    assert not gate_rate_ceiling(ctx_orders(snapshot=snap, kind="stop_replace", replaces_today=3)).passed


def test_notional_three_way_tolerance_is_one_cent_inclusive() -> None:
    assert gate_notional_three_way(ctx_preview(intent=D("1008.90"), value=D("1008.91"))).passed
    r = gate_notional_three_way(ctx_preview(intent=D("1008.89"), value=D("1008.91")))
    assert not r.passed and "unit-confusion" in r.detail


def test_notional_three_way_uses_the_previewed_price_not_the_price_sent() -> None:
    """2026-08-24: a 34.80 limit came back previewed at 34.79 with orderValue
    1008.91 — Schwab clamped the marketable buy to the NBBO ask. Comparing
    against the SENT price fails by $0.29; against the previewed price it
    agrees exactly, and the machine re-previews at the clamped price."""
    c = ctx_preview(sent=D("34.80"), previewed=D("34.79"), qty=29, intent=D("1008.91"),
                    value=D("1008.91"))
    r = gate_notional_three_way(c)
    assert r.passed and r.numbers["previewed_price"] == "34.79"


def test_notional_three_way_catches_the_missing_option_multiplier() -> None:
    c = ctx_preview(option=True, qty=1, previewed=D("5.25"), intent=D("5.25"), value=D("525.00"))
    assert not gate_notional_three_way(c).passed


def test_quote_freshness_is_three_minutes_and_halt_status_must_be_normal() -> None:
    assert gate_quote_freshness(ctx_quote(age_s=179)).passed
    assert not gate_quote_freshness(ctx_quote(age_s=181)).passed
    assert not gate_halt_status(ctx_quote(security_status="Halted")).passed
    assert gate_halt_status(ctx_quote(security_status="Normal")).passed


def test_identifier_round_trip_compares_the_description_and_the_price() -> None:
    assert gate_identifier_round_trip(ctx_quote(description="AMERICAN HOMES 4 RENT")).passed
    assert not gate_identifier_round_trip(ctx_quote(description="")).passed


def test_stop_geometry_matches_the_manual_and_never_lowers_a_trigger() -> None:
    # AMH 2026-08-24: fill 34.79, ATR 1.89% -> 2.5x = 4.72% clamps to the 8%
    # floor -> trigger 32.01, limit 30.40.
    r = gate_stop_geometry_valid(ctx_stop(entry=D("34.79"), atr_pct=D("1.89"),
                                          trigger=D("32.01"), limit=D("30.40")))
    assert r.passed
    lower = gate_stop_geometry_valid(ctx_stop(entry=D("34.79"), atr_pct=D("1.89"),
                                              trigger=D("32.01"), limit=D("30.40"),
                                              existing_trigger=D("33.00")))
    assert not lower.passed and "raised, never lowered" in lower.detail


def test_run_pre_preview_short_circuits_and_records_what_it_evaluated() -> None:
    results = run_pre_preview(ctx(spec=equity_buy_limit_day("X", 1, D("4.99"))))
    assert [r.name for r in results] == ["price_floor"]
    assert first_failure(results) is not None and first_failure(results).name == "price_floor"


def test_gate_order_is_the_specs_order_and_every_gate_is_registered() -> None:
    assert set(GATE_ORDER) == set(GATES)
    assert GATE_ORDER.index("price_floor") < GATE_ORDER.index("position_cap")
    assert GATE_ORDER.index("rate_ceiling") < GATE_ORDER.index("preview_accepted")
    assert GATE_ORDER.index("preview_accepted") < GATE_ORDER.index("notional_three_way")
```

- [ ] **Step 2: Run to verify it fails.** Expected: `ImportError: cannot import name 'GATE_ORDER'`.

- [ ] **Step 3: Implement**

`gate_notional_three_way` is the one with a tolerance, and it is the only one:

```python
TOLERANCE = Decimal("0.01")   # §4.10 "beyond rounding" — the ONLY tolerance in the suite


def gate_notional_three_way(ctx: GateContext) -> GateResult:
    """The unit-confusion abort. Three numbers, compared pairwise:
    Claude's `intent_notional`, the engine's own qty x price x multiplier, and
    the preview's `orderValue`. The price used is the one the PREVIEW returned
    — Schwab clamps a marketable limit to the NBBO, and comparing against what
    was sent fails a correct order by the size of the clamp (2026-08-24: sent
    34.80, previewed 34.79, a $0.29 gap on 29 shares)."""
    preview = ctx.preview
    if preview is None or preview.price is None or preview.order_value is None:
        return GateResult(name="notional_three_way", passed=False,
                          detail="no preview to compare against", numbers={})
    computed = arith.notional(ctx.spec.quantity, preview.price, ctx.spec.multiplier)
    intent = ctx.intent_notional if ctx.intent_notional is not None else computed
    pairs = ((intent, computed), (computed, preview.order_value), (intent, preview.order_value))
    ok = all(arith.notional_matches(a, b, TOLERANCE) for a, b in pairs)
    return GateResult(
        name="notional_three_way", passed=ok,
        detail=("§4.10 unit-confusion abort: " if not ok else "§4.10 three-way: ")
               + f"intent {intent}, computed {computed}, preview {preview.order_value}",
        numbers={"intent": str(intent), "computed": str(computed),
                 "preview_order_value": str(preview.order_value),
                 "previewed_price": str(preview.price)},
    )
```

`gate_rate_ceiling` reads `count_symbol_orders(snapshot, symbol)` and `snapshot.replaces_today`, compares against `rules.max_orders_per_symbol_per_session` / `rules.max_replaces_per_stop_per_day`, and trips on `count >= limit` (the would-be N+1). It never applies the protective-order exception itself: it reports the breach, and Task 8's machine is what decides that a protective order goes anyway and sets `ceiling_hit`. Keeping the exception out of the gate is what stops "protective" from becoming a general override.

`gate_option_quality` checks, in order and reporting all failures in one detail string: `dte >= option_min_dte`; `option_min_delta <= delta <= option_max_delta`; `delta >= strategy.option_min_delta` (a *(strategy rule)*, stricter, and named as such in the detail); `open_interest >= option_min_open_interest`; `spread_pct_of_mid <= option_max_spread_pct_of_mid`. `chain is None` fails closed.

- [ ] **Step 4: Run to verify it passes, gate, and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add engine/tc/orders/gates.py tests/engine/unit/test_gates_network.py
git commit -m "engine: the three-way compares the price the preview returned, because Schwab clamps the one you sent"
```

---

### Task 8: `tc/orders/machine.py` — the state machine and §5.5 idempotency

**Files:**
- Create: `engine/tc/orders/machine.py`
- Test: `tests/engine/unit/test_machine.py`

**Interfaces:**
- Consumes: `OrderStore`, `BrokerWriter`, `Broker`, `Approver` (Task 9 — imported as a Protocol, so this task defines it in `approval.py` first as a stub or takes it as a `Protocol` declared here and re-exported; **declare it here** and have Task 9 implement it), `gates`, `snapshot`, `specs`, `intent`, `Notifier`/`Poster`.
- Produces:
  - `class Refusal(BaseModel)`: `rule: str`, `detail: str`.
  - `posture_refusal(snapshot, kind, *, orders_enabled) -> Refusal | None` — `trader.md` §1's six refusals, checked FIRST, before validation.
  - `class ValidationResult(BaseModel)`: `proposal_id: str`, `accepted: bool`, `gates: list[GateResult]`, `state: str`, `refusal: Refusal | None`.
  - `class OrderMachine(store: OrderStore, writer: BrokerWriter, broker: Broker, market: MarketReader, approver: Approver, rules: Rules, settings: Settings, clock, poster: Poster)` with:
    `async validate(intent, kind, snapshot, agent) -> ValidationResult` (proposals row, pre-preview gates, preview, post-preview gates);
    `async submit(client_key) -> OrderRecord` (approval → placing → place → id resolution → working);
    `async place_protective(spec, kind, snapshot, *, allow_ceiling_breach: bool) -> OrderRecord`;
    `async cancel(order_id, symbol, reason) -> None`;
    `async resolve_placing_rows(account_hash, now) -> list[OrderRecord]`.

The transition table is spec §5.3, verbatim, and `_ALLOWED: dict[OrderState, frozenset[OrderState]]` encodes it so an illegal transition raises rather than being written:

```
proposed -> validated | rejected
validated -> awaiting_approval -> approved | denied | expired(600s)
approved -> placing -> working -> partially_filled -> filled | cancelled | broker_rejected
filled (entry) -> stop_pending -> stop_resting | stop_failed -> closing (§4.3) -> flat
```

Invariants enforced in code (spec §5.3):
- **One action in flight.** `submit` refuses when `orders_in_flight()` is non-empty, except for a protective order — `place_protective` pre-empts, because §3.4/§4.3 outrank everything (`trader.md` §2).
- **A working entry is cancelled at `orders.entry_cancel_et` (15:55 ET) or on shutdown** (§4.2, `strategy.md:262-264`: "no entry order may be working without a live session watching it").
- **A stop gets `orders.stop_attempts` (3) placement attempts and a §4.3 close `orders.close_attempts` (2), then `ALERT`** (`tick.md:315-317`).

§5.5 idempotency, the whole of it:

```python
    async def _place(self, rec: OrderRecord) -> OrderRecord:
        """CLAUDE.md §4.6: never resubmit an order of unknown status.

        The `placing` row with its `placing_at` is written BEFORE the HTTP
        call, so a crash between the write and the response still leaves the
        evidence that an order may exist. Every outcome — id returned, no
        Location header, timeout, exception — goes through the same
        reconcile-by-query: `get_orders(from=placing_at - 60s)` matched on
        (symbol, quantity, price, type, entered >= placing_at). Schwab has no
        client-supplied order id (spec §13.4), so the query IS the identity.
        """
```

`resolve_placing_rows` runs at startup, before any other action, and is the replay test's subject: a `placing` row with no id is resolved by query and **never resubmitted** (spec §10).

- [ ] **Step 1: Write the failing tests**

Cover, with `FakeBroker` (writer + reader) and a real `Store`:
- `validate` on a clean book writes one `proposals` row with `accepted=1` and a `gates_json` array whose names are `GATE_ORDER`-ordered, and one `orders` row in state `validated`;
- a failing gate writes `accepted=0`, `state="rejected"`, and no `orders` row;
- `posture_refusal` returns §1.1 for an unacked alert on a buy, §1.2 for `cashCall != 0` on **any** order including an exit, §1.3 for a halt on a buy and `None` for a close, §1.4 when the phase is not RTH, §1.6 when the account read failed;
- `submit` with an approving stub: `validated → awaiting_approval → approved → placing → working`, with one `order_events` row per transition and `placing_at` set before `place_order` is called (assert by a writer stub that reads the store mid-call);
- `place_order` returning `None` (no Location header) resolves the id by `get_orders` and records `id_resolved_by_query`, and the writer's `place_order` is called exactly **once**;
- a `place_order` that raises `TimeoutError` still reconciles by query and never calls `place_order` twice;
- `resolve_placing_rows` at startup attaches the id to a `placing` row and transitions it to `working` without a second placement;
- a denied approval transitions to `denied` and places nothing; an expired one to `expired`;
- `submit` refuses a second discretionary order while one is in flight, and `place_protective` succeeds in the same situation;
- `place_protective(allow_ceiling_breach=True)` past the §4.10 ceiling sets `ceiling_hit=1`, opens an alert, and posts — and the next discretionary `submit` is refused.

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError: No module named 'tc.orders.machine'`.

- [ ] **Step 3: Implement.** Skeleton:

```python
async def validate(self, intent, kind, snapshot, agent) -> ValidationResult:
    refusal = posture_refusal(snapshot, kind, orders_enabled=self._orders_enabled)
    proposal_id = f"p-{snapshot.session_date}-{uuid4().hex[:8]}"
    if refusal is not None:
        await self._record_proposal(proposal_id, intent, kind, [], accepted=False,
                                    state="rejected", agent=agent)
        return ValidationResult(proposal_id=proposal_id, accepted=False, gates=[],
                                state="rejected", refusal=refusal)
    spec = self._spec_for(intent, kind, snapshot)
    ctx = GateContext(rules=self._rules, snapshot=snapshot, spec=spec, kind=kind,
                      intent=intent, preview=None, orders_enabled=self._orders_enabled)
    results = run_pre_preview(ctx)
    if first_failure(results) is None:
        preview = await self._writer.preview_order(snapshot.view.account.account_hash, spec.payload)
        ctx = replace(ctx, preview=preview)
        if preview.price is not None and preview.price != spec.limit_price:
            # The clamp (research §4.1): re-preview at the price Schwab
            # returned so intent, limit and orderValue agree EXACTLY, then
            # re-run the three-way against that.
            spec = self._reprice(spec, preview.price)
            preview = await self._writer.preview_order(..., spec.payload)
            ctx = replace(ctx, spec=spec, preview=preview)
        results += run_post_preview(ctx)
    ...
```

The §4.9 trade-log row is written **here**, after the gates pass and before `submit` ever places: `await self._store.append_trade_log(pretrade_row(...))` with `action="PRETRADE"`, the stop's planned trigger/limit, the settled cash, the drawdown level and a `rule_check` string built from the gate results. `trader.md` §3.1: "a log written after the fact is not a gate."

- [ ] **Step 4: Gate and commit** — "engine: a placing row exists before the call, and an unknown status is resolved by query, never by a second order".

---

### Task 9: `tc/orders/approval.py` and `tc/discord/bot.py` — the ✅/❌ gate, persisted

**Files:**
- Create: `engine/tc/orders/approval.py`, `engine/tc/discord/__init__.py`, `engine/tc/discord/bot.py`
- Modify: `engine/tc/config.py` (`OrdersConfig`; `Secrets.discord_bot_token`, `discord_channel_id`, `discord_approver_id`), `engine/tc/notify.py` (`Poster` protocol, `FirstWorking`), `engine/pyproject.toml` (`discord.py>=2.4`), `config.yml`
- Test: `tests/engine/unit/test_approval.py`, `tests/engine/unit/test_discord_bot.py`

**Interfaces:**
- Produces:
  - `ApprovalRequest(client_key, summary, embed_fields: list[tuple[str, str]], spec: OrderSpec, held: int, kind: OrderKind)`.
  - `Decision = Literal["approved","denied","expired"]`.
  - `class Approver(Protocol)`: `async def request(self, req: ApprovalRequest) -> Decision: ...`
  - `class AutoApprover(Approver)` — approves everything; paper mode and tests only, and it logs at warning on every call so it can never be mistaken for the real gate in a log.
  - `class PersistedApprover(store: OrderStore, bot: DiscordBot | None, timeout_s: int, clock)` — the production `Approver`: `is_protective_stop(spec, held)` → auto-approve with an announcement; otherwise write an `approvals` row, post the embed, await the reaction, and record the decision, the approver id and the latency.
  - `async rearm_pending(approver, now) -> list[str]` — at startup, every `approvals` row still `pending` whose `expires_at` is in the future is re-attached to its `message_id`; one already past `expires_at` is recorded `expired`.
  - `class DiscordBot(token, channel_id, approver_id, store, order_store, clock)`: `async start()/close()`, `async post(text) -> bool` (satisfies `Poster`), `async request_approval(req, timeout_s) -> Decision`, slash commands `/status`, `/ack`, `/halt`, `/resume`.

Semantics, from the live record (research §5) and spec §5.4:
- **One bot token.** `TC_DISCORD_BOT_TOKEN`, `TC_DISCORD_CHANNEL_ID`, `TC_DISCORD_APPROVER_ID`. The old `schwab-mcp` bot is dead, so the token is free; the engine takes the gateway and the webhook `Notifier` stays as the fallback poster.
- **600 s timeout**, ✅/❌ from the configured approver id **only**. Observed latency on the right account: 13–35 s, one 195 s outlier — the timeout is not generous, it is calibrated.
- **A reaction from anyone else is removed and logged at WARNING**, not debug. This is the exact 2026-08-14 failure: reacting from a second account looked like the ✅ "unchecking itself" and two approvals expired silently. Warning level is the fix, and the reaction is also recorded in `approvals.rejected_reactions_json`.
- **`is_protective_stop` auto-approves with an announcement** — Chris, 2026-08-14, verbatim: *"If there is a way for you to work off stops without my input that would be approved."* The engine's predicate adds the third clause the shipped site-packages patch lacks: `quantity ≤ held`.
- **A restart re-arms.** `approvals` rows persist, so a pending approval survives the process that requested it. Today it does not, and that is a lost order.
- **A stop amendment is `replace_order`** — one call, one count against the §4.10 replace ceiling, instead of cancel-and-re-place. Announce this as a behaviour change in the commit message: the MCP had no `replace_order` and `CLAUDE.md` §4.10's counting note describes the old shape (spec §14 lists it for a Phase 3 amendment; **do not amend `CLAUDE.md` in this plan**).

- [ ] **Step 1: Write the failing approval tests**

Cover with a stub bot (no gateway):
- a protective stop with `quantity == held` is `approved` with `mode="auto_protective"`, `auto_reason` naming the `qty <= held` clause, and an announcement posted;
- a protective stop with `quantity > held` goes to the human path;
- a BUY, an option and a cancel all go to the human path — "everything else still requires the ✅";
- an approving reaction from the configured id → `approved`, `approver_id` recorded, `latency_s` set;
- a reaction from another id → removed, logged at WARNING (assert with `caplog.at_level(logging.WARNING)`), recorded in `rejected_reactions_json`, and the request still times out;
- no reaction inside the timeout → `expired`, and the row says so;
- `rearm_pending` after a restart returns the still-live key and marks a stale one `expired`.

- [ ] **Step 2: Write the failing bot tests.** `discord.py` is not exercised over a socket: `DiscordBot` takes its `discord.Client` as a constructor argument, so the tests pass a stub exposing `get_channel`, `send`, `add_reaction`, `remove_reaction` and a settable `on_raw_reaction_add` handler. Assert: the embed carries symbol, side, quantity, limit, notional, the stop geometry and the top three gate lines; `/status` renders the latest tick row; `/ack` acks open alerts; `/halt` opens an alert of kind `manual_halt` (closing-only posture); `/resume` acks it.

- [ ] **Step 3: Run to verify they fail.** Expected: `ModuleNotFoundError: No module named 'tc.orders.approval'`.

- [ ] **Step 4: Implement.** Add to `config.py`:

```python
class OrdersConfig(BaseModel):
    """Operational constants for the order path. None of these is a §9 rule
    number — the caps and ceilings all come from rules.yml — but every one of
    them is a decision that must not be inline in code."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False            # False = protective-only: stops, cancels, forced closes
    approval_timeout_s: int = 600
    entry_cutoff_et: str = "15:30"
    entry_cancel_et: str = "15:55"
    fill_poll_s: int = 15
    stop_deadline_s: int = 60
    naked_force_close_min: int = 15
    stop_attempts: int = 3           # tick.md §E
    close_attempts: int = 2          # tick.md §E
    decide_window_et: str = "10:00-15:00"
    decide_max_per_day: int = 3
```

and to `config.yml`:

```yaml
orders:
  enabled: false             # Phase 2 flips this; false = protective-only (spec §11)
  approval_timeout_s: 600    # schwab-mcp's default, kept: observed ✅ latency 13-35s
  entry_cutoff_et: "15:30"
  entry_cancel_et: "15:55"   # §4.2 + spec §5.3: no working entry past this
  fill_poll_s: 15
  stop_deadline_s: 60        # spec §11 Phase 2: stop verified resting within 60s of fill
  naked_force_close_min: 15  # tick.md §F
  stop_attempts: 3           # tick.md §E
  close_attempts: 2
  decide_window_et: "10:00-15:00"
  decide_max_per_day: 3
```

In `notify.py` add `class Poster(Protocol): async def post(self, text: str) -> bool: ...` and `class FirstWorking(Poster)` taking an ordered list of posters and returning on the first `True` — the bot when it is up, the webhook when it is not.

- [ ] **Step 5: Gate and commit** — "engine: a pending approval survives a restart, a stranger's reaction is a warning, and a covered stop needs no ✅".

---

### Task 10: `tc/loops/stops.py` — the protective loop

**Files:**
- Create: `engine/tc/loops/stops.py`
- Test: `tests/engine/unit/test_stops.py`

**Interfaces:**
- Consumes: `OrderMachine`, `OrderStore`, `BookView`, `Trip`, `arith.stop_geometry`, `Rules`, `OrdersConfig`, `Poster`.
- Produces:
  - `StopPlan(symbol, quantity, entry_price, atr_pct, trigger, limit, reason: str)` and `plan_stop(symbol, quantity, entry_price, atr_pct, rules, existing_trigger: Decimal | None) -> StopPlan`.
  - `async ensure_stops(machine, order_store, view, bars, rules, cfg, now) -> list[StopAction]` — §3.4/§4.3/§4.4: a naked position gets a stop within `cfg.stop_deadline_s` of its fill; a partial gets its stop **replaced** to the filled quantity; `cfg.stop_attempts` failures then `ALERT` and the §4.3 close (`cfg.close_attempts`, then `ALERT` and stop).
  - `async ratchet_stops(machine, order_store, view, quotes, bars, rules, now) -> list[StopAction]` — playbook §5, on **closes with a fresh quote**, never intraday noise.
  - `async cancel_orphans(machine, view, now) -> list[StopAction]` — §4.7.
  - `async reprice_after_corporate_action(...) -> list[StopAction]` — §3.7: recompute and re-place in the same session the action takes effect; the position is **not** exited.
  - `async handle_trips(machine, order_store, res: TickResult, ...) -> list[StopAction]` — the entry point `main.py` calls; routes watch 4 (naked) → `ensure_stops`, watch 5 (partial) → replace-to-filled, watch 6 (stop fill / orphan / duplicate) → `cancel_orphans`.
  - `detect_stop_drift(stop: StopRow, live: OrderRow, dividends: list[Decimal]) -> Literal["none","ex_dividend","tampered"]`.

The ratchet rules, quoted from `strategy.md:179-188` because they are the thing this loop implements:

> - §3.4 stop-limit, GTC, at the manual's ATR-scaled trigger and its limit offset below the **share-weighted average entry**; re-computed via single replace on each add
> - Stall rule: two consecutive closes below blended entry, evaluated from session 4 onward (entry day = session 1) → close next session
> - Stop ratchet — **active** (§3.4: the trigger is a floor, may be raised, never lowered): **+8%** → breakeven, **+15%** → entry+8%, limit always 5% below trigger, single-message replace only, §4.6 applies to replaces.

Thresholds come from `rules.yml`: `strategy.ratchet_breakeven_at_gain_pct` (8), `strategy.ratchet_entry_plus8_at_gain_pct` (15), `strategy.stall_rule_consecutive_closes` (2), `strategy.stall_rule_from_session` (4). The limit offset is `manual.stop_limit_pct_below_trigger`.

The ex-dividend whitelist, from the live record (research §4.4, and the project memory `gtc-stops-adjust-on-ex-dividend`): on 2026-09-03 the resting CSX stop moved 45.34/43.07 → 45.20/42.93 with the **same order id, status still `WORKING`, and no replace in its history** — exactly the $0.14 dividend that went ex on 08-31. Schwab reduces open GTC sell stops by the cash dividend on ex-date unless `DO_NOT_REDUCE` is set. `detect_stop_drift` returns `ex_dividend` when `prior_trigger − dividend == live_trigger` and there is no replace event; the loop logs `STOP_ADJUSTED` to the trade log and **does not re-place**. Anything else is `tampered` and raises an alert.

- [ ] **Step 1: Write the failing tests.** Cover: `plan_stop` reproduces the AMH row (34.79 fill, 1.89% ATR → 2.5× = 4.72% clamps to the 8% floor → 32.01/30.40) and refuses to lower an existing trigger; a naked position produces one stop action and, on the third failure, an `ALERT` plus a §4.3 close; a partial produces a **replace**, not a new order; an orphan produces a cancel; a +8% gain moves the trigger to breakeven and a +15% to entry+8% with the limit 5% below, each as one replace; a ratchet on a stale quote is deferred; a fourth replace in a day is refused by the §4.10 ceiling; `detect_stop_drift` returns `ex_dividend` for the CSX numbers and `tampered` for an unexplained move.

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError: No module named 'tc.loops.stops'`.

- [ ] **Step 3: Implement.** Every action goes through `machine.place_protective(...)`, so the retry bounds, the ceiling accounting and the `order_events` trail are in one place. `ensure_stops` returns actions rather than performing notifications; `main.py` posts. Exit ordering is an invariant this module holds and never varies: **cancel the resting stop FIRST, then place the sell** (`strategy.md:257-261` — sell-then-cancel leaves an orphaned GTC stop, which is §1.5's accidental short if the process dies between the two calls).

- [ ] **Step 4: Gate and commit** — "engine: the protective loop places, ratchets and cancels; an ex-dividend adjustment is logged, not fought".

---

### Task 11: `tc/loops/fills.py` — fill polling and the 15:55 entry cancel

**Files:**
- Create: `engine/tc/loops/fills.py`
- Test: `tests/engine/unit/test_fills.py`

**Interfaces:**
- Produces:
  - `FillEvent(client_key, order_id, symbol, filled_quantity, avg_price, terminal: bool)`.
  - `async poll_fills(machine, order_store, writer, account_hash, now) -> list[FillEvent]` — one `get_order` per order in `working | partially_filled | placing`; transitions `working → partially_filled → filled`, and `cancelled` / `broker_rejected` where the broker says so. Returns nothing and does no work when no order is in flight.
  - `async cancel_working_entries(machine, order_store, now, cutoff_et) -> list[str]` — §4.2: every working BUY is cancelled at `orders.entry_cancel_et` and on shutdown. Returns the cancelled keys.
  - `def poll_due(order_store_state: list[OrderRecord], last_poll: datetime | None, now: datetime, every_s: int) -> bool` — pure, so the cadence is testable without a clock.

The loop runs **only while something is in flight** — `strategy.md`'s §8 cadence table in engine form: 60 s while a position is naked (that is `stops.py`'s deadline), 5 min while an entry rests, 15 min otherwise. `poll_fills` at `orders.fill_poll_s` (15 s) is the tightest of these and exists for exactly one window: fill → stop.

- [ ] **Step 1: Write the failing tests.** Cover with the scripted `FakeBroker`: a working order that fills in two polls records `partial_fill` then `fill` events and ends `filled` with the average price; a `CANCELED` status from the broker ends `cancelled` without an engine cancel call; `cancel_working_entries` at 15:55 cancels a working BUY and leaves a working SELL stop alone (a GTC stop is not an entry); `poll_due` is False with nothing in flight, whatever the clock says.

- [ ] **Step 2: Run to verify it fails. Step 3: Implement. Step 4: Gate and commit** — "engine: fills are polled only while something is in flight, and no entry is left working past 15:55".

---

### Task 12: Engine wiring — `orders.enabled`, the jobs, and trips that act

**Files:**
- Modify: `engine/tc/main.py`, `engine/tc/http/app.py` (`EngineState` fields), `engine/tc/cli.py` (`--once stops`)
- Test: `tests/engine/unit/test_main.py` (extend)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `JOBS` gains `"stops"` (`at 09:31 weekdays` plus every fire of the tick) and `"entry_cancel"` (`at 15:55 weekdays`).
  - `Engine` gains `self._orders: OrderMachine | None`, `self._order_store: OrderStore`, `self._writer: BrokerWriter | None`, `self._approver: Approver`, and `self._bot: DiscordBot | None`.
  - `EngineState` gains real values for the two fields Phase 0b left as placeholders: `in_flight_proposal` (from `orders_in_flight()`) and `pending_approval_age_s` (from the oldest pending `approvals` row) — the host probe already alerts on both (spec §7 layer 2).
  - `Engine._job_tick` now calls `handle_trips(...)` after `_post_trips(...)`: **watches 4, 5 and 6 act, they do not only notify.** That is the whole point of the order path existing.
  - `Engine.start()` calls `machine.resolve_placing_rows(...)` **before anything else** (spec §5.5: "a `placing` row with no id at startup is resolved by query during reconciliation before any other action") and `rearm_pending(...)` immediately after.
  - `Engine.stop()` calls `cancel_working_entries(...)` before cancelling job tasks.

`orders.enabled` is read in exactly one place and means exactly one thing:

```python
    @property
    def orders_enabled(self) -> bool:
        """False (the default this plan ships) is PROTECTIVE-ONLY, not off:
        stops, stop replaces, orphan cancels and the §3.3/§3.5 forced closes
        all still run, because a book with no stops is the state §3.4 exists
        to prevent. What false refuses is every BUY (spec §11 Phase 2:
        "orders.enabled = false reverts to protective-only; every position
        stays stopped")."""
        return self._s.orders.enabled
```

- [ ] **Step 1: Write the failing tests.** Extend `test_main.py`: with `TC_MODE=paper` fixtures and `orders.enabled=False`, a tick whose view is naked produces a placed stop and no buy; with a proposal pending, `/health` reports `in_flight_proposal: true`; a `placing` row seeded before `start()` is resolved and never re-placed (assert the fake's `place_order` call count is 0); `stop()` cancels a working entry.

- [ ] **Step 2: Run to verify it fails. Step 3: Implement. Step 4: Gate and commit** — "engine: a tripped watch now acts, and orders.enabled=false still means every position keeps its stop".

---

### Task 13: `tc/mcp/tools_propose.py` and the role contract test

**Files:**
- Create: `engine/tc/mcp/__init__.py`, `engine/tc/mcp/tools_propose.py`
- Test: `tests/engine/unit/test_tools_propose.py`, `tests/engine/contract/test_mcp_roles_readonly.py`
- Modify: `engine/tc/rules/consistency.py` (the tool-registry check, spec §9)

**Interfaces:**
- Produces:
  - `class ProposeTools(machine: OrderMachine, snapshot_builder, store: Store, order_store: OrderStore)` with:
    `async propose_entry(intent: dict) -> dict` → `ValidationResult.model_dump()`;
    `async propose_exit(intent: dict) -> dict`;
    `async propose_option_close(position_id: str, limit_price: str, reason: str) -> dict`;
    `async get_proposal(proposal_id: str) -> dict`;
    `async book() -> dict` — positions, resting stops, settled/unsettled cash, account value, the high-water mark and the drawdown level, so the decider can reason without a broker tool.
  - `ROLE_TOOLS: dict[str, tuple[str, ...]]` — `{"decide": ("propose_entry","propose_exit","propose_option_close","get_proposal","book", <read tools>), "research": (<read tools>, <research_* writers>)}`. Plan 0c registers these with the MCP server; this plan owns the table and its test.
  - `WRITE_TOOL_RE = re.compile(r"place|cancel|replace|order")`.

- [ ] **Step 1: Write the failing contract test**

```python
"""Spec §5: "A contract test asserts that no MCP role exposes a tool whose
name matches place|cancel|replace|order." Claude never holds a write tool —
not by policy, by there being no such name in any role."""

import re

from tc.mcp.tools_propose import ROLE_TOOLS, WRITE_TOOL_RE


def test_no_role_exposes_a_write_shaped_tool() -> None:
    for role, tools in ROLE_TOOLS.items():
        for name in tools:
            assert not WRITE_TOOL_RE.search(name), f"role {role} exposes {name}"


def test_the_research_roles_carry_no_propose_tool() -> None:
    assert not [t for t in ROLE_TOOLS["research"] if t.startswith("propose_")]


def test_the_decider_carries_exactly_the_four_propose_tools() -> None:
    assert {t for t in ROLE_TOOLS["decide"] if t.startswith("propose_")} == {
        "propose_entry", "propose_exit", "propose_option_close",
    }
    assert "get_proposal" in ROLE_TOOLS["decide"]


def test_the_regex_would_actually_catch_a_regression() -> None:
    # The test is worthless if the pattern cannot fail: prove it bites.
    for bad in ("place_order", "cancel_order", "replace_order", "get_orders"):
        assert WRITE_TOOL_RE.search(bad)
```

- [ ] **Step 2: Write the failing tool tests.** `propose_entry` with a valid intent returns `accepted=True` and a proposal id that `get_proposal` resolves; an intent that fails a gate returns `accepted=False` with the gate list and **no order row**; a malformed intent returns a validation error rather than raising into the MCP layer; `book()` never contains the account hash or an order id (a `HASH_REDACTED`-style assertion, per `CLAUDE.md` §7.4).

- [ ] **Step 3: Run to verify they fail. Step 4: Implement.** Add the registry check to `consistency.py`'s `CHECKS` tuple as `tool_registry`, so a role that grows a write tool fails the consistency gate as well as the contract test (spec §9 lists it).

- [ ] **Step 5: Gate and commit** — "engine: Claude proposes; the four tools it holds cannot be named place, cancel, replace or order".

---

### Task 14: The `decide` job and `.claude/agents/decide.md`

**Files:**
- Create: `engine/tc/jobs/__init__.py`, `engine/tc/jobs/spec.py`, `.claude/agents/decide.md`
- Test: `tests/engine/unit/test_jobs_spec.py`

**Interfaces:**
- Produces:
  - `class JobResult(BaseModel)`: `verdict: Verdict`, `structured: dict[str, Any] | None`, `error: str | None`.
  - `class JobRunner(Protocol)`: `async def run_job(self, name: str, params: dict[str, Any]) -> JobResult: ...` — **consumed from Plan 0c.** Nothing in this plan calls it; the `decide` job declares it so Plan 0c has a typed target and this plan's tests can drive a stub.
  - `class JobSpec(BaseModel)`: `name`, `agent`, `allowed_tools: tuple[str, ...]`, `prompt_path: str`, `output_schema: dict`, `max_turns: int`, `timeout_s: int`, `window_et: str | None`, `max_per_day: int | None`.
  - `DECIDE: JobSpec` — `agent="decide"`, `allowed_tools=ROLE_TOOLS["decide"]`, `output_schema=DECISION_VERDICT_SCHEMA` (`{proposal_ids: [str], declined_reason: str | None}`).
  - `def decide_due(*, now_et, cfg: OrdersConfig, runs_today: int, undecided: int, manual_request: bool) -> tuple[bool, str]` — spec §4's trigger: fires when an undecided escalation or HOT candidate exists **and** the clock is inside `cfg.decide_window_et` **and** `runs_today < cfg.decide_max_per_day`, or on `/decide SYM` from Chris. Returns the reason either way, so a `noop` says *why*.

`.claude/agents/decide.md` is `trader.md` §1 and §2, ported and narrowed. What changes and why:
- **Frontmatter tools:** read tools plus `propose_entry`, `propose_exit`, `propose_option_close`, `get_proposal`, `book`. No `Bash`, no `Write`, no `Edit`, no broker write tool of any kind.
- **§0 survives verbatim in force** — one order-bearing action per invocation.
- **§1 survives verbatim** — the six refusals, checked first. The engine also enforces them in `posture_refusal`; the prompt keeps them because a decider that proposes into a halt has wasted a run and produced a rejected proposal for a human to read.
- **§2 survives verbatim** — the decision order, protective actions outranking entries, and "You do not originate theses; the research pipeline does."
- **§3, §4 and §4a are DELETED from the prompt.** The workflow (log first, preview, run the gate script, clear the four §4.10 gates, place, verify), the unattended-entry rule and the script-invocation form are now `tc/orders/`. A prompt that still described them would be instructing the model to do what it no longer has tools for — which is how the tool-allowlist failures in spec §1 happened.
- **§5's output contract survives**, with `EXEC` replaced by `PROPOSE`: `PROPOSE none — …`, `PROPOSE <kind> <symbol> — <proposal id, accepted, first failing gate>`, `REFUSE <§n> — …`.

- [ ] **Step 1: Write the failing tests.** `decide_due` is False outside the window with a reason naming the window; False at `max_per_day`; True on a manual request even outside the window (Chris asked); False with nothing undecided, reason `"no undecided escalation"`. `DECIDE.allowed_tools` contains no name matching `WRITE_TOOL_RE`. `.claude/agents/decide.md` exists, its frontmatter `tools:` line parses, and every tool on it is in `ROLE_TOOLS["decide"]` (a test that reads the file — this is the same class of drift `check-consistency.sh` exists to catch).

- [ ] **Step 2: Run to verify it fails. Step 3: Implement. Step 4: Gate and commit** — "engine: the trader becomes the decider — it proposes, and §3's workflow is now code".

---

### Task 15: `tc/store/export.py` — `trade-log.csv` and `status/DATE.md`

**Files:**
- Create: `engine/tc/store/export.py`
- Test: `tests/engine/unit/test_export.py`

**Interfaces:**
- Consumes: `OrderStore.trade_log_rows`, `Store.latest_session_status`, `Store.ticks_for`, `BookView`, `tc.shadow.parse_legacy_status`.
- Produces:
  - `TRADE_LOG_COLUMNS: tuple[str, ...]` — the 23, in order.
  - `render_trade_log_csv(rows: list[TradeLogRow]) -> str`.
  - `render_status_md(*, date, view, status: SessionStatusRow, ticks, reserve) -> str`.
  - `async export_day(store, order_store, out_dir: Path, d: date, view, reserve) -> list[Path]` — writes `<out_dir>/trade-log.csv` and `<out_dir>/status/<d>.md`. **No git push in this plan** (spec §6's store mirror is a later phase).

The 23 columns, in this exact order (`trade-log.csv:1`, `trade-log-append.sh:19-23`):

```python
TRADE_LOG_COLUMNS = (
    "date", "time_et", "action", "symbol", "instrument", "quantity", "limit_price",
    "fill_price", "gross", "fees", "net", "pct_of_comp_capital_after", "stop_trigger",
    "stop_limit", "settled_cash_before", "account_value_after", "reserve",
    "comp_capital_after", "high_water_mark", "drawdown_pct", "cum_option_premium",
    "rule_check", "rationale",
)
```

Quoting is the bash script's, exactly (`csv_quote`, `:66-71`): tabs/CR/LF become a space, `"` becomes `""`, and **every** field is wrapped in quotes. Unknown values are `-`, never blank — "no field defaults: use `-` for genuinely unknown" (`:25-27`).

`render_status_md` emits the `### State recorded — current` block in the shape `scripts/latest-status.sh`'s awk parses: the heading exactly, then a `High-water mark: $X,XXX.XX` line inside it before the next `#`, then account value, halt threshold, drawdown and the position lines with `**SYM** N sh @ $P avg (...) — stop T/L GTC WORKING (orderId …), qty N`.

- [ ] **Step 1: Write the failing tests**

```python
def test_trade_log_renders_all_23_columns_in_order_and_quotes_every_field() -> None:
    csv = render_trade_log_csv([_row(rationale='he said "buy"\tnow')])
    header, first = csv.splitlines()[:2]
    assert header == ",".join(f'"{c}"' for c in TRADE_LOG_COLUMNS)
    assert first.count(",") >= 22
    # tabs become spaces, quotes double, every field wrapped.
    assert '"he said ""buy"" now"' in first


def test_unknown_fields_render_as_a_dash_not_a_blank() -> None:
    assert '""' not in render_trade_log_csv([_row(fill_price="-")])


def test_status_export_parses_with_the_legacy_regex() -> None:
    """The round trip that matters: the engine's own export must be readable
    by the tool the old stack resolves the high-water mark with
    (scripts/latest-status.sh's awk, ported in tc.shadow.parse_legacy_status).
    If this ever fails, Phase 1's rollback path is gone."""
    body = render_status_md(date=date(2026, 9, 8), view=VIEW, status=STATUS,
                            ticks=[], reserve=D("900.00"))
    path = tmp_path / "2026-09-08.md"
    path.write_text(body)
    legacy = parse_legacy_status(path)
    assert legacy.hwm == STATUS.hwm
    assert legacy.account_value == STATUS.close_value
    assert legacy.positions == {"AMH": 29}
    assert legacy.stops["AMH"] == (D("32.01"), D("30.40"))


def test_status_export_carries_no_account_hash(tmp_path: Path) -> None:
    body = render_status_md(...)
    assert "HASH" not in body.upper().replace("HASH_REDACTED", "")
```

- [ ] **Step 2: Run to verify it fails. Step 3: Implement. Step 4: Gate and commit** — "engine: the exports are the old formats, and the status file still parses with the regex the old stack reads it by".

---

### Task 16: Paper mode — the whole path, end to end

**Files:**
- Create: `tests/engine/paper/__init__.py`, `tests/engine/paper/test_entry_fill_stop.py`; fixture `tests/engine/fixtures/broker/fills.json`
- Test: itself

This is the Phase 2 rehearsal in one test (spec §10, §11): proposal → gates → approval (stubbed) → place → fill → stop within 60 s → tick → close, asserting the **SQLite state and the trade-log rows**, not just that nothing raised.

**Interfaces:** consumes everything; produces nothing new.

- [ ] **Step 1: Write the failing scenario test**

```python
"""Paper mode: the full order path against the fixture broker with scripted
fills. This is spec §11's Phase 2 exit criterion in miniature — one entry with
approval, its stop auto-placed and verified resting within 60 seconds of the
fill, one ratchet by replace_order, one exit with the stop cancelled FIRST,
all reconciled by query with matching trade_log rows."""

async def test_entry_to_fill_to_stop_to_close(paper: PaperHarness) -> None:
    result = await paper.machine.validate(ENTRY, "entry", await paper.snapshot("AMH"), "decide")
    assert result.accepted, [g.detail for g in result.gates if not g.passed]

    # §4.9: the PRETRADE row exists BEFORE anything is placed.
    rows = await paper.order_store.trade_log_rows()
    assert [r.action for r in rows] == ["PRETRADE"]

    order = await paper.machine.submit(result_client_key(result))
    assert order.state == "working" and order.order_id == 1000000000001

    fills = await poll_fills(paper.machine, paper.order_store, paper.writer, paper.hash, paper.now())
    assert [f.filled_quantity for f in fills] == [29]
    assert (await paper.order_store.order_by_key(order.client_key)).state == "filled"

    # §3.4 / spec §11: the stop is resting, verified, inside 60 seconds.
    actions = await ensure_stops(paper.machine, paper.order_store, await paper.view(),
                                 paper.bars, paper.rules, paper.cfg, paper.now())
    stop = await paper.order_store.stop_for("AMH")
    assert stop is not None and stop.state == "resting"
    assert stop.quantity == 29                     # §4.4: the FILLED quantity
    assert (stop.verified_at - stop.placed_at).total_seconds() <= paper.cfg.stop_deadline_s
    assert stop.trigger_price == D("32.01") and stop.limit_price == D("30.40")

    # The tick now sees a covered book: no watch 4, no watch 5.
    res = await paper.tick()
    assert res.row.flags == "-" and res.trips == []

    # A +8% close ratchets to breakeven — ONE replace, counted once.
    paper.set_close("AMH", D("37.57"))
    await ratchet_stops(paper.machine, paper.order_store, await paper.view(),
                        paper.quotes, paper.bars, paper.rules, paper.now())
    stop = await paper.order_store.stop_for("AMH")
    assert stop is not None and stop.ratchet_stage == "breakeven"
    assert stop.trigger_price == D("34.79") and stop.replaces_today == 1

    # The exit cancels the stop FIRST (strategy.md:257-261).
    await paper.machine.validate(EXIT, "exit", await paper.snapshot("AMH"), "decide")
    events = [e.event for e in await paper.order_store.events_for(exit_key)]
    assert events.index("cancel_requested") < events.index("place_attempted")

    actions_log = await paper.order_store.trade_log_rows()
    assert [r.action for r in actions_log] == [
        "PRETRADE", "ORDER_PLACED", "FILL", "STOP_PLACED", "STOP_ADJUSTED", "PRETRADE", "ORDER_PLACED",
    ]
```

`PaperHarness` is a fixture in the same file: a `tmp_path` `Store`, a `FakeBroker` over the fixture directory with `fills.json`, an `AutoApprover`, `orders.enabled=True`, and a frozen clock the test advances.

- [ ] **Step 2: Run to verify it fails.** Expected: FAIL on the first missing harness piece.

- [ ] **Step 3: Add `fills.json` and whatever the harness needs; make it pass.** Nothing in `tc/` is changed to make this test pass — if it needs a production change, that change belongs in the task that owns the module and this test is the thing that found the gap.

- [ ] **Step 4: Gate and commit**

```bash
cd engine && pytest -q && mypy && ruff check . ../tests/engine
git add tests/engine/paper engine/tc/broker/fake.py tests/engine/fixtures/broker/fills.json
git commit -m "engine: the whole order path runs in paper mode, and the assertions are the ledger rows"
```

---

## Self-review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| §4 `decide` is trigger-driven (undecided/HOT, 10:00–15:00 ET, ≤3/day, `/decide SYM`) | 14 (`decide_due`) |
| §4 typed allowlists, structured verdicts, prompts reused | 14 (`JobSpec`, `DECISION_VERDICT_SCHEMA`, `.claude/agents/decide.md`) |
| §4 the runner itself, `PreToolUse` hook | **Plan 0c** — `JobRunner` protocol declared in Task 14 and marked consumed |
| §5 Claude never holds place/cancel/replace/preview | 1 (separate protocol), 13 (contract test + consistency check) |
| §5.1 intent schema, engine-assigned fields, `client_key` | 2 |
| §5.2 gates, in order, with numbers | 6 + 7 (`GATE_ORDER`) |
| §5.2 semantics ported from `pre-order-check.sh` and `trader.md` §2–§4 | 6 (four evaluated gates), 7 (the NOT-CHECKED nine), 8 (`posture_refusal` = trader.md §1) |
| §5.2 arithmetic property-tested | 6 (`tests/engine/property/test_gate_props.py`) |
| §5.3 state machine, `order_events` per transition | 4 (table + `transition`), 8 (`_ALLOWED`) |
| §5.3 one in flight; protective pre-empts; 15:55 cancel; 3 stop / 2 close then ALERT | 8, 10, 11 |
| §5.4 approver-only ✅/❌, warning-level removal, persisted re-arm, `is_protective_stop` + qty ≤ held, `replace_order` counting | 3 (predicate), 9 |
| §5.5 `placing` row before the call, reconcile by query, startup resolution, never resubmit | 8, 12 |
| §6 `proposals`, `orders`, `order_events`, `stops`, `approvals`, `trade_log` before placement | 4 |
| §6 exports of `status/DATE.md` and `trade-log.csv` in the old format | 15 |
| §6 store-repo commit and push | **out of scope, stated in Task 15** — spec §11 Phase 1 |
| §10 `FakeSchwabClient` write surface, paper mode, replay items | 1 (scripted fills), 16 (paper), 8 (`placing` replay), 9 (re-arm replay), 15 (old-format parse replay) |
| §10 contract test on the role registry | 13 |
| §11 Phase 2 exit criteria (entry + approval, stop verified ≤60 s, one `replace_order` ratchet, exit with the stop cancelled first, reconciled by query, matching `trade_log`) | 16, assertion by assertion |
| §11 `orders.enabled = false` reverts to protective-only | 12 |

Two spec items are deliberately **not** placed and say so where they arise: the Claude runner itself (Plan 0c) and the export mirror's git push (Phase 1). One `CLAUDE.md` amendment is *implied* by this plan and deliberately not made: §4.10's "the MCP exposes no `replace_order`" and the Verified-account-facts line "`replace_order` is absent" become false the moment Task 9 ships. Spec §14 already lists both for a Phase 3 §9 amendment with Chris's words quoted; **this plan does not amend the manual**, and Task 9's commit message announces the behaviour change so it is on the record.

**2. Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N", no "write tests for the above". Tasks 5, 8, 10, 11, 12 and 14 give interfaces, formulas, quoted source rules and a test list rather than complete bodies — each is a loop of 100–250 lines fully determined by its interface block plus the quoted rule text, and each has its failing-test step spelled out with the specific behaviours to assert. Tasks 1, 2, 3, 4, 6, 7, 15 and 16 carry full code.

**3. Type consistency.** `OrderSpec` (Task 3) is the argument type of `is_protective_stop` (3), `GateContext.spec` (6), `OrderMachine._spec_for` (8) and `ApprovalRequest.spec` (9). `GateResult(name, passed, detail, numbers)` is produced by every gate in 6 and 7 and stored as `proposals.gates_json` in 4. `OrderState`'s eighteen values are defined once in `orders_db.py` (4), CHECK-constrained in the same task's DDL, and consumed by `_ALLOWED` in 8. `client_key` is `str` everywhere — the `orders.client_key` UNIQUE column (4), the `stop_key`/`entry_key` helpers (2), `ApprovalRequest.client_key` (9). `PreviewResult.price` (1) is what `gate_notional_three_way` reads (7) and what `machine._reprice` re-previews against (8). `Poster` (9) is what `Notifier` (Phase 0b), `DiscordBot` (9) and `FirstWorking` (9) all satisfy. `TradeLogRow`'s field names equal `TRADE_LOG_COLUMNS` (4 and 15) — the export is a straight `getattr` per column, so a rename in one is a test failure in the other. The broker `OrderRow` (Phase 0a) and the store `OrderRecord` (4) are deliberately different names for deliberately different things: what the broker says, and what the engine intends.
