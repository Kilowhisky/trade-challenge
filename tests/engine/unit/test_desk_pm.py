"""Task 13: the PM's calls and funding (spec §6, §9)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from desk_fixtures import RULES, desk_deps, desk_settings, trend_bars
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
from tc.broker.models import AccountSnapshot, Position
from tc.desk.calls import NewCall, insert_call
from tc.desk.models import DeskRefused
from tc.desk.paper import create_proposal, ensure_book, record_fill
from tc.desk.pm import CallIn, PmContext, extend_call, request_exit_for, submit_call, tighten_call
from tc.mcp import tools_desk
from tc.store.db import Store

NOW = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)      # Tue 09:50 ET
MS = int(NOW.timestamp() * 1000)
OSI = "AAA   261120C00050000"
TODAY = date(2026, 9, 29)


def _quote(last: float, bid: float | None = None, ask: float | None = None,
           age_s: int = 0) -> dict[str, Any]:
    return {"quote": {"lastPrice": last, "bidPrice": bid if bid is not None else last - 0.01,
                      "askPrice": ask if ask is not None else last + 0.01,
                      "quoteTime": MS - age_s * 1000},
            "reference": {"description": "X"}}


def _contract(kind: str, delta: float) -> dict[str, Any]:
    return {"symbol": OSI if kind == "CALL" else "AAA   261120P00050000", "putCall": kind,
            "strikePrice": 50.0, "bid": 2.40, "ask": 2.50, "last": 2.45, "delta": delta,
            "openInterest": 1200, "totalVolume": 100, "volatility": 30.0,
            "daysToExpiration": 52, "expirationDate": "2026-11-20T21:00:00.000+00:00"}


def _fx(tmp_path: Path, aaa_age: int = 0) -> Path:
    d = tmp_path / "fx"
    d.mkdir(exist_ok=True)
    (d / "quotes.json").write_text(json.dumps({
        "AAA": _quote(50, age_s=aaa_age), "SPY": _quote(500), "XLK": _quote(200),
        "CSX": _quote(46.78), OSI: _quote(2.45, 2.40, 2.50),
    }))
    (d / "chain-AAA.json").write_text(json.dumps({
        "symbol": "AAA", "underlyingPrice": 50.0,
        "callExpDateMap": {"2026-11-20:52": {"50.0": [_contract("CALL", 0.58)]}},
        "putExpDateMap": {"2026-11-20:52": {"50.0": [_contract("PUT", -0.42)]}},
    }))
    return d


def _row(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "price": Decimal(50), "adv10": Decimal(1_000_000),
            "dollar_vol": Decimal(50_000_000), "pct_from_52wk_high": Decimal(1),
            "optionable": True, "leverage": Decimal(0), "last_earnings": "", "is_etf": False,
            "session_range_pct": Decimal("1.5"), "description": symbol, "qualified": True}


async def _ctx(store: Store, tmp_path: Path, *, aaa_age: int = 0, spread: str = "1") -> PmContext:
    await store.replace_universe(date(2026, 9, 26), [_row("AAA"), _row("CSX")])
    await store.upsert_bars("AAA", trend_bars(date(2026, 6, 1), 80, first="40", step="0.125",
                                              spread=spread))
    await store.upsert_bars("SPY", trend_bars(date(2026, 6, 1), 80, first="480", step="0.25"))
    await ensure_book(store, TODAY, Decimal("3700"), NOW)
    settings = desk_settings(tmp_path)
    return PmContext(store=store, broker=FakeBroker(_fx(tmp_path, aaa_age), NOW), rules=RULES,
                     desk=settings.desk, reserve=Decimal("900.00"), now=NOW)


def _in(**kw: Any) -> CallIn:
    base: dict[str, Any] = {"symbol": "AAA", "direction": "up",
                            "thesis": "orders are accelerating into the quarter", "target": "55",
                            "invalidation": "48", "horizon_days": 10, "conviction": 3,
                            "benchmark": "XLK"}
    base.update(kw)
    return CallIn.model_validate(base)


async def _pitch(store: Store, symbol: str = "AAA") -> int:
    await store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis, evidence_json,"
        " target, invalidation, horizon_days, conviction, benchmark) VALUES ('technical',"
        " '2026-09-28T20:45:00+00:00', '2026-09-29', ?, 'up', 't', '[]', '55', '48', 10, 3, 'XLK')",
        (symbol,),
    )
    row = await store.fetchone("SELECT MAX(id) AS id FROM pitches")
    assert row is not None
    return int(row["id"])


async def test_an_unfunded_call_adopts_a_pitch_and_stamps_its_reference(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(pitch_id=await _pitch(desk_store)))
    assert (out.origin, out.ref_price, out.proposal) == ("pitch", "50.00", None)
    row = await desk_store.fetchone("SELECT spy_ref, bench_ref FROM calls WHERE id=?", (out.call_id,))
    assert row is not None and (row["spy_ref"], row["bench_ref"]) == ("500.00", "200.00")


async def test_adopting_a_pitch_on_another_symbol_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    pid = await _pitch(desk_store, "CSX")
    with pytest.raises(DeskRefused, match=f"pitch {pid} is CSX up"):
        await submit_call(ctx, _in(pitch_id=pid))


async def test_a_stale_quote_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path, aaa_age=600)
    with pytest.raises(DeskRefused, match="stale-quote gate"):
        await submit_call(ctx, _in())


async def test_the_sixth_call_of_the_day_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    for i in range(5):
        await insert_call(desk_store, NewCall(
            made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
            symbol=f"Z{i}", direction="up", thesis="t" * 12, target=Decimal(2), invalidation=Decimal(1),
            horizon_days=5, conviction=3, benchmark="SPY", ref_price=Decimal("1.5"),
            spy_ref=Decimal(500), bench_ref=Decimal(500), funding="none"))
    with pytest.raises(DeskRefused, match="today's 5 new calls"):
        await submit_call(ctx, _in())


async def test_shares_funding_sizes_by_conviction(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(funding="shares", max_entry_price="50.50"))
    assert out.proposal is not None
    assert (out.proposal.instrument, out.proposal.quantity, out.proposal.max_entry_price) == (
        "shares", 7, "50.50")                               # 370.00 // 50.50


@pytest.mark.parametrize("kw,match", [
    ({"direction": "down", "target": "45", "invalidation": "52"}, "shares fund up calls only"),
    ({"max_entry_price": "53"}, "chases more than 5"),
    ({"conviction": 2}, "scored, never funded"),
])
async def test_share_funding_refusals(
    desk_store: Store, tmp_path: Path, kw: dict[str, Any], match: str,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match=match):
        await submit_call(ctx, _in(**{"funding": "shares", "max_entry_price": "50.50", **kw}))


async def test_a_volatile_name_is_never_share_funded(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path, spread="4")
    with pytest.raises(DeskRefused, match="6% share ceiling"):
        await submit_call(ctx, _in(funding="shares", max_entry_price="50.50"))


async def test_call_submit_accepts_an_option_symbol_without_padding(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, _in(conviction=4, funding="call",
                                     option_symbol="AAA261120C00050000"))
    assert out.proposal is not None
    assert (out.proposal.symbol, out.proposal.quantity) == (OSI, 1)   # 277.50 // 250


async def test_an_option_the_floors_did_not_offer_is_refused(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match="not among the contracts"):
        await submit_call(ctx, _in(conviction=4, funding="call", option_symbol="AAA261120C00055000"))


async def test_a_legacy_call_needs_a_real_position(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    with pytest.raises(DeskRefused, match="not a position in the real account"):
        await submit_call(ctx, _in(symbol="CSX", target="50", invalidation="44", legacy=True))
    await desk_store.record_account(AccountSnapshot(
        account_hash="H", read_at=NOW, liquidation_value=Decimal("3700"),
        cash_available_for_trading=Decimal("3200"), unsettled_cash=Decimal(0),
        cash_balance=Decimal("3200"), cash_call=Decimal(0), is_closing_only_restricted=False,
        positions=[Position(symbol="CSX", asset_type="EQUITY", quantity=4,
                            average_price=Decimal("50.375"), market_value=Decimal("187.12"),
                            day_pl=Decimal(0), settled_quantity=4)],
    ))
    out = await submit_call(ctx, _in(symbol="CSX", target="50", invalidation="44", legacy=True))
    assert out.origin == "legacy"
    assert await request_exit_for(ctx, "CSX", "thesis gone") == "legacy"


async def _held_call(store: Store, how: str | None = None) -> int:
    call = await insert_call(store, NewCall(
        made_at=NOW, session=TODAY, origin="pm", pitch_id=None, extends_call_id=None,
        symbol="AAA", direction="up", thesis="t" * 12, target=Decimal(55), invalidation=Decimal(48),
        horizon_days=5, conviction=3, benchmark="XLK", ref_price=Decimal(50),
        spy_ref=Decimal(500), bench_ref=Decimal(200), funding="shares"))
    p = await create_proposal(store, call_id=call.id, created_at=NOW, instrument="shares",
                              symbol="AAA", underlying="AAA", quantity=7,
                              max_entry_price=Decimal("50.50"), atr_pct=Decimal(2))
    await record_fill(store, p.id, NOW, "buy", 7, Decimal(50), "entry", Decimal(48), Decimal("45.60"))
    if how is not None:
        await store.execute(
            "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
            " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at) VALUES ('call', ?,"
            " '2026-09-22', '50', '2026-09-28', ?, '50', '0', '0', '0', 'x')",
            (call.id, how),
        )
    return call.id


async def test_a_horizon_call_with_a_position_extends_once(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store, how="horizon")
    new = await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                            thesis="the move is intact, the clock ran out")
    with pytest.raises(DeskRefused, match="already used its one extension"):
        await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")
    with pytest.raises(DeskRefused, match="already used its one extension"):
        await extend_call(ctx, new.call_id, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")


async def test_a_target_resolved_call_does_not_extend(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store, how="target")
    with pytest.raises(DeskRefused, match="reached its horizon"):
        await extend_call(ctx, cid, target="56", invalidation="48.5", horizon_days=5,
                          thesis="the move is intact, the clock ran out")


async def test_tightening_moves_only_toward_the_price(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    cid = await _held_call(desk_store)
    assert await tighten_call(ctx, cid, "49") == Decimal(49)
    with pytest.raises(DeskRefused, match="between the current level 49"):
        await tighten_call(ctx, cid, "48.5")
    assert await request_exit_for(ctx, "AAA", "target reached in spirit") == "paper"


async def test_midday_cannot_make_calls(desk_store: Store, tmp_path: Path) -> None:
    await _ctx(desk_store, tmp_path)
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_desk.register(
        server, desk_deps(desk_store, tmp_path, FakeBroker(_fx(tmp_path), NOW), NOW, "pm_midday"),
        "decide",
    )
    with pytest.raises(ToolError, match="no new calls at midday"):
        await server._tool_manager.call_tool("call_submit", {
            "symbol": "AAA", "direction": "up", "thesis": "orders are accelerating",
            "target": "55", "invalidation": "48", "horizon_days": 10, "conviction": 3,
            "benchmark": "XLK",
        })
    book = await server._tool_manager.call_tool("paper_book", {})
    assert book.started is True
