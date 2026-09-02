from datetime import date
from decimal import Decimal
from pathlib import Path

from tc.rules import arith
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
R = Rules.load(REPO / "rules.yml")


def test_cap_floors_to_cent() -> None:
    # 35% of 3,781.06 = 1,323.371 -> floored, never rounded up
    assert arith.cap_dollars(Decimal("35"), Decimal("3781.06")) == Decimal("1323.37")


def test_stop_geometry_clamps_and_offsets() -> None:
    # ATR 2% -> 2.5x = 5% -> below the 8% floor -> trigger 8% below entry, limit 5% below trigger
    g = arith.stop_geometry(Decimal("100.00"), Decimal("2"), R)
    assert g.trigger_pct == Decimal("8")
    assert g.trigger == Decimal("92.00")
    assert g.limit == Decimal("87.40")
    # ATR 8% -> 20% -> capped at 15%
    g = arith.stop_geometry(Decimal("100.00"), Decimal("8"), R)
    assert g.trigger_pct == Decimal("15")
    assert g.trigger == Decimal("85.00")
    # ATR 4% -> 10%, inside the band
    g = arith.stop_geometry(Decimal("50.00"), Decimal("4"), R)
    assert g.trigger == Decimal("45.00") and g.limit == Decimal("42.75")


def test_halt_and_drawdown_use_account_value() -> None:
    hwm = Decimal("3800.00")
    assert arith.halt_threshold(hwm, R) == Decimal("3040.00")
    assert arith.drawdown_pct(Decimal("3758.00"), hwm) == Decimal("-1.11")
    assert arith.is_halted(Decimal("3040.00"), hwm, R) is True
    assert arith.is_halted(Decimal("3040.01"), hwm, R) is False


def test_legacy_hwm_conversion_is_dated() -> None:
    # every High-water mark written before 2026-08-31 is competition capital ($900 low)
    assert arith.legacy_hwm_to_account_basis(
        Decimal("2900.00"), date(2026, 8, 26), Decimal("900.00")
    ) == Decimal("3800.00")
    assert arith.legacy_hwm_to_account_basis(
        Decimal("3800.00"), date(2026, 8, 31), Decimal("900.00")
    ) == Decimal("3800.00")


def test_ratchet_never_lowers() -> None:
    assert arith.ratchet_hwm(Decimal("2900"), Decimal("2881.06")) == Decimal("2900")
    assert arith.ratchet_hwm(Decimal("2900"), Decimal("2950")) == Decimal("2950")


def test_reserve_and_pl() -> None:
    assert arith.reserve_cash(Decimal("2393.57"), Decimal("2393.57"), Decimal("0")) == Decimal("2393.57")
    assert arith.reserve_cash(Decimal("500"), Decimal("300"), Decimal("100")) == Decimal("400")
    assert arith.lifetime_pl(Decimal("990.64"), Decimal("34.79"), 29) == Decimal("-18.27")


def test_notional_tolerance() -> None:
    assert arith.notional(4, Decimal("50.375"), 1) == Decimal("201.50")
    assert arith.notional(2, Decimal("1.23"), 100) == Decimal("246.00")
    assert arith.notional_matches(Decimal("201.50"), Decimal("201.51"))
    assert not arith.notional_matches(Decimal("201.50"), Decimal("201.52"))


def test_dte_identity_holds_for_real_rules() -> None:
    assert arith.dte_floor_identity_holds(R)
