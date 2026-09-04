"""CLAUDE.md §3.3 (option 5-DTE close / OCC auto-exercise) and §3.5
(leveraged-ETF 5-session hold): the clocks are arithmetic on the ledger, not
a model reading a calendar. Warn offsets are tick.md §C watch 7 operational
constants, not rule numbers — asserted here against ``OPTION_WARN_DAYS_BEFORE_CLOSE``
and ``LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE`` rather than bare literals."""

from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D  # noqa: N817 — brevity in a Decimal-heavy fixture table
from pathlib import Path

import pytest

from tc.broker.models import AccountSnapshot, Position
from tc.clock import ET
from tc.loops.clocks import (
    LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE,
    OPTION_WARN_DAYS_BEFORE_CLOSE,
    ClockAlert,
    OptionRef,
    dte,
    parse_osi,
    run_clocks,
)
from tc.rules.model import Rules
from tc.store.db import Store

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
TODAY = date(2026, 9, 4)  # a Friday


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


def _weekdays_between(a: date, b: date) -> int:
    """Fake ``trading_days_between``: counts weekday dates strictly after
    ``a`` up to and including ``b``. Ignores market holidays deliberately —
    the real implementation is Task 6/10's concern, not this clock's."""
    n = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _equity(symbol: str, quantity: int = 10) -> Position:
    return Position(
        symbol=symbol, asset_type="EQUITY", quantity=quantity, average_price=D("10.00"),
        market_value=D("100.00"), day_pl=D("0"), settled_quantity=quantity,
    )


def _option(symbol: str, quantity: int = 1) -> Position:
    return Position(
        symbol=symbol, asset_type="OPTION", quantity=quantity, average_price=D("1.00"),
        market_value=D("100.00"), day_pl=D("0"), settled_quantity=quantity,
    )


def _osi(root: str, expiry: date, kind: str, strike: D) -> str:
    padded = root.ljust(6)
    return f"{padded}{expiry:%y%m%d}{kind}{int(strike * 1000):08d}"


def _account(symbol: str, seen_on: date) -> AccountSnapshot:
    # Midday ET, not midnight UTC: a midnight-UTC stamp crosses the date line
    # into the prior ET calendar day for the whole EDT/EST year, which would
    # silently shift first_seen's ET date by one and desync these fixtures
    # from the sessions_held arithmetic they're meant to pin down.
    read_at = datetime.combine(seen_on, time(12, 0), tzinfo=ET)
    return AccountSnapshot(
        account_hash="H", read_at=read_at, liquidation_value=D("1000"),
        cash_available_for_trading=D("500"), unsettled_cash=D("0"), cash_balance=D("500"),
        cash_call=D("0"), is_closing_only_restricted=False,
        positions=[_equity(symbol)],
    )


# --- parse_osi / dte --------------------------------------------------------

def test_parse_osi_reads_padded_root_date_kind_strike() -> None:
    ref = parse_osi("AMH   261016P00030000")
    assert ref == OptionRef(underlying="AMH", expiry=date(2026, 10, 16), kind="P", strike=D("30.000"))


def test_parse_osi_returns_none_for_plain_equity_symbol() -> None:
    assert parse_osi("AMH") is None


def test_parse_osi_round_trips_a_built_symbol() -> None:
    sym = _osi("QQQ", date(2026, 12, 18), "C", D("450.500"))
    ref = parse_osi(sym)
    assert ref == OptionRef(underlying="QQQ", expiry=date(2026, 12, 18), kind="C", strike=D("450.500"))


def test_dte_is_calendar_days() -> None:
    assert dte(date(2026, 10, 16), date(2026, 10, 11)) == 5


# --- run_clocks: options -----------------------------------------------------

async def test_option_at_close_at_dte_closes(store: Store) -> None:
    close_dte = RULES.option_close_at_dte
    sym = _osi("AMH", TODAY + timedelta(days=close_dte), "P", D("30.000"))
    alerts = await run_clocks(store, RULES, [_option(sym)], TODAY, _weekdays_between, frozenset())
    assert alerts == [ClockAlert(symbol=sym, kind="option_close", detail=f"{close_dte} DTE (close at {close_dte})")]


