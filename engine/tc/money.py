"""Decimal helpers. Money is never a float anywhere in the engine."""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


def D(x: str | int | Decimal) -> Decimal:  # noqa: N802 — deliberate short name
    return x if isinstance(x, Decimal) else Decimal(str(x))


def floor_cents(d: Decimal) -> Decimal:
    """Round DOWN to the cent. Used for every cap so a cap never rounds up."""
    return d.quantize(CENT, rounding=ROUND_FLOOR)


def cents(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_EVEN)
