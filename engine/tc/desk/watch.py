"""desk_watch (spec §9.4, §9.5, §10): every five minutes in the session,
fill paper entries whose veto window has closed, and run the paper book's
exits. Engine code; the model is not involved."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import ROUND_CEILING, Decimal

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import Quote
from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.approval import Reaction, Reactions
from tc.desk.calls import current_call_id, effective_invalidation, get_call
from tc.desk.options import osi_expiry
from tc.desk.paper import (
    PaperPosition,
    Proposal,
    mark_exit_done,
    open_positions,
    pending_exit_requests,
    pending_proposals,
    record_fill,
    record_outcome,
    upsert_mark,
)
from tc.desk.scoring import resolution_for
from tc.desk.sizing import SizingRefused, entry_stop
from tc.money import CENT
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store

ENTRY_CUTOFF = time(15, 55)
RESOLVED_EXIT_FROM = time(10, 0)


@dataclass
class WatchReport:
    filled: list[int] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)
    exits: list[tuple[int, str]] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    blind: bool = False


async def _quote(broker: Broker, symbols: list[str]) -> dict[str, Quote]:
    return await broker.quotes(sorted(set(symbols)))


async def _entries(
    store: Store, broker: Broker, notifier: Notifier, reactions: Reactions, rules: Rules,
    now: datetime, rep: WatchReport,
) -> None:
    et = now.astimezone(ET)
    for p in await pending_proposals(store):
        if p.posted_at is None:
            continue
        if et.time() >= ENTRY_CUTOFF:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "unfilled at 15:55 (CLAUDE.md §4.2)"})
            rep.skipped.append((p.id, "expired"))
            continue
        r = await reactions.read(p.message_id) if p.message_id else Reaction(False, False)
        due = r.approve or (p.veto_deadline is not None and now >= p.veto_deadline)
        if not due:
            continue
        try:
            q = (await _quote(broker, [p.symbol])).get(p.symbol)
        except BrokerUnauthorized:
            await record_outcome(store, p.id, now, "skipped_blind", vetoed=r.veto,
                                 approved=r.approve, detail={})
            rep.skipped.append((p.id, "skipped_blind"))
            rep.blind = True
            continue
        except BrokerError:
            continue            # transient: the next run tries again
        await _fill_entry(store, notifier, rules, p, q, r, now, rep)


async def _fill_entry(
    store: Store, notifier: Notifier, rules: Rules, p: Proposal, q: Quote | None, r: Reaction,
    now: datetime, rep: WatchReport,
) -> None:
    detail = {"unreadable": r.unreadable}
    if q is None or q.ask <= 0 or q.ask > p.max_entry_price:
        await record_outcome(store, p.id, now, "skipped_price", vetoed=r.veto, approved=r.approve,
                             detail={**detail, "ask": None if q is None else str(q.ask)})
        rep.skipped.append((p.id, "skipped_price"))
        return
    trigger = limit = None
    if p.instrument == "shares":
        call = await get_call(store, await current_call_id(store, p.call_id))
        assert call is not None
        inval = await effective_invalidation(store, call)
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
        trigger = max(pos.stop_trigger or Decimal(0), thesis)
        if u.last <= trigger:
            price = trigger if pos.stop_limit is None or u.last >= pos.stop_limit else t.bid
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
            if u is None or t is None:
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
    desk: DeskConfig, now: datetime,
) -> WatchReport:
    rep = WatchReport()
    await _entries(store, broker, notifier, reactions, rules, now, rep)
    await _exits(store, broker, notifier, rules, now, rep)
    return rep
