"""The read tools answer in typed fields.

These call the registered functions directly rather than over HTTP. The wire
-- mounts, bearer gate, lifespan -- is `test_mcp_server.py`'s subject and is
tested there against the real app; what is under test here is the *answer*:
which fields come back, what a missing symbol looks like, and that a dead
token is a `ToolError` the model can read rather than a traceback it cannot.

Every broker read is served by `FakeBroker` over the recorded fixtures, so a
test asserting a price is asserting against a payload Schwab actually sent.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.client import BrokerError, BrokerUnauthorized, MoverDirection
from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, Mover, OrderRow, VerboseQuote
from tc.config import Settings, load_settings
from tc.mcp import tools_read
from tc.mcp.registry import DECIDE_ONLY_READ_TOOLS, READ_TOOLS, Role
from tc.mcp.server import McpDeps
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import SessionStatusRow, Store, TickRow

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "tests" / "engine" / "fixtures" / "broker"
NOW = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)  # 14:00 ET, a closed Monday
HASH = "HASH_REDACTED"

CONFIG = """
engine:
  data_dir: {data}
  repo_dir: {repo}
  research_dir: {research}
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""

ENV = """
TC_SCHWAB_APP_KEY=k
TC_SCHWAB_APP_SECRET=s
"""


def tool(server: FastMCP, name: str) -> Any:
    """The callable behind a registered FastMCP tool."""
    registered = server._tool_manager.get_tool(name)
    assert registered is not None, f"{name} is not registered"
    return registered.fn


def _settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        CONFIG.format(data=tmp_path, repo=REPO, research=tmp_path / "research")
    )
    env = tmp_path / ".env"
    env.write_text(ENV)
    return load_settings(cfg, env)


def _server(store: Store, broker: FakeBroker, tmp_path: Path, role: Role) -> FastMCP:
    """One role's read tools on a bare FastMCP.

    Not `build_servers`: that assembles every module's registrar and refuses
    to finish while any declared tool is unbuilt, which would make this file
    fail for a gap in a neighbouring module. What is under test is what
    `tools_read.register` puts on a server, so that is what is built.
    """
    deps = McpDeps(
        store=store,
        broker=broker,
        docs=DocStore(tmp_path / "research", store),
        rules=Rules.load(REPO / "rules.yml"),
        settings=_settings(tmp_path),
        clock=lambda: NOW,
    )
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_read.register(server, deps, role)
    return server


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "engine.db")
    await s.open()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def read_server(store: Store, tmp_path: Path) -> FastMCP:
    return _server(store, FakeBroker(FIX, NOW), tmp_path, "research")


@pytest.fixture
async def decide_server(store: Store, tmp_path: Path) -> FastMCP:
    return _server(store, FakeBroker(FIX, NOW), tmp_path, "decide")


@pytest.fixture
async def read_server_unauthorized(store: Store, tmp_path: Path) -> FastMCP:
    """A broker whose token is dead: `FakeBroker` raises `BrokerUnauthorized`
    for every read when the marker file is present."""
    blind = tmp_path / "blind-fixtures"
    blind.mkdir()
    (blind / "unauthorized").write_text("")
    return _server(store, FakeBroker(blind, NOW), tmp_path, "research")


@pytest.fixture
def merged_quote_fixtures(tmp_path: Path) -> Path:
    """The recorded fixtures, with AMH present in BOTH quote reads.

    The committed `quotes-verbose.json` holds the universe sweep's symbols and
    `quotes.json` holds AMH; no symbol is in both, so the merge the `quotes`
    tool performs is untestable against them as they stand. Copying rather
    than editing them keeps every other task's fixtures exactly as recorded.
    """
    out = tmp_path / "merged-fixtures"
    shutil.copytree(FIX, out)
    verbose = json.loads((out / "quotes-verbose.json").read_text())
    verbose["AMH"] = {
        "assetMainType": "EQUITY",
        "quote": {"52WeekHigh": 40.5, "lastPrice": 34.16},
        "reference": {"description": "AMERICAN HOMES 4 RENT", "optionable": True},
        "fundamental": {"avg10DaysVolume": 2349452},
    }
    (out / "quotes-verbose.json").write_text(json.dumps(verbose))
    return out


