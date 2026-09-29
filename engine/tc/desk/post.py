"""What the desk says in Discord, and the posting of paper proposals behind
their veto window (spec §9.4)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta

from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.calls import Call, get_call
from tc.desk.models import utc_iso
from tc.desk.paper import Proposal, mark_posted, record_outcome, unposted_proposals
from tc.notify import Notifier
from tc.rules.model import Rules
from tc.store.db import Store


def render_proposal(p: Proposal, call: Call, deadline_et: str) -> str:
    kind = {"shares": "shares", "call": "CALL", "put": "PUT"}[p.instrument]
    return (
        f"🧾 PAPER PROPOSAL #{p.id} — BUY {p.quantity} {p.symbol} ({kind}),"
        f" max {p.max_entry_price}\n"
        f"call #{call.id}: {call.symbol} {call.direction} → target {call.target},"
        f" invalidation {call.invalidation}, {call.horizon_days} sessions,"
        f" conviction {call.conviction}\n"
        f"{call.thesis}\n"
        f"Executes at {deadline_et} ET unless ❌ (✅ = now). Paper phase: nothing reaches Schwab."
    )


async def post_proposals(
    store: Store, notifier: Notifier, rules: Rules, desk: DeskConfig, now: datetime
) -> list[int]:
    et = now.astimezone(ET)
    window = timedelta(minutes=int(rules.get("strategy", "veto_window_minutes")))
    opens = datetime.combine(et.date(), desk.entry_window_start, tzinfo=ET)
    posted: list[int] = []
    for p in await unposted_proposals(store):
        if et.time() >= desk.entry_window_end:
            await record_outcome(store, p.id, now, "expired", vetoed=False, approved=False,
                                 detail={"reason": "proposed after the entry window"})
            continue
        call = await get_call(store, p.call_id)
        assert call is not None
        deadline = max(now, opens) + window
        mid = await notifier.post_message(
            render_proposal(p, call, deadline.astimezone(ET).strftime("%H:%M"))
        )
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
