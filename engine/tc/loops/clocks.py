"""CLAUDE.md §3.3 (option expiry / OCC auto-exercise) and §3.5 (leveraged-ETF
hold limit): the two clocks are arithmetic on the ledger — a DTE subtraction
and a session count — not a model reading a calendar.

``OPTION_WARN_DAYS_BEFORE_CLOSE`` and ``LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE``
are operational constants from tick.md §C watch 7 ("option ≤ 7 DTE warn,
close at 5 → warn = close + 2"; "leveraged ETF ≥ day 3 of 5 warn → warn =
max_hold - 2"). They are watch-table tuning, not §9-gated rule numbers, so
they live here as named constants rather than in rules.yml.

Leveraged-ETF detection: rules.yml carries no leveraged-symbol list (§3.5's
"leveraged/inverse ETFs" is not a rule parameter, it's a universe fact), so
``run_clocks`` takes a ``leveraged`` set from its caller instead of reading
one out of ``Rules``. Until Phase 0c's universe table exists, the caller is
expected to source that set from ``config.yml``'s ``engine.leveraged_symbols``
(Task 6/10 wires that up; this module only consumes the set it is given).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.broker.models import Position
from tc.clock import ET
from tc.rules.model import Rules
from tc.store.db import Store

# tick.md §C watch 7 — operational tuning, not a §9 rule number.
OPTION_WARN_DAYS_BEFORE_CLOSE = 2
LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE = 2

# Schwab OSI: a 6-char space-padded root, YYMMDD, C/P, strike*1000 (8 digits).
# e.g. "AMH   261016P00030000" -> root="AMH", 2026-10-16, Put, strike 30.000.
_OSI_RE = re.compile(
    r"^(?P<root>[A-Z.]{1,6})\s*(?P<yy>\d\d)(?P<mm>\d\d)(?P<dd>\d\d)"
    r"(?P<kind>[CP])(?P<strike>\d{8})$"
)
_STRIKE_SCALE = Decimal(1000)
_STRIKE_QUANT = Decimal("0.001")


class OptionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    underlying: str
    expiry: date
    kind: Literal["C", "P"]
    strike: Decimal


# Most urgent first. A `*_close` is a forced action today; a `*_warn` is
# notice. Within a tier §3.3 outranks §3.5: missing the DTE close means an OCC
# auto-exercise and a cash call, missing the hold limit means one more day of
# decay. One position reports one alert, so this is the tie-break.
_ALERT_PRECEDENCE = {
    "option_close": 0,
    "leveraged_close": 1,
    "option_warn": 2,
    "leveraged_warn": 3,
}


class ClockAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    kind: Literal["option_close", "option_warn", "leveraged_close", "leveraged_warn"]
    detail: str


def parse_osi(symbol: str) -> OptionRef | None:
    """Parse a Schwab OSI option symbol. Returns None for anything that
    doesn't match — including a plain equity symbol, which is the common
    case callers must distinguish before treating a position as an option."""
    m = _OSI_RE.match(symbol.strip())
    if m is None:
        return None
    yy, mm, dd = int(m["yy"]), int(m["mm"]), int(m["dd"])
    try:
        expiry = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    kind = m["kind"]
    assert kind in ("C", "P")  # guaranteed by the regex's [CP] class
    strike = (Decimal(m["strike"]) / _STRIKE_SCALE).quantize(_STRIKE_QUANT)
    return OptionRef(underlying=m["root"], expiry=expiry, kind=kind, strike=strike)


def dte(expiry: date, today: date) -> int:
    """Calendar days to expiration — not trading days. §3.3's 5-DTE close
    and its whole derivation in rules.yml (close_at_dte + max_blind_days +
    execution_margin) are stated in calendar days."""
    return (expiry - today).days


async def run_clocks(
    store: Store,
    rules: Rules,
    positions: list[Position],
    today: date,
    trading_days_between: Callable[[date, date], int],
    leveraged: frozenset[str] | set[str],
) -> list[ClockAlert]:
    """One alert per position at most.

    A position can be on both clocks at once: §3.5 says its limits "also apply
    to options on leveraged ETFs", so an option whose symbol the caller has
    declared leveraged is checked against §3.3's DTE clock *and* the §3.5 hold
    clock. Only the most urgent result is reported, ranked by
    ``_ALERT_PRECEDENCE`` — every `*_close` outranks every `*_warn`, and within
    a tier the option clock outranks the hold clock, because §3.3's deadline is
    an OCC auto-exercise (a cash call) while §3.5's is a decay limit.

    `leveraged` is matched against the position symbol as given, never against
    a parsed OSI underlying: mapping an option to its underlying is the
    caller's job (it owns the per-underlying accounting), and this module only
    consumes the set it is handed.
    """
    close_dte = rules.option_close_at_dte
    warn_dte = close_dte + OPTION_WARN_DAYS_BEFORE_CLOSE
    max_hold = rules.leveraged_max_hold_sessions
    warn_hold = max_hold - LEVERAGED_WARN_SESSIONS_BEFORE_CLOSE

    alerts: list[ClockAlert] = []
    for p in positions:
        firing: list[ClockAlert] = []

        ref = parse_osi(p.symbol)
        if ref is not None:
            d = dte(ref.expiry, today)
            if d <= close_dte:
                firing.append(
                    ClockAlert(
                        symbol=p.symbol, kind="option_close",
                        detail=f"{d} DTE (close at {close_dte})",
                    )
                )
            elif d <= warn_dte:
                firing.append(
                    ClockAlert(
                        symbol=p.symbol, kind="option_warn",
                        detail=f"{d} DTE (warn at {warn_dte}, close at {close_dte})",
                    )
                )

        if p.symbol in leveraged:
            first_seen = await store.first_seen(p.symbol)
            first_date = first_seen.astimezone(ET).date() if first_seen is not None else today
            sessions_held = trading_days_between(first_date, today) + 1
            if sessions_held >= max_hold:
                firing.append(
                    ClockAlert(
                        symbol=p.symbol, kind="leveraged_close",
                        detail=f"held {sessions_held} sessions (max {max_hold})",
                    )
                )
            elif sessions_held >= warn_hold:
                firing.append(
                    ClockAlert(
                        symbol=p.symbol, kind="leveraged_warn",
                        detail=(
                            f"held {sessions_held} sessions "
                            f"(warn at {warn_hold}, max {max_hold})"
                        ),
                    )
                )

        if firing:
            alerts.append(min(firing, key=lambda a: _ALERT_PRECEDENCE[a.kind]))
    return alerts
