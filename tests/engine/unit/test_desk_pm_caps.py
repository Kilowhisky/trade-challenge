"""Final review C1: every funded call in one PM run sizes against the calls
funded before it in the same run (spec §9.2, §10: "the $900 reserve and all
caps"), plus the Task 13 refusal paths that were deferred to this fix -- the
correlated-set refusal and the put happy path."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any

import pytest
from desk_fixtures import RULES, desk_settings, sessions

from tc.broker.fake import FakeBroker
from tc.broker.models import DailyBar
from tc.desk.calls import NewCall, insert_call
from tc.desk.models import DeskRefused, Instrument
from tc.desk.paper import create_proposal, ensure_book, record_fill, record_outcome
from tc.desk.pm import CallIn, CallOut, PmContext, submit_call
from tc.store.db import Store

NOW = datetime(2026, 9, 29, 13, 50, tzinfo=UTC)      # Tue 09:50 ET
MS = int(NOW.timestamp() * 1000)
TODAY = date(2026, 9, 29)
NAMES = ("AAA", "BBB", "CCC")


def noisy_bars(seed: int, n: int = 80) -> list[DailyBar]:
    """A random walk near 50 with ~2-3% daily ranges: an ATR the share
    ceiling accepts, and returns that do not correlate across seeds."""
    rng = Random(seed)  # noqa: S311 -- test data, not a secret
    out: list[DailyBar] = []
    p = Decimal(50)
    for d in sessions(date(2026, 6, 1), n):
        c = (p * (1 + Decimal(rng.randint(-15, 15)) / 1000)).quantize(Decimal("0.01"))
        hi, lo = max(p, c) * Decimal("1.01"), min(p, c) * Decimal("0.99")
        out.append(DailyBar(date=d, open=p, high=hi.quantize(Decimal("0.01")),
                            low=lo.quantize(Decimal("0.01")), close=c, volume=1_000_000))
        p = c
    return out


def _quote(last: float, bid: float, ask: float) -> dict[str, Any]:
    return {"quote": {"lastPrice": last, "bidPrice": bid, "askPrice": ask, "quoteTime": MS},
            "reference": {"description": "X"}}


def _osi(sym: str, kind: str = "C") -> str:
    return f"{sym:<6}261120{kind}00050000"


def _contract(sym: str, kind: str, delta: float, bid: float, ask: float) -> dict[str, Any]:
    return {"symbol": _osi(sym, kind[0]), "putCall": kind, "strikePrice": 50.0, "bid": bid,
            "ask": ask, "last": (bid + ask) / 2, "delta": delta, "openInterest": 1200,
            "totalVolume": 100, "volatility": 30.0, "daysToExpiration": 52,
            "expirationDate": "2026-11-20T21:00:00.000+00:00"}


def _fx(tmp_path: Path) -> Path:
    d = tmp_path / "fx"
    d.mkdir(exist_ok=True)
    quotes = {"SPY": _quote(500, 499.99, 500.01), "XLK": _quote(200, 199.99, 200.01),
              "XLE": _quote(80, 79.99, 80.01)}
    for s in NAMES:
        quotes[s] = _quote(50, 49.99, 50.01)
        quotes[_osi(s)] = _quote(3.50, 3.45, 3.55)
        quotes[_osi(s, "P")] = _quote(2.45, 2.40, 2.50)
        (d / f"chain-{s}.json").write_text(json.dumps({
            "symbol": s, "underlyingPrice": 50.0,
            "callExpDateMap": {"2026-11-20:52": {"50.0": [_contract(s, "CALL", 0.58, 3.45, 3.55)]}},
            "putExpDateMap": {"2026-11-20:52": {"50.0": [_contract(s, "PUT", -0.55, 2.40, 2.50)]}},
        }))
    (d / "quotes.json").write_text(json.dumps(quotes))
    return d


def _row(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "price": Decimal(50), "adv10": Decimal(1_000_000),
            "dollar_vol": Decimal(50_000_000), "pct_from_52wk_high": Decimal(1),
            "optionable": True, "leverage": Decimal(0), "last_earnings": "", "is_etf": False,
            "session_range_pct": Decimal("1.5"), "description": symbol, "qualified": True}


async def _ctx(store: Store, tmp_path: Path, seeds: dict[str, int] | None = None) -> PmContext:
    seeds = seeds or {"AAA": 1, "BBB": 2, "CCC": 3}
    await store.replace_universe(date(2026, 9, 26), [_row(s) for s in NAMES])
    for s, seed in seeds.items():
        await store.upsert_bars(s, noisy_bars(seed))
    await ensure_book(store, TODAY, Decimal("3700"), NOW)
    return PmContext(store=store, broker=FakeBroker(_fx(tmp_path), NOW), rules=RULES,
                     desk=desk_settings(tmp_path).desk, reserve=Decimal("900.00"), now=NOW)


async def _held(store: Store, symbol: str, instrument: Instrument, qty: int, price: str,
                benchmark: str = "SPY") -> None:
    """A position the book already holds: a call, its proposal, the entry fill."""
    call = await insert_call(store, NewCall(
        made_at=NOW, session=date(2026, 9, 28), origin="pm", pitch_id=None,
        extends_call_id=None, symbol=symbol, direction="up", thesis="held from yesterday",
        target=Decimal(60), invalidation=Decimal(40), horizon_days=10, conviction=4,
        benchmark=benchmark, ref_price=Decimal(50), spy_ref=Decimal(500),
        bench_ref=Decimal(200), funding="shares" if instrument == "shares" else instrument))
    sym = symbol if instrument == "shares" else _osi(symbol)
    p = await create_proposal(store, call_id=call.id, created_at=NOW, instrument=instrument,
                              symbol=sym, underlying=symbol, quantity=qty,
                              max_entry_price=Decimal(price), atr_pct=None)
    await record_fill(store, p.id, NOW, "buy", qty, Decimal(price), "entry")
    await record_outcome(store, p.id, NOW, "filled", vetoed=False, approved=False, detail={})


def _shares(symbol: str, benchmark: str = "SPY") -> CallIn:
    return CallIn.model_validate({
        "symbol": symbol, "direction": "up", "thesis": "orders are accelerating into the quarter",
        "target": "55", "invalidation": "48", "horizon_days": 10, "conviction": 5,
        "benchmark": benchmark, "funding": "shares", "max_entry_price": "50.10"})


def _option(symbol: str) -> CallIn:
    return CallIn.model_validate({
        "symbol": symbol, "direction": "up", "thesis": "orders are accelerating into the quarter",
        "target": "55", "invalidation": "48", "horizon_days": 10, "conviction": 5,
        "benchmark": "SPY", "funding": "call", "option_symbol": _osi(symbol).replace(" ", ""),
        "max_entry_price": "3.70"})


def _funded(out: CallOut) -> Decimal:
    assert out.proposal is not None
    mult = 1 if out.proposal.instrument == "shares" else 100
    return Decimal(out.proposal.max_entry_price) * out.proposal.quantity * mult


async def test_the_third_funded_call_in_one_run_is_refused_at_the_reserve(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    await _held(desk_store, "ZZZ", "shares", 20, "50")         # cash 2700, equity 3700
    first = await submit_call(ctx, _shares("AAA"))
    second = await submit_call(ctx, _shares("BBB"))
    # 14 shares x 50.10 = 701.40 each: 2700 - 1402.80 = 1297.20 left, and a
    # third 701.40 would leave 595.80 -- through the 900.00 reserve.
    assert _funded(first) == _funded(second) == Decimal("701.40")
    with pytest.raises(DeskRefused, match="reserve"):
        await submit_call(ctx, _shares("CCC"))


async def test_the_third_funded_call_in_one_run_is_refused_on_the_sector_cluster(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    await submit_call(ctx, _shares("AAA", "XLK"))
    await submit_call(ctx, _shares("BBB", "XLK"))
    # 2 x 701.40 pending in XLK names; a third makes 2104.20 > 50% of 3700.
    with pytest.raises(DeskRefused, match=r"CCC clusters with AAA, BBB .*§3.8"):
        await submit_call(ctx, _shares("CCC", "XLK"))


async def test_the_third_funded_option_in_one_run_is_refused_on_open_premium(
    desk_store: Store, tmp_path: Path,
) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    await _held(desk_store, "ZZZ", "call", 1, "3.60")          # 360.00 premium already open
    first = await submit_call(ctx, _option("AAA"))
    second = await submit_call(ctx, _option("BBB"))
    # 360 + 370 + 370 = 1100 <= 30% of 3700 = 1110; a third 370 would be 1470.
    assert _funded(first) == _funded(second) == Decimal("370.00")
    with pytest.raises(DeskRefused, match=r"open premium 1100.*§3.2"):
        await submit_call(ctx, _option("CCC"))


async def test_a_measured_correlation_clusters_across_sectors(
    desk_store: Store, tmp_path: Path,
) -> None:
    # BBB moves exactly as AAA does (same walk), and is held under XLE.
    ctx = await _ctx(desk_store, tmp_path, seeds={"AAA": 1, "BBB": 1, "CCC": 3})
    await _held(desk_store, "BBB", "shares", 30, "50", benchmark="XLE")    # 1500 held
    with pytest.raises(DeskRefused, match=r"AAA clusters with BBB .*§3.8"):
        await submit_call(ctx, _shares("AAA", "XLK"))
    # An uncorrelated name in the same sector as nothing held is fine.
    assert (await submit_call(ctx, _shares("CCC", "XLK"))).proposal is not None


async def test_a_down_call_is_funded_with_a_put(desk_store: Store, tmp_path: Path) -> None:
    ctx = await _ctx(desk_store, tmp_path)
    out = await submit_call(ctx, CallIn.model_validate({
        "symbol": "AAA", "direction": "down", "thesis": "guidance cut is not priced in yet",
        "target": "45", "invalidation": "52", "horizon_days": 10, "conviction": 4,
        "benchmark": "XLK", "funding": "put", "option_symbol": "AAA261120P00050000"}))
    assert out.proposal is not None
    assert (out.proposal.instrument, out.proposal.symbol, out.proposal.quantity) == (
        "put", _osi("AAA", "P"), 1)
