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
from typing import Any, cast

import pytest
from schwab.client import AsyncClient

from tc.broker.client import BrokerError, SchwabBroker
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


async def test_verbose_quote_zero_high_low_is_carried_through_not_nulled(broker: FakeBroker) -> None:
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
    assert first.open_interest == 1500
    assert first.days_to_expiration == 39 and first.expiry == date(2026, 10, 16)
    assert first.bid == D("2.05") and first.ask == D("2.20")


async def test_option_chain_keeps_sub_cent_precision(broker: FakeBroker) -> None:
    """A delta and an implied volatility are not money.

    `models._dec` quantizes to the cent because it reads prices; these fields
    go through `_num` instead. Asserting the exact Decimals is what makes a
    refactor back to `_dec` a red test rather than a silent 0.6123 -> 0.61 --
    which would move a contract across a delta band boundary without anyone
    seeing a number change in a log.
    """
    view = await broker.option_chain(
        "CSX", date(2026, 10, 1), date(2026, 11, 1), 6, "CALL"
    )
    first = view.contracts[0]
    assert first.delta == D("0.6123")
    assert first.implied_volatility == D("20.847")
    # Same arithmetic, stated as the property that actually matters.
    assert first.delta != D("0.61") and first.implied_volatility != D("20.85")


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


class _Resp:
    """Enough of httpx.Response for `_raise_for`."""

    status_code = 200
    text = ""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _CapturingClient:
    """A stand-in that records what the wrapper handed to schwab-py.

    The enum namespaces are the REAL ones off `AsyncClient`, because the thing
    under test is precisely how a caller's vocabulary maps onto them. A hand
    written stub enum would let the mapping be wrong in both places at once.
    """

    Movers = AsyncClient.Movers
    Options = AsyncClient.Options
    Instrument = AsyncClient.Instrument

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def get_movers(self, index: Any, sort_order: Any = None) -> _Resp:
        self.seen = {"index": index, "sort_order": sort_order}
        return _Resp({"screeners": []})

    async def get_option_chain(self, symbol: str, **kw: Any) -> _Resp:
        self.seen = dict(kw, symbol=symbol)
        return _Resp({"callExpDateMap": {}, "putExpDateMap": {}})

    async def get_instruments(self, symbols: Any, projection: Any) -> _Resp:
        self.seen = {"symbols": symbols, "projection": projection}
        return _Resp({"instruments": []})


def _stub_broker() -> tuple[SchwabBroker, _CapturingClient]:
    """A SchwabBroker whose client is already built, so nothing reads a token
    and nothing touches the network."""
    broker = SchwabBroker(cast(Any, None), "k", "s")
    stub = _CapturingClient()
    broker._client = cast(Any, stub)
    return broker, stub


async def test_movers_index_name_maps_to_the_dollar_prefixed_value() -> None:
    """`Movers.Index.DJI` is `"$DJI"` -- name and value differ.

    Looking the index up by VALUE would raise on every `$`-prefixed index
    while quietly working for `EQUITY_ALL`, whose name and value coincide. So
    the one index everybody tests with would pass and the rest would not.
    """
    broker, stub = _stub_broker()
    await broker.movers("DJI", "up")
    assert stub.seen["index"].value == "$DJI"
    assert stub.seen["sort_order"].value == "PERCENT_CHANGE_UP"

    await broker.movers("EQUITY_ALL", "down")
    assert stub.seen["index"].value == "EQUITY_ALL"
    assert stub.seen["sort_order"].value == "PERCENT_CHANGE_DOWN"


async def test_instruments_projection_maps_by_wire_value() -> None:
    """The opposite convention, and deliberately so: a projection is written
    `symbol-search` everywhere a human writes one, never `SYMBOL_SEARCH`."""
    broker, stub = _stub_broker()
    await broker.instruments("CSX", "symbol-search")
    assert stub.seen["projection"].value == "symbol-search"


async def test_contract_type_maps_by_name() -> None:
    broker, stub = _stub_broker()
    await broker.option_chain("CSX", date(2026, 10, 1), date(2026, 11, 1), 6, "PUT")
    assert stub.seen["contract_type"].value == "PUT"
    assert stub.seen["strike_count"] == 6


async def test_a_bad_enum_key_is_a_broker_error_not_a_bare_key_error() -> None:
    """These are caller mistakes, but they surface from inside schwab-py as
    KeyError/ValueError -- which nothing catching broker faults would handle,
    so an unknown index would crash a job that an unreachable broker only
    degrades."""
    broker, _ = _stub_broker()
    with pytest.raises(BrokerError, match="mover index"):
        await broker.movers("NOT_AN_INDEX", "up")
    with pytest.raises(BrokerError, match="mover direction"):
        await broker.movers("DJI", cast(Any, "sideways"))
    with pytest.raises(BrokerError, match="projection"):
        await broker.instruments("CSX", "not-a-projection")
    with pytest.raises(BrokerError, match="contract type"):
        await broker.option_chain(
            "CSX", date(2026, 10, 1), date(2026, 11, 1), 6, cast(Any, "STRADDLE")
        )
