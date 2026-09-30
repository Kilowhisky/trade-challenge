"""Pitches: an analyst's dated, falsifiable prediction (spec §5).

A pitch is filed once and never edited. Its reference price is NOT here:
it is the opening print of its session, read from bars at scoring time
(tc/desk/scoring.py), so no price the model typed is ever the reference.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from tc.clock import ET
from tc.config import DeskConfig
from tc.desk.models import BENCHMARKS, Analyst, DeskRefused, Direction, utc_iso
from tc.rules.model import Rules
from tc.store.db import Store

SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    claim: str = Field(min_length=3, max_length=300)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class PitchIn(BaseModel):
    """What the model submits. Prices are strings; `parse_price` decides."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=10)
    direction: Direction
    thesis: str = Field(min_length=10, max_length=400)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=5)
    target: str
    invalidation: str
    horizon_days: int
    conviction: int = Field(ge=1, le=5)
    benchmark: str


class Pitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    analyst: Analyst
    filed_at: datetime
    session: date
    symbol: str
    direction: Direction
    thesis: str
    evidence: list[EvidenceItem]
    target: Decimal
    invalidation: Decimal
    horizon_days: int
    conviction: int
    benchmark: str
    withdrawn: bool = False


def parse_price(value: str, field: str) -> Decimal:
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise DeskRefused(
            f'{field} must be a plain decimal price string like "48.25", got {value!r}'
        ) from None
    if not d.is_finite() or d <= 0:
        raise DeskRefused(f"{field} must be a positive finite price, got {value!r}")
    return d


def check_levels(
    direction: Direction, last: Decimal, target: Decimal, invalidation: Decimal, rules: Rules
) -> None:
    if direction == "up" and not target > last > invalidation:
        raise DeskRefused(
            f"an up call needs target > last price > invalidation; the last price is {last}"
        )
    if direction == "down" and not target < last < invalidation:
        raise DeskRefused(
            f"a down call needs target < last price < invalidation; the last price is {last}"
        )
    max_dist = rules.get("strategy", "pitch_level_max_distance_pct")
    for name, level in (("target", target), ("invalidation", invalidation)):
        if abs(level - last) / last * 100 > max_dist:
            raise DeskRefused(
                f"{name} {level} is more than {max_dist}% from the last price {last}"
            )


def check_horizon(h: int, rules: Rules) -> None:
    lo = int(rules.get("strategy", "pitch_horizon_min_days"))
    hi = int(rules.get("strategy", "pitch_horizon_max_days"))
    if not lo <= h <= hi:
        raise DeskRefused(f"horizon_days must be {lo}-{hi} trading days, got {h}")


def check_benchmark(b: str) -> str:
    up = b.strip().upper()
    if up not in BENCHMARKS:
        raise DeskRefused(f"benchmark must be one of {', '.join(BENCHMARKS)}, got {b!r}")
    return up


def reference_session(filed_at: datetime, is_trading_day: Callable[[date], bool]) -> date:
    """The first regular session whose OPEN comes after the filing. A holiday
    this cannot see is healed at scoring time (the first SPY session on or
    after this date is used)."""
    et = filed_at.astimezone(ET)
    d = et.date()
    if et.time() < SESSION_OPEN and is_trading_day(d):
        return d
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def _run_window(filed_at: datetime) -> tuple[datetime, str]:
    """The run a pitch belongs to, for the per-run cap: evening runs start at
    16:00, pre-open runs are the pre-09:30 part of the day. Analysts do not
    run during the session, so a filing then is refused outright."""
    et = filed_at.astimezone(ET)
    if et.time() >= SESSION_CLOSE:
        return datetime.combine(et.date(), SESSION_CLOSE, tzinfo=ET), "evening"
    if et.time() < SESSION_OPEN:
        return datetime.combine(et.date(), time(0, 0), tzinfo=ET), "preopen"
    raise DeskRefused(
        "analysts file pitches outside the regular session (before 09:30 or after 16:00 ET)"
    )


async def tradeable_symbols(store: Store, desk: DeskConfig) -> set[str]:
    rows = await store.universe_rows(qualified_only=True)
    return {str(r["symbol"]) for r in rows} | set(desk.etf_list)


async def last_close(store: Store, symbol: str) -> Decimal | None:
    bars = await store.bars_for(symbol, limit=1)
    return bars[-1].close if bars else None


