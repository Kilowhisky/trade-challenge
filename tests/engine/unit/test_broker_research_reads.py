"""The five reads research needs, against fixtures.

The one that carries history is `quotes_verbose`: v2 read these fields out of
two-space-indented verbose *text*, and `lastPrice` appears in BOTH the
`extended` and `quote` blocks with `extended` first, so a whole-body match
returned the after-hours print. Here the sub-objects are named and the
precedence is asserted rather than reverse-engineered.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tc.broker.fake import FakeBroker
from tc.broker.models import OptionContract, VerboseQuote
from tc.money import D

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker(FIX, NOW)


async def test_verbose_quote_prefers_regular_over_extended(broker: FakeBroker) -> None:
    q = (await broker.quotes_verbose(["MPC"]))["MPC"]
    assert q.price == D("368.83")           # NOT 999.99 from the extended block
    assert q.avg10_days_volume == D("2349452")
    assert q.fund_leverage_factor == D("0")  # a percentage, not a multiple
    assert q.week52_high == D("369.12")
    assert q.optionable is True
    assert q.last_earnings == "2026-08-04"   # truncated to 10 chars
    assert q.is_etf is False


async def test_verbose_quote_marks_unquotable_rather_than_zero(broker: FakeBroker) -> None:
    q = (await broker.quotes_verbose(["NOPRICE"]))["NOPRICE"]
    assert q.price is None
    assert q.last_earnings == ""


async def test_verbose_quote_zero_high_low_is_no_data_not_zero_range(broker: FakeBroker) -> None:
    q = (await broker.quotes_verbose(["SPY"]))["SPY"]
    assert q.high == D("0") and q.low == D("0")
    assert q.fund_leverage_factor == D("100")   # a 1x fund, not "leveraged"
    assert q.is_etf is True


def test_verbose_quote_falls_back_to_the_quote_block() -> None:
    """No `regular` sub-object at all: `quote.lastPrice` is the answer, and
    `extended.lastPrice` still is not."""
    q = VerboseQuote.from_payload(
        "XYZ",
        {"quote": {"lastPrice": 11.5}, "extended": {"lastPrice": 999.99}},
    )
    assert q.price == D("11.5")
    assert q.description == "" and q.optionable is False


def test_verbose_quote_ignores_a_null_regular_price() -> None:
    """A `regular` block present with a null price is the pre-market shape.
    `.get(key, fallback)` would return that null as the answer, because the
    key IS there — so the fall-through tests the value, not the key."""
    q = VerboseQuote.from_payload(
        "XYZ",
        {"regular": {"regularMarketLastPrice": None}, "quote": {"lastPrice": 11.5}},
    )
    assert q.price == D("11.5")


async def test_verbose_quote_filters_to_the_requested_symbols(broker: FakeBroker) -> None:
    rows = await broker.quotes_verbose(["SPY", "TQQQ", "NOT_IN_FIXTURE"])
    assert sorted(rows) == ["SPY", "TQQQ"]
    assert await broker.quotes_verbose([]) == {}


async def test_option_chain_flattens_both_maps(broker: FakeBroker) -> None:
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
    assert first.bid == D("2.05") and first.ask == D("2.20")
    assert first.implied_volatility == D("20.8")


def test_option_contract_sentinel_delta_is_unknown_not_a_number() -> None:
    """Schwab reports -999.0 for a contract it cannot price. A delta BAND test
    would reject that as too low, which reads as a real answer; None is the
    honest record and makes the caller decide."""
    c = OptionContract.from_payload(
        {
            "symbol": "CSX   261016C00047500", "putCall": "PUT", "strikePrice": 47.5,
            "delta": -999.0, "volatility": -999.0,
            "expirationDate": "2026-10-16T20:00:00.000+00:00",
        }
    )
    assert c.delta is None and c.implied_volatility is None
    assert c.kind == "PUT"
    # Absent numeric fields are zero, not a crash: the chain is a screen input.
    assert c.bid == D("0") and c.open_interest == 0 and c.days_to_expiration == 0


async def test_expiration_chain(broker: FakeBroker) -> None:
    exps = await broker.expiration_chain("CSX")
    assert [e.expiry for e in exps] == [date(2026, 10, 16), date(2026, 11, 20)]
    assert exps[0].standard is True
    assert exps[0].days_to_expiration == 39


async def test_instruments(broker: FakeBroker) -> None:
    rows = await broker.instruments("CSX", "symbol-search")
    assert rows[0].symbol == "CSX" and rows[0].exchange == "NASDAQ"
    assert rows[0].asset_type == "EQUITY"
    assert rows[0].cusip == "126408103"


async def test_movers(broker: FakeBroker) -> None:
    rows = await broker.movers("EQUITY_ALL", "up")
    assert [r.symbol for r in rows] == ["AAAA", "BBBB"]
    assert rows[0].net_percent_change == D("10.8") and rows[0].volume == 4500000
    assert rows[0].last == D("12.34") and rows[0].net_change == D("1.20")
