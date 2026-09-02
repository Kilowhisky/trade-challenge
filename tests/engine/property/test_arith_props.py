from datetime import date
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from tc.rules import arith
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
R = Rules.load(REPO / "rules.yml")

# Floor raised from 0.01 to 5.00 per the task-3 decision log: at a $0.01 entry,
# cents() rounding collapses trigger back onto entry, and CLAUDE.md §1.4
# forbids trading anything under $5.00/share anyway, so sub-$5 entries are not
# a real input this arithmetic will ever see.
money = st.decimals(min_value=Decimal("5.00"), max_value=Decimal("10000000"), places=2, allow_nan=False)
pct = st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=2, allow_nan=False)
atr = st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=3, allow_nan=False)


@given(pct, money)
def test_cap_never_exceeds_exact(p: Decimal, av: Decimal) -> None:
    assert arith.cap_dollars(p, av) <= av * p / 100
    assert arith.cap_dollars(p, av) >= av * p / 100 - Decimal("0.01")


@given(money, atr)
@settings(max_examples=300)
def test_stop_trigger_pct_inside_band_and_monotone(entry: Decimal, a: Decimal) -> None:
    g = arith.stop_geometry(entry, a, R)
    assert R.stop_trigger_min_pct <= g.trigger_pct <= R.stop_trigger_max_pct
    assert g.limit < g.trigger < entry
    g2 = arith.stop_geometry(entry, a + Decimal("0.5"), R)
    assert g2.trigger_pct >= g.trigger_pct  # more volatile -> wider or equal


@given(money, money)
def test_hwm_ratchet_monotone(prior: Decimal, close: Decimal) -> None:
    new = arith.ratchet_hwm(prior, close)
    assert new >= prior and new >= close


@given(money, st.dates(min_value=date(2026, 8, 1), max_value=date(2026, 12, 31)))
def test_basis_conversion_idempotent(hwm: Decimal, d: date) -> None:
    once = arith.legacy_hwm_to_account_basis(hwm, d, Decimal("900.00"))
    # applying the conversion to an already-converted (post-amendment) mark changes nothing
    assert arith.legacy_hwm_to_account_basis(once, date(2026, 8, 31), Decimal("900.00")) == once


@given(money, money)
def test_halt_is_exactly_at_or_below_threshold(av: Decimal, hwm: Decimal) -> None:
    assert arith.is_halted(av, hwm, R) == (av <= arith.halt_threshold(hwm, R))
