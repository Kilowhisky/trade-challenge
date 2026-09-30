"""Which contract (spec §9.3): only ones that clear every manual §3.2 floor,
ranked toward the strategy's target delta. The engine builds the symbol --
the model picks from this list and nothing else."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker
from tc.broker.models import OptionChainView
from tc.desk.models import Direction
from tc.rules.model import Rules

OSI = re.compile(r"(\d{6})([CP])(\d{8})$")
STRIKE_COUNT = 30
CENT = Decimal("0.01")
EXTRA_WEEK = 7


def _plain(d: Decimal) -> str:
    """50 -> "50", 50.50 -> "50.5": no exponent, no trailing zeros."""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


class OptionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    osi: str
    expiry: str
    strike: str
    kind: str
    bid: str
    ask: str
    delta: str
    open_interest: int
    spread_pct: str
    dte: int
    premium_per_contract: str


def min_dte(horizon_days: int, rules: Rules) -> int:
    """Long enough to outlast the horizon AND the §3.3 5-DTE close, plus a
    week, never under the manual's own floor."""
    calendar = -(-horizon_days * 7 // 5)
    return max(rules.option_min_dte, calendar + rules.option_close_at_dte + EXTRA_WEEK)


def delta_band(rules: Rules) -> tuple[Decimal, Decimal]:
    lo = max(rules.get("manual", "option_min_delta"), rules.get("strategy", "option_min_delta"))
    return lo, rules.get("manual", "option_max_delta")


def select(
    chain: OptionChainView, direction: Direction, horizon_days: int, premium_cap: Decimal,
    rules: Rules,
) -> list[OptionCandidate]:
    kind = "CALL" if direction == "up" else "PUT"
    lo_dte, hi_dte = min_dte(horizon_days, rules), int(rules.get("strategy", "option_max_dte"))
    d_lo, d_hi = delta_band(rules)
    target = rules.get("strategy", "option_target_delta")
    min_oi = int(rules.get("manual", "option_min_open_interest"))
    max_spread = rules.get("manual", "option_max_spread_pct_of_mid")
    ranked: list[tuple[Decimal, Decimal, int, OptionCandidate]] = []
    for c in chain.contracts:
        if c.kind != kind or c.delta is None:
            continue
        if not lo_dte <= c.days_to_expiration <= hi_dte:
            continue
        d = abs(c.delta)
        if not d_lo <= d <= d_hi:
            continue
        if c.open_interest < min_oi or c.bid <= 0 or c.ask <= 0 or c.ask < c.bid:
            continue
        mid = (c.bid + c.ask) / 2
        spread = (c.ask - c.bid) / mid * 100
        premium = c.ask * 100
        if spread > max_spread or premium > premium_cap:
            continue
        ranked.append((abs(d - target), spread, -c.open_interest, OptionCandidate(
            osi=c.osi, expiry=c.expiry.isoformat(), strike=_plain(c.strike),
            kind=c.kind, bid=str(c.bid), ask=str(c.ask), delta=str(d),
            open_interest=c.open_interest, spread_pct=str(spread.quantize(CENT)),
            dte=c.days_to_expiration, premium_per_contract=str(premium.quantize(CENT)),
        )))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in ranked[:3]]


async def fetch_candidates(
    broker: Broker, symbol: str, direction: Direction, horizon_days: int,
    premium_cap: Decimal, rules: Rules, today: date,
) -> list[OptionCandidate]:
    lo = min_dte(horizon_days, rules)
    hi = int(rules.get("strategy", "option_max_dte"))
    chain = await broker.option_chain(
        symbol, today + timedelta(days=lo), today + timedelta(days=hi), STRIKE_COUNT,
        "CALL" if direction == "up" else "PUT",
    )
    return select(chain, direction, horizon_days, premium_cap, rules)


def osi_expiry(osi: str) -> date:
    m = OSI.search(osi.replace(" ", "").upper())
    if m is None:
        raise ValueError(f"not an OSI option symbol: {osi!r}")
    return datetime.strptime(m.group(1), "%y%m%d").date()


def same_osi(a: str, b: str) -> bool:
    """Schwab pads the root to six characters; a model copying the symbol
    usually does not. Compare with the padding removed."""
    return a.replace(" ", "").upper() == b.replace(" ", "").upper()