async def test_option_at_close_plus_warn_offset_warns(store: Store) -> None:
    close_dte = RULES.option_close_at_dte
    warn_at = close_dte + OPTION_WARN_DAYS_BEFORE_CLOSE
    sym = _osi("AMH", TODAY + timedelta(days=warn_at), "P", D("30.000"))
    alerts = await run_clocks(store, RULES, [_option(sym)], TODAY, _weekdays_between, frozenset())
    assert len(alerts) == 1 and alerts[0].kind == "option_warn" and alerts[0].symbol == sym


async def test_option_past_warn_offset_is_silent(store: Store) -> None:
    close_dte = RULES.option_close_at_dte
    quiet_at = close_dte + OPTION_WARN_DAYS_BEFORE_CLOSE + 1
    sym = _osi("AMH", TODAY + timedelta(days=quiet_at), "P", D("30.000"))
    alerts = await run_clocks(store, RULES, [_option(sym)], TODAY, _weekdays_between, frozenset())
    assert alerts == []


# --- run_clocks: leveraged ETFs ---------------------------------------------

async def test_leveraged_at_max_hold_closes(store: Store) -> None:
    max_hold = RULES.leveraged_max_hold_sessions
    # 2026-08-31 (Mon) -> 4 weekdays elapsed by 2026-09-04 (Fri) -> sessions_held = 5
    first = date(2026, 8, 31)
    assert _weekdays_between(first, TODAY) + 1 == max_hold
    await store.record_account(_account("TQQQ", first))
    alerts = await run_clocks(
        store, RULES, [_equity("TQQQ")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert len(alerts) == 1 and alerts[0].kind == "leveraged_close" and alerts[0].symbol == "TQQQ"


async def test_leveraged_at_warn_offset_warns(store: Store) -> None:
    warn_hold = RULES.leveraged_max_hold_sessions - LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE
    # 2026-09-02 (Wed) -> 2 weekdays elapsed by 2026-09-04 (Fri) -> sessions_held = 3
    first = date(2026, 9, 2)
    assert _weekdays_between(first, TODAY) + 1 == warn_hold
    await store.record_account(_account("TQQQ", first))
    alerts = await run_clocks(
        store, RULES, [_equity("TQQQ")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert len(alerts) == 1 and alerts[0].kind == "leveraged_warn" and alerts[0].symbol == "TQQQ"


async def test_leveraged_below_warn_offset_is_silent(store: Store) -> None:
    first = date(2026, 9, 3)  # sessions_held = 2, below warn_hold=3
    await store.record_account(_account("TQQQ", first))
    alerts = await run_clocks(
        store, RULES, [_equity("TQQQ")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert alerts == []


async def test_leveraged_never_snapshotted_is_treated_as_day_one(store: Store) -> None:
    """No account_snapshots row for the symbol: first_seen is None, so the
    clock treats it as first seen today (day 1) rather than raising or
    silently ignoring the position."""
    alerts = await run_clocks(
        store, RULES, [_equity("TQQQ")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert alerts == []


async def test_non_leveraged_equity_never_alerts(store: Store) -> None:
    first = date(2026, 8, 1)  # long held, but not in the leveraged set
    await store.record_account(_account("AMH", first))
    alerts = await run_clocks(
        store, RULES, [_equity("AMH")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert alerts == []


async def test_position_gets_at_most_one_alert_close_wins(store: Store) -> None:
    """A position exactly at both the close and warn thresholds — impossible
    for a single sane pair of constants, but the precedence rule must still
    hold at the close boundary itself: only one alert, and it is *_close."""
    max_hold = RULES.leveraged_max_hold_sessions
    first = date(2026, 8, 24)  # well past max_hold sessions ago
    await store.record_account(_account("TQQQ", first))
    held = _weekdays_between(first, TODAY) + 1
    assert held > max_hold
    alerts = await run_clocks(
        store, RULES, [_equity("TQQQ")], TODAY, _weekdays_between, frozenset({"TQQQ"})
    )
    assert [a.kind for a in alerts] == ["leveraged_close"]
