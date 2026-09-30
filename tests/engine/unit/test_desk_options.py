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