def _pitch(r: sqlite3.Row, withdrawn: bool) -> Pitch:
    return Pitch(
        id=r["id"], analyst=r["analyst"], filed_at=datetime.fromisoformat(r["filed_at"]),
        session=date.fromisoformat(r["session"]), symbol=r["symbol"],
        direction=r["direction"], thesis=r["thesis"],
        evidence=[EvidenceItem.model_validate(e) for e in json.loads(r["evidence_json"])],
        target=Decimal(r["target"]), invalidation=Decimal(r["invalidation"]),
        horizon_days=r["horizon_days"], conviction=r["conviction"],
        benchmark=r["benchmark"], withdrawn=withdrawn,
    )


async def get_pitch(store: Store, pitch_id: int) -> Pitch | None:
    row = await store.fetchone(
        "SELECT p.*, (w.pitch_id IS NOT NULL) AS withdrawn FROM pitches p"
        " LEFT JOIN pitch_withdrawals w ON w.pitch_id = p.id WHERE p.id=?",
        (pitch_id,),
    )
    return None if row is None else _pitch(row, bool(row["withdrawn"]))


async def open_pitches(store: Store) -> list[Pitch]:
    rows = await store.fetchall(
        "SELECT * FROM pitches WHERE id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch') ORDER BY id"
    )
    return [_pitch(r, False) for r in rows]


async def submit_pitch(
    store: Store,
    *,
    analyst: Analyst,
    pin: PitchIn,
    now: datetime,
    last: Decimal,
    tradeable: set[str],
    rules: Rules,
    is_trading_day: Callable[[date], bool],
) -> Pitch:
    symbol = pin.symbol.strip().upper()
    if symbol not in tradeable:
        raise DeskRefused(f"{symbol} is not in the qualified universe or on the ETF list")
    benchmark = check_benchmark(pin.benchmark)
    check_horizon(pin.horizon_days, rules)
    target = parse_price(pin.target, "target")
    invalidation = parse_price(pin.invalidation, "invalidation")
    check_levels(pin.direction, last, target, invalidation, rules)
    start, mode = _run_window(now)
    key = (
        "desk_max_pitches_per_analyst_run"
        if mode == "evening"
        else "desk_preopen_max_new_pitches"
    )
    cap = int(rules.get("strategy", key))
    filed = await store.fetchone(
        "SELECT COUNT(*) AS n FROM pitches WHERE analyst=? AND filed_at >= ?",
        (analyst, utc_iso(start)),
    )
    if filed is not None and int(filed["n"]) >= cap:
        raise DeskRefused(
            f"this run's pitch limit ({cap}) is spent;"
            " rank harder rather than file more"
        )
    dup = await store.fetchone(
        "SELECT id FROM pitches WHERE analyst=? AND symbol=? AND direction=?"
        " AND id NOT IN (SELECT pitch_id FROM pitch_withdrawals)"
        " AND id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')",
        (analyst, symbol, pin.direction),
    )
    if dup is not None:
        raise DeskRefused(
            f"you already have open pitch {dup['id']} on {symbol} {pin.direction};"
            " it stays open until it resolves"
        )
    session = reference_session(now, is_trading_day)
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
            " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                analyst, utc_iso(now), session.isoformat(), symbol, pin.direction,
                pin.thesis, json.dumps([e.model_dump() for e in pin.evidence]),
                str(target), str(invalidation), pin.horizon_days, pin.conviction, benchmark,
            ),
        )
        pid = int(cur.lastrowid or 0)
    return Pitch(
        id=pid, analyst=analyst, filed_at=now.astimezone(UTC), session=session, symbol=symbol,
        direction=pin.direction, thesis=pin.thesis, evidence=pin.evidence, target=target,
        invalidation=invalidation, horizon_days=pin.horizon_days, conviction=pin.conviction,
        benchmark=benchmark,
    )


async def withdraw_pitch(store: Store, *, analyst: Analyst, pitch_id: int, now: datetime) -> None:
    p = await get_pitch(store, pitch_id)
    if p is None or p.analyst != analyst or p.withdrawn:
        raise DeskRefused(f"there is no open pitch {pitch_id} of yours to withdraw")
    opens = datetime.combine(p.session, SESSION_OPEN, tzinfo=ET)
    if now >= opens:
        raise DeskRefused(
            f"pitch {pitch_id} has a reference price already"
            " (its session opened); it can only resolve"
        )
    await store.execute(
        "INSERT INTO pitch_withdrawals(pitch_id, at) VALUES (?,?)", (pitch_id, utc_iso(now))
    )
