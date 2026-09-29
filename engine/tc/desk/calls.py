"""Calls (spec §6): the PM's predictions. Append-only rows; an extension is
a NEW call pointing back at the one it continues, and a tightening is a new
row that moves the exit level -- never an edit to the call as made, which is
what gets scored."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.desk.models import Direction, Funding, utc_iso
from tc.store.db import Store


class NewCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    made_at: datetime
    session: date
    origin: Literal["pitch", "pm", "legacy"]
    pitch_id: int | None
    extends_call_id: int | None
    symbol: str
    direction: Direction
    thesis: str
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    conviction: int
    benchmark: str
    ref_price: Decimal
    spy_ref: Decimal
    bench_ref: Decimal
    funding: Funding


class Call(NewCall):
    id: int


def _call(r: sqlite3.Row) -> Call:
    return Call(
        id=r["id"], made_at=datetime.fromisoformat(r["made_at"]),
        session=date.fromisoformat(r["session"]), origin=r["origin"], pitch_id=r["pitch_id"],
        extends_call_id=r["extends_call_id"], symbol=r["symbol"], direction=r["direction"],
        thesis=r["thesis"], target=Decimal(r["target"]),
        invalidation=Decimal(r["invalidation"]), horizon_days=r["horizon_days"],
        conviction=r["conviction"], benchmark=r["benchmark"],
        ref_price=Decimal(r["ref_price"]), spy_ref=Decimal(r["spy_ref"]),
        bench_ref=Decimal(r["bench_ref"]), funding=r["funding"],
    )


async def insert_call(store: Store, c: NewCall) -> Call:
    async with store.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO calls(made_at, session, origin, pitch_id, extends_call_id, symbol,"
            " direction, thesis, target, invalidation, horizon_days, conviction, benchmark,"
            " ref_price, spy_ref, bench_ref, funding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                utc_iso(c.made_at), c.session.isoformat(), c.origin, c.pitch_id,
                c.extends_call_id, c.symbol, c.direction, c.thesis, str(c.target),
                str(c.invalidation), c.horizon_days, c.conviction, c.benchmark,
                str(c.ref_price), str(c.spy_ref), str(c.bench_ref), c.funding,
            ),
        )
        cid = int(cur.lastrowid or 0)
    got = await get_call(store, cid)
    assert got is not None
    return got


async def get_call(store: Store, call_id: int) -> Call | None:
    row = await store.fetchone("SELECT * FROM calls WHERE id=?", (call_id,))
    return None if row is None else _call(row)


async def open_call_for(
    store: Store, symbol: str, direction: Direction, *, legacy: bool = False
) -> Call | None:
    # One of two literal operators, chosen by a bool: never input.
    op = "=" if legacy else "!="
    row = await store.fetchone(
        f"SELECT * FROM calls WHERE symbol=? AND direction=? AND origin {op} 'legacy'"  # noqa: S608
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
        " ORDER BY id DESC LIMIT 1",
        (symbol, direction),
    )
    return None if row is None else _call(row)


async def calls_made_on(store: Store, session: date) -> int:
    row = await store.fetchone(
        "SELECT COUNT(*) AS n FROM calls WHERE session=? AND origin IN ('pitch','pm')"
        " AND extends_call_id IS NULL",
        (session.isoformat(),),
    )
    return 0 if row is None else int(row["n"])


async def current_call_id(store: Store, call_id: int) -> int:
    cur = call_id
    while True:
        row = await store.fetchone("SELECT id FROM calls WHERE extends_call_id=?", (cur,))
        if row is None:
            return cur
        cur = int(row["id"])


async def is_extended(store: Store, call_id: int) -> bool:
    row = await store.fetchone("SELECT 1 FROM calls WHERE extends_call_id=?", (call_id,))
    return row is not None


async def tighten(store: Store, call_id: int, invalidation: Decimal, now: datetime) -> None:
    await store.execute(
        "INSERT INTO call_tightenings(call_id, at, invalidation) VALUES (?,?,?)",
        (call_id, utc_iso(now), str(invalidation)),
    )


async def effective_invalidation(store: Store, call: Call) -> Decimal:
    row = await store.fetchone(
        "SELECT invalidation FROM call_tightenings WHERE call_id=? ORDER BY id DESC LIMIT 1",
        (call.id,),
    )
    return call.invalidation if row is None else Decimal(row["invalidation"])
