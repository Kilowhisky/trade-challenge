"""Pure arithmetic for every cap and threshold the engine enforces.

Ported from scripts/pre-order-check.sh (caps, notional, price floor),
.claude/commands/tick.md §B5/§C (drawdown, reserve, lifetime P/L) and
.claude/agents/session-close.md §3 (ratchet, basis conversion). No I/O, no
floats, no rule numbers: every parameter comes in through `Rules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tc.money import cents, floor_cents
from tc.rules.model import Rules

# The §3.6 re-anchor date: every High-water mark recorded before it is a
# competition-capital figure (account value - reserve) and must be CONVERTED,
# never merely compared (CLAUDE.md §3.6 migration note).
BASIS_AMENDMENT_DATE = date(2026, 8, 31)

HUNDRED = Decimal("100")


def cap_dollars(pct: Decimal, account_value: Decimal) -> Decimal:
    return floor_cents(account_value * pct / HUNDRED)


@dataclass(frozen=True)
class StopGeometry:
    trigger: Decimal
    limit: Decimal
    trigger_pct: Decimal  # percent below entry, after the clamp


def stop_geometry(entry: Decimal, daily_atr_pct: Decimal, rules: Rules) -> StopGeometry:
    """CLAUDE.md §3.4: trigger_pct = clamp(multiple x ATR%, min, max); limit sits
    limit_pct below the trigger."""
    raw = rules.stop_atr_multiple * daily_atr_pct
    trigger_pct = min(max(raw, rules.stop_trigger_min_pct), rules.stop_trigger_max_pct)
    trigger = cents(entry * (HUNDRED - trigger_pct) / HUNDRED)
    limit = cents(trigger * (HUNDRED - rules.stop_limit_pct_below_trigger) / HUNDRED)
    return StopGeometry(trigger=trigger, limit=limit, trigger_pct=trigger_pct)


def halt_threshold(hwm: Decimal, rules: Rules) -> Decimal:
    return cents(hwm * rules.halt_multiple_of_hwm)


def drawdown_pct(account_value: Decimal, hwm: Decimal) -> Decimal:
    if hwm <= 0:
        raise ValueError("hwm must be positive")
    return cents((account_value - hwm) / hwm * HUNDRED)


def is_halted(account_value: Decimal, hwm: Decimal, rules: Rules) -> bool:
    return account_value <= halt_threshold(hwm, rules)


def ratchet_hwm(prior: Decimal, close: Decimal) -> Decimal:
    return max(prior, close)


def legacy_hwm_to_account_basis(recorded: Decimal, recorded_on: date, reserve: Decimal) -> Decimal:
    return recorded + reserve if recorded_on < BASIS_AMENDMENT_DATE else recorded


def reserve_cash(
    cash_balance: Decimal, cash_available_for_trading: Decimal, unsettled: Decimal
) -> Decimal:
    return min(cash_balance, cash_available_for_trading + unsettled)


def lifetime_pl(market_value: Decimal, average_price: Decimal, quantity: int) -> Decimal:
    return cents(market_value - average_price * abs(quantity))


def notional(quantity: int, price: Decimal, multiplier: int) -> Decimal:
    return cents(Decimal(quantity) * price * multiplier)


def notional_matches(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("0.01")) -> bool:
    return abs(a - b) <= tolerance


def dte_floor_identity_holds(rules: Rules) -> bool:
    expected = (
        rules.option_close_at_dte
        + int(rules.get("manual", "option_max_blind_days"))
        + int(rules.get("manual", "option_dte_execution_margin_days"))
    )
    return rules.option_min_dte == expected
