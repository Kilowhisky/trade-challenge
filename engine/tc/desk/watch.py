"""desk_watch (spec §9.4, §9.5, §10): every five minutes in the session,
fill paper entries whose veto window has closed, and run the paper book's
exits. Engine code; the model is not involved."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_CEILING, Decimal

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import MarketWindow, Quote
from tc.clock import ET, fallback_window, phase_for
from tc.config import DeskConfig
from tc.desk.approval import Reaction, Reactions
from tc.desk.calls import current_call_id, effective_invalidation, get_call
from tc.desk.options import osi_expiry
from tc.desk.paper import (
    MULT,
    PaperPosition,
    Proposal,
    book_state,
    mark_exit_done,
    open_positions,
    pending_exit_requests,
    pending_proposals,
    record_fill,
    record_outcome,
    upsert_mark,
)
from tc.desk.pm import QUOTE_MAX_AGE_S, correlated_with
from tc.desk.scoring import resolution_for
from tc.desk.sizing import SizingRefused, check_book, entry_stop
from tc.money import CENT, floor_cents
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store

log = logging.getLogger(__name__)

ENTRY_CUTOFF = time(15, 55)
RESOLVED_EXIT_FROM = time(10, 0)
HUNDRED = Decimal(100)


@dataclass
class WatchReport:
    filled: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    exits: list[tuple[int, str]] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    blind: bool = False
    outside_rth: bool = False


async def _quote(broker: Broker, symbols: list[str]) -> dict[str, Quote]:
    return await broker.quotes(sorted(set(symbols)))


def _stale(q: Quote | None, now: datetime) -> bool:
    """CLAUDE.md §4.10: a quote more than a few minutes old is re-fetched or
    the action deferred -- the same QUOTE_MAX_AGE_S the PM's calls use. A
    stale quote is transient, like a BrokerError: the next pass tries again."""
    return q is not None and (now - q.quote_time).total_seconds() > QUOTE_MAX_AGE_S


async def _entries(
    store: Store, broker: Broker, notifier: Notifier, reactions: Reactions, rules: Rules,
    reserve: Decimal, now: datetime, rep: WatchReport,
) -> None:
    et = now.astimezone(ET)
    for p in await pending_proposals(store):
        if p.posted_at is None:
            continue
        # A missed 15:55 fire must not let a proposal from an earlier ET
        # session fill the next morning -- that is exactly the overnight-
        # resting entry CLAUDE.md §4.2 forbids.
        posted_et_date = p.posted_at.astimezone(ET).date()
        if posted_et_date < et.date() or et.time() >= ENTRY_CUTOFF:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "unfilled at 15:55 (CLAUDE.md §4.2)"})
            rep.skipped.append((p.id, "expired"))
            continue
        r = await reactions.read(p.message_id) if p.message_id else Reaction(False, False)
        due = r.approve or (p.veto_deadline is not None and now >= p.veto_deadline)
        if not due:
            continue
        try:
            quotes = await _quote(broker, [p.symbol, p.underlying])
        except BrokerUnauthorized:
            await record_outcome(store, p.id, now, "skipped_blind", vetoed=r.veto,
                                 approved=r.approve, detail={})
            rep.skipped.append((p.id, "skipped_blind"))
            rep.blind = True
            continue
        except BrokerError:
            continue            # transient: the next run tries again
        if _stale(quotes.get(p.symbol), now) or _stale(quotes.get(p.underlying), now):
            log.warning("desk_watch: stale quote for proposal %s (%s); fill deferred",
                        p.id, p.symbol)
            continue
        await _fill_entry(store, notifier, rules, reserve, p, quotes.get(p.symbol),
                          quotes.get(p.underlying), r, now, rep)


async def _fill_entry(
    store: Store, notifier: Notifier, rules: Rules, reserve: Decimal, p: Proposal,
    q: Quote | None, u: Quote | None, r: Reaction, now: datetime, rep: WatchReport,
) -> None:
    detail = {"unreadable": r.unreadable}
    if q is None or q.ask <= 0 or q.ask > p.max_entry_price:
        await record_outcome(store, p.id, now, "skipped_price", vetoed=r.veto, approved=r.approve,
                             detail={**detail, "ask": None if q is None else str(q.ask)})
        rep.skipped.append((p.id, "skipped_price"))
        return
    call = await get_call(store, await current_call_id(store, p.call_id))
    assert call is not None
    inval = await effective_invalidation(store, call)
    trigger = limit = None
    if p.instrument == "shares":
        try:
            if q.ask <= inval:
                raise SizingRefused("the ask is at or below the invalidation")
            stop = entry_stop(q.ask, p.atr_pct or Decimal(0), inval, rules)
        except SizingRefused as e:
            await record_outcome(store, p.id, now, "skipped_invalid", vetoed=r.veto,
                                 approved=r.approve, detail={**detail, "reason": str(e)})
            rep.skipped.append((p.id, "skipped_invalid"))
            return
        trigger, limit = stop.trigger, stop.limit
    elif u is not None:
        # An option's own quote says nothing about the thesis: the
        # underlying can already be through the invalidation while the
        # contract itself still has a fillable ask, and filling here just
        # hands it straight back to `_exits` for an immediate sale.
        through = (u.last <= inval) if call.direction == "up" else (u.last >= inval)
        if through:
            await record_outcome(
                store, p.id, now, "skipped_invalid", vetoed=r.veto, approved=r.approve,
                detail={**detail, "reason": "the underlying is at or through the invalidation"},
            )
            rep.skipped.append((p.id, "skipped_invalid"))
            return
    # The caps were checked when the PM proposed, at the worst-case price, but
    # marks move during the veto window: re-check at the price actually paid,
    # against the book WITHOUT this proposal's own commitment (spec §10: the
    # paper book runs under "the $900 reserve and all caps").
    book = await book_state(store, exclude_proposal=p.id)
    try:
        check_book(
            book, symbol=p.underlying, benchmark=call.benchmark,
            notional=q.ask * p.quantity * MULT[p.instrument], is_option=p.instrument != "shares",
            correlated=await correlated_with(store, rules, p.underlying, book), rules=rules,
            reserve=reserve,
        )
    except SizingRefused as e:
        await record_outcome(store, p.id, now, "skipped_invalid", vetoed=r.veto,
                             approved=r.approve,
                             detail={**detail, "reason": f"at the fill price: {e}"})
        rep.skipped.append((p.id, "skipped_invalid"))
        return
    await record_fill(store, p.id, now, "buy", p.quantity, q.ask, "entry", trigger, limit)
    await record_outcome(store, p.id, now, "filled", vetoed=r.veto, approved=r.approve,
                         detail=detail)
    rep.filled.append(p.id)
    note = " — ❌ vetoed: kept in the paper book as Claude's decision" if r.veto else ""
    stop_txt = "" if trigger is None else f", stop {trigger}/{limit}"
    await notifier.post(f"📄 PAPER BUY {p.quantity} {p.symbol} @ {q.ask}{stop_txt}{note}")


def _exit_reason(
    pos: PaperPosition, up: bool, target: Decimal, inval: Decimal, u: Quote, t: Quote,
    rules: Rules, today_et: datetime,
) -> tuple[str, Decimal] | None:
    if pos.instrument == "shares":
        thesis = inval.quantize(CENT, rounding=ROUND_CEILING)
        raw_trigger = pos.stop_trigger or Decimal(0)
        trigger = max(raw_trigger, thesis)
        # §3.4/§9.5: the limit sits 5% below the trigger. A trigger raised by
        # a tightened invalidation carries a limit that was never priced for
        # it -- the stored `stop_limit` is still 5% below the OLD trigger --
        # so a raised trigger recomputes its own limit rather than reusing it.
        limit = pos.stop_limit
        if trigger > raw_trigger:
            limit = floor_cents(trigger * (HUNDRED - rules.stop_limit_pct_below_trigger) / HUNDRED)
        if u.last <= trigger:
            price = trigger if limit is None or u.last >= limit else t.bid
            return "stop", price
        if u.last >= target:
            return "target", t.bid
        return None
    if (u.last <= inval) if up else (u.last >= inval):
        return "invalidation", t.bid
    if (u.last >= target) if up else (u.last <= target):
        return "target", t.bid
    if (osi_expiry(pos.symbol) - today_et.date()).days <= rules.option_close_at_dte:
        return "dte_close", t.bid
    return None


async def _exits(
    store: Store, broker: Broker, notifier: Notifier, rules: Rules, now: datetime,
    rep: WatchReport,
) -> None:
    positions = await open_positions(store)
    requests = await pending_exit_requests(store)
    if positions:
        try:
            symbols = [p.underlying for p in positions] + [p.symbol for p in positions]
            quotes = await _quote(broker, symbols)
        except BrokerUnauthorized:
            rep.blind = True
            return
        except BrokerError:
            return
        et = now.astimezone(ET)
        wanted = {r.proposal_id: r for r in requests if r.proposal_id is not None}
        for pos in positions:
            u, t = quotes.get(pos.underlying), quotes.get(pos.symbol)
            if u is None or t is None or _stale(u, now) or _stale(t, now):
                log.warning(
                    "desk_watch: no fresh quote for %s (underlying %s) -- exit check skipped"
                    " this pass", pos.symbol, pos.underlying,
                )
                continue
            mark_price = t.bid if pos.instrument != "shares" else u.last
            await upsert_mark(store, pos.symbol, mark_price, now)
            cid = await current_call_id(store, pos.call_id)
            call = await get_call(store, cid)
            assert call is not None
            inval = await effective_invalidation(store, call)
            hit = _exit_reason(pos, call.direction == "up", call.target, inval, u, t, rules, et)
            if hit is None and et.time() >= RESOLVED_EXIT_FROM:
                if await resolution_for(store, "call", cid) is not None:
                    hit = ("call_resolved", t.bid)
            if hit is None and pos.proposal_id in wanted:
                hit = ("pm_exit", t.bid)
            if hit is None:
                continue
            reason, price = hit
            await record_fill(store, pos.proposal_id, now, "sell", pos.quantity, price, reason)
            rep.exits.append((pos.proposal_id, reason))
            await notifier.post(f"📄 PAPER SELL {pos.quantity} {pos.symbol} @ {price} — {reason}")
    still_open = {p.proposal_id for p in await open_positions(store)}
    for r in requests:
        if r.proposal_id is None:
            await notifier.post(
                f"📌 The PM recommends exiting {r.symbol} (real money, a legacy position):"
                f" {r.reason}. Act at Schwab if you agree; the desk places nothing."
            )
            rep.recommended.append(r.symbol)
            await mark_exit_done(store, r.id, now)
        elif r.proposal_id not in still_open:
            await mark_exit_done(store, r.id, now)


async def run_desk_watch(
    *, store: Store, broker: Broker, notifier: Notifier, reactions: Reactions, rules: Rules,
    desk: DeskConfig, reserve: Decimal, window: MarketWindow | None, now: datetime,
) -> WatchReport:
    """Acts only while the regular session is open by the engine's own
    market window. The schedule runs 09:55-15:55 on every trading day, and on
    an early close (2026-11-27, 2026-12-24) that is three hours of post-close
    asks and extended-hours prints no real order could have met (spec §10:
    the paper book does "exactly as the real one would"). Without the
    broker's calendar the weekday guess stands in, as it does for the tick."""
    rep = WatchReport()
    et_date = now.astimezone(ET).date()
    w = window if window is not None and window.date == et_date else fallback_window(et_date)
    if phase_for(now, w) != "RTH":
        rep.outside_rth = True
        return rep
    await _entries(store, broker, notifier, reactions, rules, reserve, now, rep)
    await _exits(store, broker, notifier, rules, now, rep)
    return rep
