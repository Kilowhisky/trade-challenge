"""Scoring (spec §7): every pitch and every call resolves from stored bars,
once, into `resolutions`. Engine code only; append-only; recomputable from
bars at any time by running `resolve` over the same inputs."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.broker.models import DailyBar
from tc.desk.models import Analyst, Direction
from tc.store.db import Store, now_iso

How = Literal["target", "invalidation", "horizon", "gap_target", "gap_invalidation", "open_through"]
HUNDRED = Decimal(100)
STUCK_AFTER_SESSIONS = 3
CENT = Decimal("0.01")


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pitch", "call"]
    item_id: int
    ref_date: date
    ref_price: Decimal
    resolved_on: date
    how: How
    exit_price: Decimal
    ret_pct: Decimal
    spy_ret_pct: Decimal
    bench_ret_pct: Decimal

    @property
    def excess_spy(self) -> Decimal:
        return self.ret_pct - self.spy_ret_pct

    @property
    def excess_bench(self) -> Decimal:
        return self.ret_pct - self.bench_ret_pct

    @property
    def hit(self) -> bool:
        return self.how in ("target", "gap_target")


@dataclass(frozen=True)
class Item:
    kind: Literal["pitch", "call"]
    item_id: int
    symbol: str
    direction: Direction
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    benchmark: str
    session: date
    # None for a pitch: its reference is its session's opening print.
    ref_price: Decimal | None = None
    spy_ref: Decimal | None = None
    bench_ref: Decimal | None = None


def _move(frm: Decimal, to: Decimal) -> Decimal:
    return (to - frm) / frm * HUNDRED


def resolve(
    item: Item, bars: Sequence[DailyBar], spy: Sequence[DailyBar], bench: Sequence[DailyBar]
) -> Resolution | None:
    by = {b.date: b for b in bars}
    spy_by = {b.date: b for b in spy}
    bench_by = {b.date: b for b in bench}
    days = [b.date for b in spy if b.date >= item.session]   # market sessions
    if not days:
        return None
    start = days[0]
    first = by.get(start)
    if first is None or start not in bench_by:
        return None
    if item.ref_price is None:
        ref, spy_ref, bench_ref = first.open, spy_by[start].open, bench_by[start].open
    else:
        assert item.spy_ref is not None and item.bench_ref is not None
        ref, spy_ref, bench_ref = item.ref_price, item.spy_ref, item.bench_ref
    up = item.direction == "up"

    def through_target(p: Decimal) -> bool:
        return p >= item.target if up else p <= item.target

    def through_inval(p: Decimal) -> bool:
        return p <= item.invalidation if up else p >= item.invalidation

    def done(day: date, how: How, exit_price: Decimal, at_open: bool) -> Resolution | None:
        s, bb = spy_by.get(day), bench_by.get(day)
        if s is None or bb is None:
            return None
        spy_exit = s.open if at_open else s.close
        bench_exit = bb.open if at_open else bb.close
        ret = _move(ref, exit_price)
        bench_move = _move(bench_ref, bench_exit)
        return Resolution(
            kind=item.kind, item_id=item.item_id, ref_date=start, ref_price=ref,
            resolved_on=day, how=how, exit_price=exit_price,
            ret_pct=ret if up else -ret,
            spy_ret_pct=_move(spy_ref, spy_exit),
            bench_ret_pct=bench_move if up else -bench_move,
        )

    for k, day in enumerate(days[: item.horizon_days], start=1):
        b = by.get(day)
        if b is None:
            return None
        if k == 1 and item.ref_price is None:
            if through_target(b.open) or through_inval(b.open):
                return done(day, "open_through", b.open, at_open=True)
        if k > 1:
            if through_target(b.open):
                return done(day, "gap_target", b.open, at_open=True)
            if through_inval(b.open):
                return done(day, "gap_invalidation", b.open, at_open=True)
        if k > 1 or item.ref_price is None:
            inval_hit = b.low <= item.invalidation if up else b.high >= item.invalidation
            target_hit = b.high >= item.target if up else b.low <= item.target
            if inval_hit:
                return done(day, "invalidation", item.invalidation, at_open=False)
            if target_hit:
                return done(day, "target", item.target, at_open=False)
        if k == item.horizon_days:
            return done(day, "horizon", b.close, at_open=False)
    return None


def missing_bar(item: Item, bars: Sequence[DailyBar], spy: Sequence[DailyBar]) -> bool:
    """True when a session inside the item's window has no bar for the symbol
    and is at least STUCK_AFTER_SESSIONS SPY sessions old: a halt, a delisting,
    or a symbol that fell out of the bars set -- a human should look."""
    have = {b.date for b in bars}
    days = [b.date for b in spy if b.date >= item.session][: item.horizon_days]
    all_days = [b.date for b in spy]
    for d in days:
        if d not in have and len(all_days) - 1 - all_days.index(d) >= STUCK_AFTER_SESSIONS:
            return True
    return False


@dataclass(frozen=True)
class ScoreReport:
    resolved: list[Resolution] = field(default_factory=list)
    stuck: list[str] = field(default_factory=list)


async def load_open_items(store: Store) -> list[Item]:
    items: list[Item] = []
    for r in await store.fetchall(
        "SELECT * FROM pitches WHERE id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')"
    ):
        items.append(Item(
            kind="pitch", item_id=r["id"], symbol=r["symbol"], direction=r["direction"],
            target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
            horizon_days=r["horizon_days"], benchmark=r["benchmark"],
            session=date.fromisoformat(r["session"]),
        ))
    for r in await store.fetchall(
        "SELECT * FROM calls WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
    ):
        items.append(Item(
            kind="call", item_id=r["id"], symbol=r["symbol"], direction=r["direction"],
            target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
            horizon_days=r["horizon_days"], benchmark=r["benchmark"],
            session=date.fromisoformat(r["session"]), ref_price=Decimal(r["ref_price"]),
            spy_ref=Decimal(r["spy_ref"]), bench_ref=Decimal(r["bench_ref"]),
        ))
    return items


async def insert_resolution(store: Store, r: Resolution) -> bool:
    """True when this pass wrote it; False when it already existed."""
    before = await store.fetchone(
        "SELECT 1 FROM resolutions WHERE kind=? AND item_id=?", (r.kind, r.item_id)
    )
    if before is not None:
        return False
    await store.execute(
        "INSERT OR IGNORE INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on,"
        " how, exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (r.kind, r.item_id, r.ref_date.isoformat(), str(r.ref_price), r.resolved_on.isoformat(),
         r.how, str(r.exit_price), str(r.ret_pct), str(r.spy_ret_pct), str(r.bench_ret_pct),
         now_iso()),
    )
    return True


def _resolution(row: sqlite3.Row) -> Resolution:
    return Resolution(
        kind=row["kind"], item_id=row["item_id"], ref_date=date.fromisoformat(row["ref_date"]),
        ref_price=Decimal(row["ref_price"]), resolved_on=date.fromisoformat(row["resolved_on"]),
        how=row["how"], exit_price=Decimal(row["exit_price"]), ret_pct=Decimal(row["ret_pct"]),
        spy_ret_pct=Decimal(row["spy_ret_pct"]), bench_ret_pct=Decimal(row["bench_ret_pct"]),
    )


async def resolution_for(store: Store, kind: str, item_id: int) -> Resolution | None:
    row = await store.fetchone(
        "SELECT * FROM resolutions WHERE kind=? AND item_id=?", (kind, item_id)
    )
    return None if row is None else _resolution(row)


async def score(store: Store) -> ScoreReport:
    items = await load_open_items(store)
    if not items:
        return ScoreReport()
    earliest = min(i.session for i in items)
    cache: dict[str, list[DailyBar]] = {}

    async def series(symbol: str) -> list[DailyBar]:
        if symbol not in cache:
            cache[symbol] = await store.bars_for(symbol, since=earliest)
        return cache[symbol]

    spy = await series("SPY")
    resolved: list[Resolution] = []
    stuck: list[str] = []
    for item in items:
        bars = await series(item.symbol)
        r = resolve(item, bars, spy, await series(item.benchmark))
        if r is None:
            if missing_bar(item, bars, spy):
                stuck.append(f"{item.kind}#{item.item_id} {item.symbol}")
            continue
        if await insert_resolution(store, r):
            resolved.append(r)
    return ScoreReport(resolved=resolved, stuck=stuck)


@dataclass(frozen=True)
class ResolvedRow:
    resolution: Resolution
    analyst: Analyst | None     # pitches
    origin: str | None          # calls: 'pitch' | 'pm' | 'legacy'
    conviction: int


async def resolved_rows(store: Store) -> list[ResolvedRow]:
    rows = await store.fetchall(
        "SELECT r.*, p.analyst AS analyst, c.origin AS origin,"
        " COALESCE(p.conviction, c.conviction) AS conv FROM resolutions r"
        " LEFT JOIN pitches p ON r.kind='pitch' AND p.id=r.item_id"
        " LEFT JOIN calls c ON r.kind='call' AND c.id=r.item_id ORDER BY r.id"
    )
    return [
        ResolvedRow(resolution=_resolution(r), analyst=r["analyst"], origin=r["origin"],
                    conviction=int(r["conv"]))
        for r in rows
    ]


class RecordRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    resolved_on: str
    how: str
    ret_pct: str
    excess_spy_pct: str


async def analyst_record(store: Store, analyst: Analyst, limit: int = 10) -> list[RecordRow]:
    rows = await store.fetchall(
        "SELECT r.*, p.symbol AS symbol, p.direction AS direction FROM resolutions r"
        " JOIN pitches p ON r.kind='pitch' AND p.id=r.item_id WHERE p.analyst=?"
        " ORDER BY r.resolved_on DESC, r.id DESC LIMIT ?",
        (analyst, limit),
    )
    out: list[RecordRow] = []
    for row in rows:
        res = _resolution(row)
        out.append(RecordRow(
            symbol=row["symbol"], direction=row["direction"],
            resolved_on=res.resolved_on.isoformat(), how=res.how,
            ret_pct=str(res.ret_pct.quantize(CENT)),
            excess_spy_pct=str(res.excess_spy.quantize(CENT)),
        ))
    return out
