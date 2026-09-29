"""What the desk says in Discord, and the posting of paper proposals behind
their veto window (spec §9.4)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal

from tc.broker.models import MarketWindow
from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.calls import Call, effective_invalidation, get_call
from tc.desk.models import utc_iso
from tc.desk.paper import Proposal, mark_posted, record_outcome, unposted_proposals
from tc.desk.sizing import SizingRefused, entry_stop
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store


def render_proposal(p: Proposal, call: Call, deadline_et: str, stop: str) -> str:
    kind = {"shares": "shares", "call": "CALL", "put": "PUT"}[p.instrument]
    return (
        f"🧾 PAPER PROPOSAL #{p.id} — BUY {p.quantity} {p.symbol} ({kind}),"
        f" max {p.max_entry_price}\n"
        f"call #{call.id}: {call.symbol} {call.direction} → target {call.target},"
        f" invalidation {call.invalidation}, {call.horizon_days} sessions,"
        f" conviction {call.conviction}\n"
        f"stop {stop}\n"
        f"{call.thesis}\n"
        f"Executes at {deadline_et} ET unless ❌ (✅ = now). Paper phase: nothing reaches Schwab."
    )


async def _stop_text(store: Store, rules: Rules, p: Proposal, call: Call) -> str:
    """What CLAUDE.md §3.4 would place, estimated off the proposal's own
    `max_entry_price` -- the worst-case fill the limit allows. The actual
    fill (desk_watch, `_fill_entry`) re-runs the same `entry_stop` formula
    against the real ask, so this is a preview, not a commitment; it exists
    so the Discord proposal (spec §9.4: "shows ... limit, stop, target ...")
    never asks for a veto decision on a risk the message does not state."""
    if p.instrument != "shares":
        # CLAUDE.md §3.4: long options get no resting stop.
        return "n/a (options carry no resting stop, CLAUDE.md §3.4)"
    inval = await effective_invalidation(store, call)
    try:
        stop = entry_stop(p.max_entry_price, p.atr_pct or Decimal(0), inval, rules)
    except SizingRefused:
        return "n/a (too tight against the limit right now; recomputed at fill)"
    return f"{stop.trigger}/{stop.limit} (trigger/limit, planned off the {p.max_entry_price} limit)"


def posting_end(
    desk: DeskConfig, veto: timedelta, day: date, window: MarketWindow | None
) -> datetime:
    """The last moment a proposal may be posted: the entry window's end, or
    the session's close less the veto window when that is earlier. On an
    early close (13:00) a 14:30 post would open a veto window that ends after
    the market has shut, and desk_watch would never see it fill in RTH."""
    end = datetime.combine(day, desk.entry_window_end, tzinfo=ET)
    if window is not None and window.date == day and window.rth_end is not None:
        end = min(end, window.rth_end.astimezone(ET) - veto)
    return end


async def post_proposals(
    store: Store, notifier: Notifier, rules: Rules, desk: DeskConfig, now: datetime,
    window: MarketWindow | None,
) -> list[int]:
    et = now.astimezone(ET)
    veto = timedelta(minutes=int(rules.get("strategy", "veto_window_minutes")))
    opens = datetime.combine(et.date(), desk.entry_window_start, tzinfo=ET)
    end = posting_end(desk, veto, et.date(), window)
    posted: list[int] = []
    for p in await unposted_proposals(store):
        if p.created_at.astimezone(ET).date() < et.date():
            # Never posted the day it was made: the runner was down, or a
            # prior post_proposals call crashed before reaching it. Posting
            # it now would open a fresh veto window on a call the desk made
            # a session or more ago -- CLAUDE.md §4.2's day-only entries bind
            # the paper book exactly as they would a real order.
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "stale"})
            continue
        if et >= end:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "proposed after the entry window",
                                         "posting_end": end.strftime("%H:%M")})
            continue
        call = await get_call(store, p.call_id)
        if call is None:
            # A proposal's `call_id` is a foreign key that must resolve; a
            # miss here is a data-integrity bug, not a routine skip, and
            # must surface rather than silently drop the proposal.
            raise RuntimeError(f"proposal {p.id} names call {p.call_id}, which does not exist")
        deadline = max(now, opens) + veto
        stop = await _stop_text(store, rules, p, call)
        mid = await notifier.post_message(
            render_proposal(p, call, deadline.astimezone(ET).strftime("%H:%M"), stop)
        )
        if mid is None:
            # Nobody can react to a message that never went out. Marking it
            # posted anyway would still set a `veto_deadline`, and
            # desk_watch's `_entries` reads a missing message id as "no
            # veto" and fills at the deadline regardless -- an entry
            # executing with no veto window ever having existed. Expire it
            # instead.
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "post failed"})
            continue
        await mark_posted(store, p.id, now, deadline, mid)
        posted.append(p.id)
    return posted


async def pitch_counts_since(store: Store, since: datetime) -> dict[str, int]:
    rows = await store.fetchall(
        "SELECT analyst, COUNT(*) AS n FROM pitches WHERE filed_at >= ? GROUP BY analyst",
        (utc_iso(since),),
    )
    return {r["analyst"]: int(r["n"]) for r in rows}


def desk_summary(
    chain: str, results: Mapping[str, str], counts: Mapping[str, int], day: date
) -> str:
    label = "evening" if chain == "desk_evening" else "pre-open"
    parts = [f"{a} {n}" for a, n in sorted(counts.items())] or ["no pitches"]
    bad = [f"{j} {v}" for j, v in results.items() if v not in ("done", "noop")]
    tail = f" | ⚠️ {', '.join(bad)}" if bad else ""
    return f"🗂️ DESK {label} {day.isoformat()}: " + " · ".join(parts) + tail
