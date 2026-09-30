"""How much, and whether the book can take it (spec §9.2), plus the share
stop (§9.5). Pure arithmetic over a `BookState` snapshot; every cap is read
from `Rules`, never typed here."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from tc.desk.models import DeskRefused
from tc.money import CENT, cents, floor_cents
from tc.rules.arith import StopGeometry, cap_dollars, stop_geometry
from tc.rules.model import Rules

HUNDRED = Decimal(100)
OPTION_MULTIPLIER = 100


class SizingRefused(DeskRefused):
    pass


@dataclass(frozen=True)
class Holding:
    symbol: str             # the UNDERLYING, for options too
    market_value: Decimal
    benchmark: str
    is_option: bool
    premium_paid: Decimal = Decimal(0)
    # A proposal not yet filled or finished, held at its worst case
    # (`max_entry_price` x quantity x multiplier) so that every cap sees money
    # the book has already promised (paper.book_state).
    pending: bool = False


@dataclass(frozen=True)
class BookState:
    """A snapshot of the book for sizing. `holdings` holds the open positions
    AND every pending proposal (`Holding.pending`), and `cash` is already net
    of the pending proposals' worst-case cost: a second call in the same PM run
    must size against the first one's commitment, not against a book that
    has not heard of it."""

    equity: Decimal
    cash: Decimal
    holdings: tuple[Holding, ...]

    @property
    def positions(self) -> int:
        return sum(1 for h in self.holdings if not h.pending)

    @property
    def pending(self) -> int:
        return sum(1 for h in self.holdings if h.pending)

    @property
    def open_premium(self) -> Decimal:
        """§3.2 "open premium" = premium PAID on open positions, plus the
        worst-case premium of pending option proposals; marks irrelevant."""
        return sum((h.premium_paid for h in self.holdings if h.is_option), Decimal(0))


def conviction_pct(
    kind: Literal["shares", "option"], conviction: int, rules: Rules
) -> Decimal:
    if conviction < int(rules.get("strategy", "min_fundable_conviction")):
        raise SizingRefused(
            f'conviction {conviction} calls are scored, never funded; submit with funding "none"'
        )
    if kind == "shares":
        prefix_base = "size_shares_pct_conviction_"
    else:
        prefix_base = "size_option_premium_pct_conviction_"
    return rules.get("strategy", f"{prefix_base}{conviction}")


def share_quantity(conviction: int, equity: Decimal, max_entry: Decimal, rules: Rules) -> int:
    budget = cap_dollars(conviction_pct("shares", conviction, rules), equity)
    qty = int(budget // max_entry)
    if qty < 1:
        raise SizingRefused(
            f"the conviction-{conviction} budget {budget} buys no whole share at {max_entry}"
        )
    return qty


def option_quantity(conviction: int, equity: Decimal, ask: Decimal, rules: Rules) -> int:
    budget = cap_dollars(conviction_pct("option", conviction, rules), equity)
    per = ask * OPTION_MULTIPLIER
    qty = int(budget // per)
    if qty < 1:
        raise SizingRefused(
            f"one contract costs {cents(per)}, over the conviction-{conviction} premium cap"
            f" {budget}"
        )
    return qty


def check_book(
    book: BookState,
    *,
    symbol: str,
    benchmark: str,
    notional: Decimal,
    is_option: bool,
    correlated: frozenset[str],
    rules: Rules,
    reserve: Decimal,
) -> None:
    max_pos = int(rules.get("strategy", "max_funded_positions"))
    if len(book.holdings) >= max_pos:
        raise SizingRefused(
            f"the book carries {book.positions} positions and {book.pending} pending"
            f" proposals; the limit is {max_pos}"
        )
    if book.cash - notional < reserve:
        raise SizingRefused(
            f"cash {cents(book.cash)} (net of pending proposals) less {cents(notional)} would"
            f" breach the {reserve} reserve"
        )
    # CLAUDE.md §3.1 caps the resulting TOTAL, "counting all prior adds": what
    # is already held or pending on this underlying counts with the new order.
    same = sum((h.market_value for h in book.holdings if h.symbol == symbol), Decimal(0))
    if same + notional > cap_dollars(rules.single_position_pct, book.equity):
        raise SizingRefused(
            f"{cents(notional)} on top of {cents(same)} already held or pending in {symbol} is"
            f" over the §3.1 single-position cap ({rules.single_position_pct}% of"
            f" {cents(book.equity)})"
        )
    if is_option:
        if notional > cap_dollars(rules.option_single_position_pct, book.equity):
            raise SizingRefused(
                f"{cents(notional)} premium is over the §3.2 per-position cap"
                f" ({rules.option_single_position_pct}% of {book.equity})"
            )
        if book.open_premium + notional > cap_dollars(rules.option_open_premium_pct, book.equity):
            raise SizingRefused(
                f"open premium {book.open_premium} plus {cents(notional)} is over the §3.2"
                f" {rules.option_open_premium_pct}% open-premium cap"
            )
    cluster = [
        h for h in book.holdings
        if h.symbol == symbol or h.symbol in correlated
        or (benchmark != "SPY" and h.benchmark == benchmark)
    ]
    cap_pct = rules.get("manual", "correlation_cap_pct")
    held = sum((h.market_value for h in cluster), Decimal(0))
    if held + notional > cap_dollars(cap_pct, book.equity):
        names = ", ".join(sorted({h.symbol for h in cluster}))
        raise SizingRefused(
            f"{symbol} clusters with {names} ({held} held); adding {cents(notional)} breaks"
            f" the §3.8 {cap_pct}% correlated-exposure cap"
        )


def entry_stop(
    fill: Decimal, atr_pct: Decimal, invalidation: Decimal, rules: Rules
) -> StopGeometry:
    """Spec §9.5: the trigger is the HIGHER of the §3.4 formula and the call's
    invalidation -- §3.4 lets a trigger be raised, never lowered, so a thesis
    with a tight invalidation gets a tight stop. Trigger rounded up, limit
    rounded down (the trade log's convention since 2026-08-14)."""
    formula = stop_geometry(fill, atr_pct, rules)
    trigger = max(formula.trigger, invalidation.quantize(CENT, rounding=ROUND_CEILING))
    if trigger >= fill:
        raise SizingRefused(
            f"the stop trigger {trigger} is at or above the fill {fill}:"
            " the thesis is already broken"
        )
    limit = floor_cents(trigger * (HUNDRED - rules.stop_limit_pct_below_trigger) / HUNDRED)
    return StopGeometry(
        trigger=trigger, limit=limit, trigger_pct=(fill - trigger) / fill * HUNDRED
    )
