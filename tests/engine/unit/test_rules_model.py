from decimal import Decimal
from pathlib import Path

import pytest

from tc.money import D, cents, floor_cents
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]


def test_loads_real_rules_yml() -> None:
    r = Rules.load(REPO / "rules.yml")
    assert r.single_position_pct == Decimal("35")
    assert r.halt_multiple_of_hwm == Decimal("0.80")
    assert r.settlement_reserve_usd == Decimal("900.00")
    assert r.get("strategy", "option_min_delta") == Decimal("0.50")


def test_unknown_key_raises() -> None:
    r = Rules.load(REPO / "rules.yml")
    with pytest.raises(KeyError):
        r.get("manual", "does_not_exist")


def test_unknown_section_raises() -> None:
    r = Rules.load(REPO / "rules.yml")
    with pytest.raises(KeyError):
        r.get("strateyg", "option_min_delta")
    with pytest.raises(KeyError):
        r.get("Manual", "single_position_pct")


def test_rejects_nesting_deeper_than_two(tmp_path: Path) -> None:
    p = tmp_path / "rules.yml"
    p.write_text("manual:\n  a:\n    b: 1\nstrategy: {}\n")
    with pytest.raises(ValueError):
        Rules.load(p)


def test_rejects_non_numeric_value(tmp_path: Path) -> None:
    p = tmp_path / "rules.yml"
    p.write_text("manual:\n  a: yes\nstrategy: {}\n")
    with pytest.raises(ValueError):
        Rules.load(p)


def test_money_helpers() -> None:
    assert D("1.005") == Decimal("1.005")
    assert floor_cents(Decimal("1.009")) == Decimal("1.00")
    assert cents(Decimal("1.005")) == Decimal("1.00")  # banker's rounding
    assert cents(Decimal("1.015")) == Decimal("1.02")
