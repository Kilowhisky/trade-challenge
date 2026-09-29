"""Task 20: one desk day and its aftermath, end to end, on the real desk
modules and the fixture broker. An analyst pitches, the PM adopts and funds
it, the proposal posts and fills, bars arrive, both predictions resolve, the
paper position exits at its target, and the scorecard counts it all."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from desk_fixtures import RULES, bar, desk_deps, desk_settings, sessions
from mcp.server.fastmcp import FastMCP

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, DailyBar
from tc.desk.approval import NoReactions
from tc.desk.paper import ensure_book
from tc.desk.post import post_proposals
from tc.desk.scorecard import build_scorecard
from tc.desk.scoring import score
from tc.desk.watch import run_desk_watch
from tc.mcp import tools_desk
from tc.mcp.registry import Role
from tc.mcp.server import McpDeps
from tc.notify import Notifier
from tc.store.db import Store

EVENING = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)   # Mon 16:45 ET
PM_AT = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)     # Tue 09:50 ET
FILL_AT = datetime(2026, 9, 29, 14, 10, tzinfo=UTC)   # Tue 10:10 ET
EXIT_AT = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)    # Thu 11:00 ET
D29, D30, D01 = date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)


class Notes(Notifier):
    def __init__(self) -> None:
        super().__init__(None, httpx.AsyncClient())
        self.posts: list[str] = []

    async def post(self, text: str) -> bool:
        self.posts.append(text)
        return True

    async def post_message(self, text: str) -> str | None:
        # post_proposals (Task 17 fix) expires any proposal whose Discord
        # post fails -- the base Notifier.post_message returns None with no
        # target configured (target=None above), which would expire every
        # proposal in this test before it ever reached desk_watch. The stub
        # fakes a successful post, same pattern as test_desk_engine.py's Notes.
        self.posts.append(text)
        return f"msg-{len(self.posts)}"


def _history(price: str) -> list[DailyBar]:
    p = Decimal(price)
    days = [d for d in sessions(date(2026, 5, 1), 150) if d <= date(2026, 9, 28)][-80:]
    return [DailyBar(date=d, open=p, high=p + Decimal("0.5"), low=p - Decimal("0.5"), close=p,
                     volume=1_000_000) for d in days]


def _quotes(d: Path, at: datetime, **px: tuple[float, float, float]) -> None:
    ms = int(at.timestamp() * 1000)
    (d / "quotes.json").write_text(json.dumps({
        s: {"quote": {"lastPrice": last, "bidPrice": bid, "askPrice": ask, "quoteTime": ms}}
        for s, (last, bid, ask) in px.items()
    }))


def _server(role: Role, deps: McpDeps) -> FastMCP:
    s = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(s, deps, role)
    return s


async def test_a_desk_day_end_to_end(desk_store: Store, tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    fx.mkdir()
    for sym, px in (("AAA", "50"), ("SPY", "500"), ("XLK", "200")):
        await desk_store.upsert_bars(sym, _history(px))
    await desk_store.replace_universe(date(2026, 9, 26), [{
        "symbol": "AAA", "price": Decimal(50), "adv10": Decimal(1_000_000),
        "dollar_vol": Decimal(50_000_000), "pct_from_52wk_high": Decimal(1), "optionable": True,
        "leverage": Decimal(0), "last_earnings": "", "is_etf": False,
        "session_range_pct": Decimal("1.5"), "description": "AAA", "qualified": True,
    }])
    await desk_store.record_account(AccountSnapshot(
        account_hash="H", read_at=EVENING, liquidation_value=Decimal("3700.00"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[]))
    desk = desk_settings(tmp_path).desk

    # Monday evening: the technical analyst pitches AAA up.
    research = _server("research", desk_deps(desk_store, tmp_path, FakeBroker(fx, EVENING),
                                             EVENING, "analyst_technical"))
    pitch = await research._tool_manager.call_tool("pitch_submit", {
        "symbol": "AAA", "direction": "up", "thesis": "a tight base under 51 breaking on volume",
        "evidence": [{"url": "https://example.com/aaa", "claim": "base since July",
                      "date": "2026-09-28"}],
        "target": "55", "invalidation": "48", "horizon_days": 5, "conviction": 4,
        "benchmark": "XLK",
    })

    # Tuesday 09:50: the PM adopts it and funds it with shares.
    await ensure_book(desk_store, D29, Decimal("3700.00"), PM_AT)
    _quotes(fx, PM_AT, AAA=(50.0, 49.98, 50.02), SPY=(500.0, 499.9, 500.1), XLK=(200.0, 199.9, 200.1))
    decide = _server("decide", desk_deps(desk_store, tmp_path, FakeBroker(fx, PM_AT), PM_AT, "pm"))
    call = await decide._tool_manager.call_tool("call_submit", {
        "pitch_id": pitch.id, "symbol": "AAA", "direction": "up",
        "thesis": "adopting the technical pitch: base breakout, volume confirms",
        "target": "55", "invalidation": "48", "horizon_days": 5, "conviction": 4,
        "benchmark": "XLK", "funding": "shares", "max_entry_price": "50.50",
    })
    assert call.origin == "pitch" and call.proposal.quantity == 10   # 555.00 // 50.50
    notes = Notes()
    posted = await post_proposals(desk_store, notes, RULES, desk, PM_AT)

    # 10:10: the veto window closes and desk_watch fills at the ask.
    _quotes(fx, FILL_AT, AAA=(50.1, 50.08, 50.12))
    rep = await run_desk_watch(store=desk_store, broker=FakeBroker(fx, FILL_AT), notifier=notes,
                               reactions=NoReactions(), rules=RULES, desk=desk,
                               reserve=Decimal("900.00"), now=FILL_AT)
    assert rep.filled == posted

    # Bars arrive; AAA reaches 55.5 on Thursday. Both predictions hit.
    await desk_store.upsert_bars("AAA", [bar(D29, 50, 51, 49.5, 50.8), bar(D30, 50.8, 53, 50.5, 52.9),
                                         bar(D01, 53, 55.5, 52.8, 55.2)])
    for sym, price in (("SPY", 500), ("XLK", 200)):
        await desk_store.upsert_bars(sym, [bar(d, price, price, price, price) for d in (D29, D30, D01)])
    resolved = (await score(desk_store)).resolved
    assert {(r.kind, r.how, r.ret_pct) for r in resolved} == {
        ("pitch", "target", Decimal(10)), ("call", "target", Decimal(10)),
    }

    # Thursday 11:00: the paper position exits at its target.
    _quotes(fx, EXIT_AT, AAA=(55.2, 55.1, 55.3))
    rep = await run_desk_watch(store=desk_store, broker=FakeBroker(fx, EXIT_AT), notifier=notes,
                               reactions=NoReactions(), rules=RULES, desk=desk,
                               reserve=Decimal("900.00"), now=EXIT_AT)
    assert rep.exits == [(posted[0], "target")]

    sc = await build_scorecard(desk_store, RULES, desk, D01)
    assert (sc.pm_calls.n, sc.all_pitches.n, sc.book.closed_trades) == (1, 1, 1)
    assert sc.pm_calls.mean_excess_spy_pct == "10.00"
    assert sc.selection_edge_pct == "0.00"
