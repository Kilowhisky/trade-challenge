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
    with pytest.raises(SizingRefused, match=r"§3.1"):
        check_book(_book(), symbol="NEW", benchmark="SPY", notional=Decimal(1300),
                   is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)


def test_open_option_premium_is_capped_at_thirty_percent() -> None:
    opts = tuple(Holding(f"O{i}", Decimal(300), "SPY", True, Decimal(360)) for i in range(3))
    with pytest.raises(SizingRefused, match=r"§3.2"):
        check_book(_book(*opts), symbol="NEW", benchmark="SPY", notional=Decimal(100),
                   is_option=True, correlated=frozenset(), rules=RULES, reserve=RESERVE)


def test_the_correlation_cluster_is_a_cap_not_a_ban() -> None:
    same_sector = Holding("AAA", Decimal(1500), "XLK", False)
    # 1500 + 300 = 1800 <= 50% of 3700 = 1850: allowed.
    check_book(_book(same_sector), symbol="BBB", benchmark="XLK", notional=Decimal(300),
               is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)
    with pytest.raises(SizingRefused, match=r"§3.8"):
        check_book(_book(same_sector), symbol="BBB", benchmark="XLK", notional=Decimal(400),
                   is_option=False, correlated=frozenset(), rules=RULES, reserve=RESERVE)
    # SPY as a benchmark is "the market", not a sector: it clusters nothing.
    check_book(_book(Holding("AAA", Decimal(1500), "SPY", False)), symbol="BBB",
               benchmark="SPY", notional=Decimal(400), is_option=False,
               correlated=frozenset(), rules=RULES, reserve=RESERVE)
    # A measured correlation clusters across sectors.
    with pytest.raises(SizingRefused, match=r"§3.8"):
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