@pytest.fixture
async def seeded_store(store: Store) -> Store:
    await store.write_session_status(
        SessionStatusRow(
            date=NOW.date(),
            close_value=D("3781.06"),
            hwm=D("3800.00"),
            halt=D("3040.00"),
            drawdown_pct=D("-0.50"),
            level="OK",
            prior_hwm=D("3800.00"),
            ratcheted=False,
            intraday_high=None,
        )
    )
    await store.append_tick(
        TickRow(
            at_et="2026-09-07T14:00:00",
            state="POST",
            account_value=D("3781.06"),
            comp_capital=D("2881.06"),
            hwm=D("3800.00"),
            drawdown_pct=D("-0.50"),
            level="OK",
            positions=1,
            stops=1,
            orders=1,
            settled=D("2393.57"),
            unsettled=D("0.00"),
            reserve=D("900.00"),
            flags="",
            note="fixture",
        )
    )
    return store


@pytest.fixture
async def reconciled_store(store: Store) -> Store:
    broker = FakeBroker(FIX, NOW)
    account: AccountSnapshot = await broker.account(HASH)
    orders: list[OrderRow] = await broker.orders(HASH, NOW, NOW)
    await store.record_account(account)
    await store.record_orders(HASH, orders, NOW)
    return store


# --- the clock ---------------------------------------------------------


async def test_get_datetime_is_eastern(read_server: FastMCP) -> None:
    out = await tool(read_server, "get_datetime")()
    assert out.date == "2026-09-07"
    assert out.time_et == "14:00:00"
    assert out.tz == "America/New_York"
    assert out.iso_utc.startswith("2026-09-07T18:00:00")


async def test_market_hours_reports_the_window_even_when_closed(
    read_server: FastMCP,
) -> None:
    """A closed-day payload carries no `sessionHours`, and an after-hours read
    of a trading day carries one that `MarketWindow` discards. Either way the
    caller still needs to know when the session runs, so the window and the
    trading-day flag are two separate answers."""
    out = await tool(read_server, "market_hours")(date="2026-09-07")
    assert out.date == "2026-09-07"
    assert out.is_trading_day is False
    assert out.rth_start_et == "09:30:00"
    assert out.rth_end_et == "16:00:00"


async def test_market_hours_on_a_trading_day_uses_the_brokers_window(
    read_server: FastMCP,
) -> None:
    out = await tool(read_server, "market_hours")(date="2026-09-04")
    assert out.is_trading_day is True
    assert (out.rth_start_et, out.rth_end_et) == ("09:30:00", "16:00:00")


async def test_market_hours_rejects_a_date_that_is_not_a_date(
    read_server: FastMCP,
) -> None:
    with pytest.raises(ToolError):
        await tool(read_server, "market_hours")(date="next tuesday")


# --- quotes ------------------------------------------------------------


async def test_quotes_are_compact_and_name_the_symbols_that_did_not_answer(
    read_server: FastMCP,
) -> None:
    out = await tool(read_server, "quotes")(symbols=["AMH", "NOSUCHSYM"])
    assert [q.symbol for q in out.quotes] == ["AMH"]
    assert out.missing == ["NOSUCHSYM"]
    assert (out.quotes[0].last, out.quotes[0].bid, out.quotes[0].ask) == (
        "34.16",
        "34.15",
        "34.17",
    )
    # The fundamentals block is what the two-tier universe design exists to
    # keep out of context.
    assert not hasattr(out.quotes[0], "avg10_days_volume")


async def test_quotes_carry_the_reference_fields_a_screen_needs(
    store: Store, merged_quote_fixtures: Path, tmp_path: Path
) -> None:
    """§1.4 and §2 are asked of names that are not in the weekly universe
    table, so `optionable` and the 52-week high have to be reachable per
    symbol."""
    server = _server(store, FakeBroker(merged_quote_fixtures, NOW), tmp_path, "research")
    out = await tool(server, "quotes")(symbols=["AMH"])
    assert out.quotes[0].week52_high == "40.5"
    assert out.quotes[0].optionable is True


async def test_quotes_do_not_invent_reference_fields_they_did_not_get(
    read_server: FastMCP,
) -> None:
    out = await tool(read_server, "quotes")(symbols=["AMH"])
    assert out.quotes[0].week52_high is None
    assert out.quotes[0].optionable is None


