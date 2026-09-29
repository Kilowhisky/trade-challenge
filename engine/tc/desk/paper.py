"""The paper book (spec §10): Claude's funding decisions, filled and exited
by engine code at quoted prices, with the same caps as real money. Vetoed
proposals still fill here, flagged, because this book records Claude's
decisions, not Chris's."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tc.desk.calls import current_call_id, get_call
from tc.desk.models import DeskRefused, Instrument, utc_iso
from tc.desk.sizing import BookState, Holding
from tc.store.db import Store

MULT: dict[str, int] = {"shares": 1, "call": 100, "put": 100}
Outcome = Literal["filled", "skipped_price", "skipped_invalid", "skipped_blind", "expired"]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    call_id: int
    created_at: datetime
    instrument: Instrument
    symbol: str
    underlying: str
    quantity: int
    max_entry_price: Decimal
    atr_pct: Decimal | None
    posted_at: datetime | None
    veto_deadline: datetime | None
    message_id: str | None


class PaperPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    instrument: Instrument
    symbol: str
    underlying: str
    quantity: int
    entry_price: Decimal
    opened_at: datetime
    stop_trigger: Decimal | None
    stop_limit: Decimal | None


class ClosedTrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    instrument: Instrument
    symbol: str
    pnl: Decimal
    ret_pct: Decimal


class ExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    at: datetime
    symbol: str
    proposal_id: int | None
    reason: str


def _opt_dt(v: str | None) -> datetime | None:
    return None if v is None else datetime.fromisoformat(v)


def _opt_dec(v: str | None) -> Decimal | None:
    return None if v is None else Decimal(v)


def _proposal(r: sqlite3.Row) -> Proposal:
    return Proposal(
        id=r["id"], call_id=r["call_id"], created_at=datetime.fromisoformat(r["created_at"]),
        instrument=r["instrument"], symbol=r["symbol"], underlying=r["underlying"],
        quantity=r["quantity"], max_entry_price=Decimal(r["max_entry_price"]),
        atr_pct=_opt_dec(r["atr_pct"]), posted_at=_opt_dt(r["posted_at"]),
        veto_deadline=_opt_dt(r["veto_deadline"]), message_id=r["message_id"],
    )


async def ensure_book(
    store: Store, start_date: date, start_equity: Decimal, now: datetime
) -> tuple[date, Decimal]:
    await store.execute(
        "INSERT OR IGNORE INTO paper_book(id, start_date, start_equity, created_at)"
        " VALUES (1,?,?,?)",
        (start_date.isoformat(), str(start_equity), utc_iso(now)),
    )
    row = await book_row(store)
    assert row is not None
    return row


async def book_row(store: Store) -> tuple[date, Decimal] | None:
    row = await store.fetchone("SELECT start_date, start_equity FROM paper_book WHERE id=1")
    if row is None:
        return None
    return date.fromisoformat(row["start_date"]), Decimal(row["start_equity"])


async def create_proposal(
    store: Store, *, call_id: int, created_at: datetime, instrument: Instrument, symbol: str,
    underlying: str, quantity: int, max_entry_price: Decimal, atr_pct: Decimal | None,
) -> Proposal:
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO proposals(call_id, created_at, instrument, symbol, underlying, quantity,"
            " max_entry_price, atr_pct) VALUES (?,?,?,?,?,?,?,?)",
            (call_id, utc_iso(created_at), instrument, symbol, underlying, quantity,
             str(max_entry_price), None if atr_pct is None else str(atr_pct)),
        )
        pid = int(cur.lastrowid or 0)
    row = await store.fetchone("SELECT * FROM proposals WHERE id=?", (pid,))
    assert row is not None
    return _proposal(row)


async def mark_posted(
    store: Store, proposal_id: int, posted_at: datetime, veto_deadline: datetime,
    message_id: str | None,
) -> None:
    await store.execute(
        "UPDATE proposals SET posted_at=?, veto_deadline=?, message_id=? WHERE id=?",
        (utc_iso(posted_at), utc_iso(veto_deadline), message_id, proposal_id),
    )


async def unposted_proposals(store: Store) -> list[Proposal]:
    rows = await store.fetchall(
        "SELECT * FROM proposals WHERE posted_at IS NULL"
        " AND id NOT IN (SELECT proposal_id FROM proposal_outcomes) ORDER BY id"
    )
    return [_proposal(r) for r in rows]


async def pending_proposals(store: Store) -> list[Proposal]:
    rows = await store.fetchall(
        "SELECT * FROM proposals WHERE id NOT IN (SELECT proposal_id FROM proposal_outcomes)"
        " ORDER BY id"
    )
    return [_proposal(r) for r in rows]


async def record_outcome(
    store: Store, proposal_id: int, at: datetime, outcome: Outcome, *, vetoed: bool,
    approved: bool, detail: dict[str, Any],
) -> None:
    await store.execute(
        "INSERT INTO proposal_outcomes(proposal_id, at, outcome, vetoed, approved, detail_json)"
        " VALUES (?,?,?,?,?,?)",
        (proposal_id, utc_iso(at), outcome, int(vetoed), int(approved),
         json.dumps(detail, default=str, sort_keys=True)),
    )


async def record_fill(
    store: Store, proposal_id: int, at: datetime, side: Literal["buy", "sell"], quantity: int,
    price: Decimal, reason: str, stop_trigger: Decimal | None = None,
    stop_limit: Decimal | None = None,
) -> None:
    await store.execute(
        "INSERT INTO paper_fills(proposal_id, at, side, quantity, price, reason, stop_trigger,"
        " stop_limit) VALUES (?,?,?,?,?,?,?,?)",
        (proposal_id, utc_iso(at), side, quantity, str(price), reason,
         None if stop_trigger is None else str(stop_trigger),
         None if stop_limit is None else str(stop_limit)),
    )


async def open_positions(store: Store) -> list[PaperPosition]:
    rows = await store.fetchall(
        "SELECT * FROM (SELECT p.id AS proposal_id, p.call_id, p.instrument, p.symbol,"
        " p.underlying, b.at AS opened_at, b.price AS entry_price, b.stop_trigger, b.stop_limit,"
        " b.quantity - COALESCE((SELECT SUM(s.quantity) FROM paper_fills s"
        "   WHERE s.proposal_id = p.id AND s.side = 'sell'), 0) AS open_qty"
        " FROM proposals p JOIN paper_fills b ON b.proposal_id = p.id AND b.side = 'buy')"
        " WHERE open_qty > 0 ORDER BY proposal_id"
    )
    return [
        PaperPosition(
            proposal_id=r["proposal_id"], call_id=r["call_id"], instrument=r["instrument"],
            symbol=r["symbol"], underlying=r["underlying"], quantity=int(r["open_qty"]),
            entry_price=Decimal(r["entry_price"]), opened_at=datetime.fromisoformat(r["opened_at"]),
            stop_trigger=_opt_dec(r["stop_trigger"]), stop_limit=_opt_dec(r["stop_limit"]),
        )
        for r in rows
    ]


async def cash(store: Store, start_equity: Decimal) -> Decimal:
    rows = await store.fetchall(
        "SELECT f.side, f.quantity, f.price, p.instrument FROM paper_fills f"
        " JOIN proposals p ON p.id = f.proposal_id"
    )
    total = start_equity
    for r in rows:
        value = Decimal(r["price"]) * int(r["quantity"]) * MULT[r["instrument"]]
        total += value if r["side"] == "sell" else -value
    return total


async def upsert_mark(store: Store, symbol: str, price: Decimal, at: datetime) -> None:
    await store.execute(
        "INSERT INTO paper_marks(symbol, price, at) VALUES (?,?,?) ON CONFLICT(symbol)"
        " DO UPDATE SET price=excluded.price, at=excluded.at",
        (symbol, str(price), utc_iso(at)),
    )


async def marks(store: Store) -> dict[str, Decimal]:
    rows = await store.fetchall("SELECT symbol, price FROM paper_marks")
    return {r["symbol"]: Decimal(r["price"]) for r in rows}


async def closed_trades(store: Store) -> list[ClosedTrade]:
    rows = await store.fetchall(
        "SELECT p.id, p.call_id, p.instrument, p.symbol,"
        " SUM(CASE WHEN f.side='buy' THEN f.quantity ELSE 0 END) AS bq,"
        " SUM(CASE WHEN f.side='sell' THEN f.quantity ELSE 0 END) AS sq"
        " FROM proposals p JOIN paper_fills f ON f.proposal_id = p.id GROUP BY p.id"
        " HAVING bq > 0 AND bq = sq ORDER BY p.id"
    )
    out: list[ClosedTrade] = []
    for r in rows:
        fills = await store.fetchall(
            "SELECT side, quantity, price FROM paper_fills WHERE proposal_id=?", (r["id"],)
        )
        m = MULT[r["instrument"]]
        buy = sum(
            (Decimal(f["price"]) * f["quantity"] * m for f in fills if f["side"] == "buy"),
            Decimal(0),
        )
        sell = sum(
            (Decimal(f["price"]) * f["quantity"] * m for f in fills if f["side"] == "sell"),
            Decimal(0),
        )
        out.append(ClosedTrade(
            proposal_id=r["id"], call_id=r["call_id"],
            instrument=r["instrument"], symbol=r["symbol"],
            pnl=sell - buy, ret_pct=(sell - buy) / buy * 100,
        ))
    return out


async def book_state(store: Store) -> BookState:
    row = await book_row(store)
    if row is None:
        raise DeskRefused("the paper book has not started (it starts at the first PM run)")
    _, start_equity = row
    mk = await marks(store)
    holdings: list[Holding] = []
    for p in await open_positions(store):
        call = await get_call(store, await current_call_id(store, p.call_id))
        price = mk.get(p.symbol, p.entry_price)
        m = MULT[p.instrument]
        is_option = p.instrument != "shares"
        holdings.append(Holding(
            symbol=p.underlying, market_value=price * p.quantity * m,
            benchmark="SPY" if call is None else call.benchmark, is_option=is_option,
            premium_paid=p.entry_price * p.quantity * m if is_option else Decimal(0),
        ))
    c = await cash(store, start_equity)
    return BookState(
        equity=c + sum((h.market_value for h in holdings), Decimal(0)), cash=c,
        holdings=tuple(holdings), pending=len(await pending_proposals(store)),
    )


async def request_exit(
    store: Store, symbol: str, proposal_id: int | None, reason: str, at: datetime
) -> int:
    async with store.transaction() as c:
        cur = await c.execute(
            "INSERT INTO exit_requests(at, symbol, proposal_id, reason) VALUES (?,?,?,?)",
            (utc_iso(at), symbol, proposal_id, reason),
        )
        return int(cur.lastrowid or 0)


async def pending_exit_requests(store: Store) -> list[ExitRequest]:
    rows = await store.fetchall("SELECT * FROM exit_requests WHERE done_at IS NULL ORDER BY id")
    return [
        ExitRequest(id=r["id"], at=datetime.fromisoformat(r["at"]), symbol=r["symbol"],
                    proposal_id=r["proposal_id"], reason=r["reason"])
        for r in rows
    ]


async def mark_exit_done(store: Store, request_id: int, at: datetime) -> None:
    await store.execute("UPDATE exit_requests SET done_at=? WHERE id=?", (utc_iso(at), request_id))
