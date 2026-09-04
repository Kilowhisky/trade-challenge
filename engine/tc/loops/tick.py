"""The tick: `.claude/commands/tick.md` §B and §C as code.

One sweep answers one question — has the book drifted out of the box since the
last tick? — and it answers it in a single appended ledger row plus a list of
trips for the caller to escalate. Three properties this module exists to hold:

* **BLIND is a state, not a crash.** A dead token means the row says `BLIND`
  and the trips say so; it never propagates an exception into the scheduler,
  because a watchdog that dies on the one failure it is watching for is worse
  than no watchdog (§0 operating limitation #1: absence of action is never
  evidence that nothing needed doing).
* **The row is written exactly once per call**, on every path, before
  returning — including the tripped and BLIND paths. The ledger is the record
  of what the engine saw; a sweep that trips and forgets to write is a sweep
  that never happened.
* **Watch evaluation is pure.** `evaluate_watches` does no I/O, so the
  priority order (§C: restriction before naked, drawdown on *account value*)
  is testable without a broker or a store.

Escalation and notification are deliberately NOT here (tick.md §E, §H): this
module returns trips, and the job that called it decides what to say and to
whom. The tick path places nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import MarketWindow
from tc.clock import ET, fallback_window, phase_for
from tc.loops.clocks import ClockAlert, parse_osi, run_clocks
from tc.loops.reconcile import BookView, reconcile
from tc.rules import arith
from tc.rules.model import Rules
from tc.store.db import Store, TickRow

# tick.md §B4: "A quote more than a few minutes stale in RTH is re-fetched
# once; if still stale, the tick is STALE." An operational constant of the
# monitoring loop, not a §9-gated rule number — it lives here rather than in
# rules.yml for the same reason clocks.py's warn offsets do.
STALE_QUOTE_AGE = timedelta(minutes=3)
_STALE_MINUTES = int(STALE_QUOTE_AGE.total_seconds() // 60)

TickState = Literal["RTH", "PRE", "POST", "STALE", "BLIND"]

# tick.md §D FLAGS. Watch letters are emitted in watch order, so the string
# itself reads as the §C priority order; S/F/C/B are conditions of the sweep
# rather than watches and follow.
WATCH_FLAG: dict[int, str] = {1: "R", 2: "V", 3: "H", 4: "N", 5: "P", 6: "X", 7: "K"}
NO_FLAGS = "-"


class Trip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    watch: int  # 1-7 per tick.md §C; 0 is a sweep-level failure (BLIND, unseeded)
    name: str
    detail: str


class TickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    row: TickRow
    trips: list[Trip]
    view: BookView | None


def _blind_row(at_et: str, flags: str) -> TickRow:
    """Nothing was read, so nothing is claimed. Every figure is zero rather
    than carried forward from a prior tick — §4.5: never begin from an assumed
    state, and never publish one either."""
    z = Decimal("0")
    return TickRow(
        at_et=at_et, state="BLIND", account_value=z, comp_capital=z, hwm=z,
        drawdown_pct=z, level="OK", positions=0, stops=0, orders=0,
        settled=z, unsettled=z, reserve=z, flags=flags,
        note="BLIND: token dead or absent",
    )


async def _quotes_are_stale(broker: Broker, symbols: Sequence[str], now: datetime) -> bool:
    """tick.md §B4: one `get_quotes` for every held symbol, timestamp checked
    against the clock, re-fetched **once** if old. Returns True when the tick
    must be marked STALE.

    A broker failure here is also "no fresh price": the account read already
    succeeded, so this is not a BLIND tick, but nothing price-dependent may
    proceed (§4.10 stale-quote gate). Never raises.
    """
    for attempt in (1, 2):
        try:
            quotes = await broker.quotes(symbols)
        except (BrokerUnauthorized, BrokerError):
            return True
        if not quotes:
            # Every held symbol came back without a quote block — halted,
            # unknown, or a partial outage. That is "no fresh price" too.
            return True
        oldest = min(q.quote_time for q in quotes.values())
        if now - oldest <= STALE_QUOTE_AGE:
            return False
        if attempt == 2:
            return True
    return True  # pragma: no cover -- the loop above always returns


def evaluate_watches(
    view: BookView,
    prior_positions: dict[str, int],
    hwm: Decimal,
    rules: Rules,
    reserve: Decimal,
    clock_alerts: list[ClockAlert],
) -> list[Trip]:
    """tick.md §C, in priority order — which is not the §8 listing order. A
    restricted account changes which responses are even legal, so it is tested
    first; a naked position is the loop's top *actionable* alert.

    Pure by construction: every input is already-read state. Watch 8
    (correlation) is a weekly job, not an in-tick computation, and is deferred
    to Plan 0c — see `run_tick`'s `C` flag.
    """
    account = view.account
    trips: list[Trip] = []

    # 1 — restriction (§5): no further orders at all, account read-only.
    if view.restricted:
        trips.append(
            Trip(watch=1, name="restriction",
                 detail=f"closing_only={account.is_closing_only_restricted} "
                        f"cash_call={account.cash_call}")
        )

    # 2 — reserve invariant: the settlement float is not a source of funds.
    if account.reserve_cash < reserve:
        trips.append(
            Trip(watch=2, name="reserve",
                 detail=f"total cash {account.reserve_cash} below reserve {reserve}")
        )

    # 3 — drawdown (§3.6). ACCOUNT VALUE, never comp_capital: the two differ by
    # exactly the reserve, and crossing them reports roughly -24% on a flat
    # book, which is through the halt, permanently, with nothing having lost
    # anything. hwm <= 0 means the mark is unseeded and the test is skipped
    # rather than guessed (run_tick has already tripped watch 0 for that).
    value = account.liquidation_value
    if hwm > 0 and arith.is_halted(value, hwm, rules):
        trips.append(
            Trip(watch=3, name="drawdown",
                 detail=f"account value {value} <= halt {arith.halt_threshold(hwm, rules)} "
                        f"(hwm {hwm})")
        )

    # 4 — naked position (§3.4/§4.3): the loop's reason for existing.
    if view.naked:
        trips.append(
            Trip(watch=4, name="naked", detail="no resting stop: " + ", ".join(view.naked))
        )

    # 5 — partial fill (§4.4): stop quantity must match the FILLED quantity.
    if view.partial:
        trips.append(
            Trip(watch=5, name="partial",
                 detail="; ".join(f"{s}: {held} held vs {stop} stopped"
                                  for s, held, stop in view.partial))
        )

    # 6 — stop fill (§4.7): a position present last tick is gone now. The
    # orphaned-stop half of the watch is already in view.orphaned_stops.
    held = {p.symbol for p in account.positions if p.quantity != 0}
    gone = sorted(s for s, q in prior_positions.items() if q != 0 and s not in held)
    if gone:
        trips.append(
            Trip(watch=6, name="stop_fill",
                 detail="position gone since last snapshot: " + ", ".join(gone))
        )

    # 7 — clocks (§3.3 / §3.5). One trip per alert: each names a different
    # position and a different forced action.
    trips.extend(
        Trip(watch=7, name=a.kind, detail=f"{a.symbol}: {a.detail}") for a in clock_alerts
    )
    return trips


def _flag_string(trips: list[Trip], extra: Sequence[str]) -> str:
    letters: list[str] = []
    for t in trips:
        f = WATCH_FLAG.get(t.watch)
        if f is not None and f not in letters:
            letters.append(f)
    letters.extend(extra)
    return "".join(letters) or NO_FLAGS


def _leveraged_effective(symbols: Sequence[str], leveraged: set[str]) -> set[str]:
    """§3.5's carve-in: the leveraged limits "also apply to options on
    leveraged ETFs". An option whose OSI underlying is a declared leveraged
    symbol is therefore passed to the clocks as leveraged itself.

    NOTE: `run_clocks` currently `continue`s after handling any position whose
    symbol parses as OSI, so this set membership has no effect on an option
    today — the §3.5 *hold* clock for options on leveraged ETFs is not yet
    implemented (Task 5 left it to the caller that owns per-underlying
    accounting). Computing it here keeps the caller's half of the contract and
    makes the gap a one-line change in clocks.py rather than a missing input.
    """
    out = set(leveraged)
    for s in symbols:
        ref = parse_osi(s)
        if ref is not None and ref.underlying in leveraged:
            out.add(s)
    return out


async def run_tick(
    *,
    broker: Broker,
    store: Store,
    rules: Rules,
    reserve: Decimal,
    account_hash: str | None,
    now: datetime,
    window: MarketWindow | None,
    orders_from: date,
    leveraged: set[str],
    trading_days_between: Callable[[date, date], int],
) -> TickResult:
    now_et = now.astimezone(ET)
    at_et = now_et.strftime("%Y-%m-%d %H:%M")  # §D: Eastern, never the machine clock
    fallback = window is None
    win = fallback_window(now_et.date()) if window is None else window

    try:
        h = account_hash or (await broker.account_hashes())[0]
        # §C watch 6 compares the PRIOR snapshot against this read, so it must
        # be taken before reconcile writes a new one — otherwise the
        # comparison is the new snapshot against itself and can never trip.
        prior = await store.latest_positions()
        view = await reconcile(broker, store, h, now, orders_from)
    except BrokerUnauthorized:
        row = _blind_row(at_et, ("F" if fallback else "") + "B")
        await store.append_tick(row)
        return TickResult(
            row=row,
            trips=[Trip(watch=0, name="blind", detail="token dead or absent")],
            view=None,
        )

    phase = phase_for(now, win)
    positions = [p for p in view.account.positions if p.quantity != 0]
    notes: list[str] = []

    # §B4: quotes are an RTH-only read, and skipped entirely on a flat book.
    stale = False
    if phase == "RTH" and positions:
        stale = await _quotes_are_stale(broker, [p.symbol for p in positions], now)
        if stale:
            notes.append(f"STALE: quotes older than {_STALE_MINUTES}m after one re-fetch")

    # CLOSED is not a ledger state (§D lists RTH/PRE/POST/STALE/BLIND); a
    # holiday or weekend sweep records POST and says so in the note.
    state: TickState
    if stale:
        state = "STALE"
    elif phase == "CLOSED":
        state = "POST"
        notes.append("not a trading day")
    else:
        state = phase

    # §B5 — compute before evaluating any watch.
    account = view.account
    account_value = account.liquidation_value  # THE §3.6 DENOMINATOR
    comp_capital = account_value - reserve  # ledger/display column only

    trips: list[Trip] = []
    status = await store.latest_session_status()
    if status is None:
        # The mark is the §3.6 denominator; without it there is no drawdown to
        # compute. Refusing to write a row would lose the sweep entirely, so
        # the row is written with the mark zeroed and the gap is a trip.
        hwm = Decimal("0")
        drawdown = Decimal("0")
        level: Literal["OK", "HALT"] = "OK"
        notes.append("no high-water mark on record: run tc seed-hwm")
        trips.append(
            Trip(watch=0, name="unseeded", detail="no session_status row; run `tc seed-hwm`")
        )
    else:
        hwm = status.hwm
        drawdown = arith.drawdown_pct(account_value, hwm)
        level = "HALT" if arith.is_halted(account_value, hwm, rules) else "OK"

    alerts = await run_clocks(
        store, rules, positions, now_et.date(), trading_days_between,
        _leveraged_effective([p.symbol for p in positions], leveraged),
    )
    trips.extend(evaluate_watches(view, prior, hwm, rules, reserve, alerts))

    extra: list[str] = []
    if stale:
        extra.append("S")
    if fallback:
        extra.append("F")
    # Watch 8 (correlation) is a weekly job deferred to Plan 0c. Until it
    # exists, the first trading day of the week carries a reminder letter so
    # the omission is visible in the ledger instead of silent.
    if now_et.weekday() == 0:
        extra.append("C")

    row = TickRow(
        at_et=at_et,
        state=state,
        account_value=account_value,
        comp_capital=comp_capital,
        hwm=hwm,
        drawdown_pct=drawdown,
        level=level,
        positions=len(positions),
        stops=len(view.resting_stops),
        orders=len(view.open_entries),
        settled=account.cash_available_for_trading,
        unsettled=account.unsettled_cash,
        reserve=account.reserve_cash,  # §D: the watch-2 total-cash figure
        flags=_flag_string(trips, extra),
        note=" ".join(notes),
    )
    await store.append_tick(row)
    return TickResult(row=row, trips=trips, view=view)