async def test_a_failed_reference_read_still_returns_the_prices(
    store: Store, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two reads, two failure domains. Losing the reference block must not
    throw away the price and the timestamp already in hand -- those are what
    the stale-quote gate and every entry check actually run on."""

    class HalfBlind(FakeBroker):
        async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
            raise BrokerError("500: reference service unavailable")

    server = _server(store, HalfBlind(FIX, NOW), tmp_path, "research")
    with caplog.at_level(logging.WARNING):
        out = await tool(server, "quotes")(symbols=["AMH"])
    assert out.partial is True
    assert out.quotes[0].last == "34.16"
    assert out.quotes[0].week52_high is None and out.quotes[0].optionable is None
    assert "BrokerError" in caplog.text


async def test_a_blind_reference_read_is_a_refusal_not_a_partial(
    store: Store, tmp_path: Path
) -> None:
    """Degraded and blind are different answers. A dead token will fail the
    next read too, and a caller told "partial" would carry on regardless."""

    class BlindReference(FakeBroker):
        async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
            raise BrokerUnauthorized("401")

    server = _server(store, BlindReference(FIX, NOW), tmp_path, "research")
    with pytest.raises(ToolError) as e:
        await tool(server, "quotes")(symbols=["AMH"])
    assert str(e.value) == "broker blind: token absent/dead"


async def test_quotes_are_not_partial_when_both_reads_answer(
    store: Store, merged_quote_fixtures: Path, tmp_path: Path
) -> None:
    server = _server(store, FakeBroker(merged_quote_fixtures, NOW), tmp_path, "research")
    out = await tool(server, "quotes")(symbols=["AMH"])
    assert out.partial is False


async def test_quotes_refuse_an_unbounded_symbol_list(read_server: FastMCP) -> None:
    """The cap is enforced in the body, not only in the schema FastMCP shows
    the model: context is the budget a read tool spends, and the caller cannot
    see the bill before it arrives."""
    with pytest.raises(ToolError):
        await tool(read_server, "quotes")(symbols=[f"S{i}" for i in range(51)])


# --- prices ------------------------------------------------------------


async def test_price_history_returns_the_last_n_daily_bars(read_server: FastMCP) -> None:
    out = await tool(read_server, "price_history")(symbol="AMH", days=2)
    assert out.symbol == "AMH"
    assert len(out.bars) == 2
    assert out.bars[-1].close == "34.20"
    assert out.bars[-1].date == "2025-09-02"


async def test_option_chain_passes_the_window_through(read_server: FastMCP) -> None:
    out = await tool(read_server, "option_chain")(
        symbol="CSX",
        from_date="2026-10-01",
        to_date="2026-11-01",
        strike_count=6,
        contract_type="CALL",
    )
    assert out.underlying_price == D("48.99")
    assert len(out.contracts) == 2


async def test_option_chain_renders_every_decimal_as_a_string_on_the_wire(
    read_server: FastMCP,
) -> None:
    """The model reads JSON, not Python. A Decimal that serialised as a float
    would put 48.989999999999995 in front of a rule check."""
    out = await tool(read_server, "option_chain")(
        symbol="CSX",
        from_date="2026-10-01",
        to_date="2026-11-01",
        strike_count=6,
        contract_type="CALL",
    )
    wire = json.loads(out.model_dump_json())
    assert wire["underlying_price"] == "48.99"
    assert isinstance(wire["contracts"][0]["strike"], str)


async def test_expiration_chain_lists_the_expiries(read_server: FastMCP) -> None:
    out = await tool(read_server, "expiration_chain")(symbol="CSX")
    assert out.symbol == "CSX"
    assert [e.expiry.isoformat() for e in out.expirations] == ["2026-10-16", "2026-11-20"]


async def test_instruments_answers_the_identifier_round_trip(read_server: FastMCP) -> None:
    out = await tool(read_server, "instruments")(query="CSX", projection="symbol-search")
    assert [i.symbol for i in out.instruments] == ["CSX"]
    assert out.instruments[0].exchange == "NASDAQ"


async def test_movers_returns_the_screener_rows(read_server: FastMCP) -> None:
    out = await tool(read_server, "movers")(index="EQUITY_ALL", direction="up")
    assert [m.symbol for m in out.movers] == ["AAAA", "BBBB"]


async def test_a_broker_error_is_one_line_that_names_only_its_class(
    store: Store, tmp_path: Path
) -> None:
    """An unknown mover index is rejected by the broker, not by the tool --
    and whatever the broker says about it stays there. A `BrokerError` message
    is a Schwab response body or a URL, so the caller gets the class name."""

    class Rejecting(FakeBroker):
        async def movers(self, index: str, direction: MoverDirection) -> list[Mover]:
            raise BrokerError(f"unknown mover index: {index!r} https://api.example/x?token=abc")

    server = _server(store, Rejecting(FIX, NOW), tmp_path, "research")
    with pytest.raises(ToolError) as e:
        await tool(server, "movers")(index="NOT_AN_INDEX", direction="up")
    assert str(e.value) == "movers read failed: BrokerError"


# --- the store-backed reads --------------------------------------------


async def test_rules_returns_rules_yml_as_strings(read_server: FastMCP) -> None:
    out = await tool(read_server, "rules")()
    assert out.manual["option_min_dte"] == "18"
    assert out.strategy["scout_entry_window_min_days"] == "21"
    assert out.source.endswith("rules.yml")


async def test_status_latest_reports_the_mark_as_a_field(
    read_server: FastMCP, seeded_store: Store
) -> None:
    out = await tool(read_server, "status_latest")()
    assert out.session_status is not None
    assert out.session_status.hwm == "3800.00"
    assert out.session_status.level == "OK"
    assert out.last_tick is not None
    assert out.last_tick.account_value == "3781.06"
    assert out.last_tick.state == "POST"


async def test_status_latest_before_any_close_says_so(read_server: FastMCP) -> None:
    out = await tool(read_server, "status_latest")()
    assert out.session_status is None
    assert out.last_tick is None


# --- the book ----------------------------------------------------------


async def test_book_is_absent_from_the_research_role(
    read_server: FastMCP, decide_server: FastMCP
) -> None:
    assert read_server._tool_manager.get_tool("book") is None
    assert decide_server._tool_manager.get_tool("book") is not None


async def test_book_reports_positions_and_their_stops(
    decide_server: FastMCP, reconciled_store: Store
) -> None:
    out = await tool(decide_server, "book")()
    assert [p.symbol for p in out.positions] == ["AMH"]
    assert out.positions[0].quantity == 29
    assert [(s.symbol, s.stop_price, s.limit_price) for s in out.stops] == [
        ("AMH", "32.01", "30.40")
    ]
    assert out.account_value == "3781.06"
    assert out.settled_cash == "2393.57"
    assert out.restricted is False


async def test_book_before_any_reconcile_is_empty_not_a_crash(
    decide_server: FastMCP,
) -> None:
    out = await tool(decide_server, "book")()
    assert out.positions == [] and out.stops == []
    assert out.read_at is None


# --- failure ------------------------------------------------------------


async def test_a_broker_failure_is_a_tool_error_not_a_crash(
    read_server_unauthorized: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await tool(read_server_unauthorized, "quotes")(symbols=["AMH"])
    assert "blind" in str(e.value)


async def test_a_blind_broker_never_leaks_the_underlying_message(
    read_server_unauthorized: FastMCP,
) -> None:
    """The exception text from a real 401 is a Schwab response body. One line,
    no payload."""
    with pytest.raises(ToolError) as e:
        await tool(read_server_unauthorized, "price_history")(symbol="AMH", days=2)
    assert str(e.value) == "broker blind: token absent/dead"


async def test_the_research_role_gets_exactly_the_declared_read_tools(
    read_server: FastMCP,
) -> None:
    """Against `registry.py`, not against a list repeated here. `build_servers`
    refuses a role that registers a name the table does not declare AND a role
    whose declared name nobody supplies, so this equality is what lets the two
    checks pass when the wiring assembles the roles."""
    names = {t.name for t in read_server._tool_manager.list_tools()}
    assert names == set(READ_TOOLS)


async def test_the_decide_role_gets_the_read_tools_and_the_book(
    decide_server: FastMCP,
) -> None:
    names = {t.name for t in decide_server._tool_manager.list_tools()}
    assert names == set(READ_TOOLS) | set(DECIDE_ONLY_READ_TOOLS)
