"""SQLite store: one writer, WAL, Decimal-as-text, append-only ledgers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict

from tc.broker.models import AccountSnapshot, OrderRow

Verdict = Literal["done", "noop", "content_failed", "failed", "timeout", "missed"]
VERDICTS: frozenset[str] = frozenset(
    {"done", "noop", "content_failed", "failed", "timeout", "missed"}
)
SCHEMA_VERSION = 1


class SessionStatusRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    close_value: Decimal
    hwm: Decimal
    halt: Decimal
    drawdown_pct: Decimal
    level: Literal["OK", "HALT"]
    prior_hwm: Decimal
    ratcheted: bool
    intraday_high: Decimal | None


class TickRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_et: str
    state: Literal["RTH", "PRE", "POST", "STALE", "BLIND"]
    account_value: Decimal
    comp_capital: Decimal
    hwm: Decimal
    drawdown_pct: Decimal
    level: Literal["OK", "HALT"]
    positions: int
    stops: int
    orders: int
    settled: Decimal
    unsettled: Decimal
    reserve: Decimal
    flags: str
    note: str


class AlertRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    opened_at: datetime
    kind: str
    message: str
    acked_at: datetime | None


def _s(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # autocommit; we BEGIN explicitly where a transaction is needed
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        schema = resources.files("tc.store").joinpath("schema.sql").read_text()
        await self._conn.executescript(schema)
        await self._conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _now()),
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("store not opened")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._lock:
            await self._c().execute(sql, params)

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        async with self._lock:
            cur = await self._c().execute(sql, params)
            return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        async with self._lock:
            cur = await self._c().execute(sql, params)
            row = await cur.fetchone()
            return row

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """BEGIN/COMMIT (or ROLLBACK on exception) around the block.

        Acquires ``self._lock`` itself — there is one connection and one
        writer queue, so callers must NOT already hold the lock when they
        enter this context manager (doing so would deadlock).
        """
        async with self._lock:
            c = self._c()
            await c.execute("BEGIN")
            try:
                yield c
                await c.execute("COMMIT")
            except Exception:
                await c.execute("ROLLBACK")
                raise

    # --- typed helpers -----------------------------------------------------
    async def record_account(self, s: AccountSnapshot) -> int:
        async with self._transaction() as c:
            cur = await c.execute(
                "INSERT INTO account_snapshots(account_hash, read_at, liquidation_value,"
                " cash_available_for_trading, unsettled_cash, cash_balance, cash_call,"
                " is_closing_only_restricted) VALUES (?,?,?,?,?,?,?,?)",
                (
                    s.account_hash,
                    s.read_at.isoformat(),
                    _s(s.liquidation_value),
                    _s(s.cash_available_for_trading),
                    _s(s.unsettled_cash),
                    _s(s.cash_balance),
                    _s(s.cash_call),
                    int(s.is_closing_only_restricted),
                ),
            )
            sid = int(cur.lastrowid or 0)
            # Yield point inside the held lock: a concurrent reader gets
            # queued on self._lock and cannot observe the account_snapshots
            # row without its position_snapshots rows, no matter where the
            # writer yields between BEGIN and COMMIT.
            await asyncio.sleep(0)
            await c.executemany(
                "INSERT INTO position_snapshots(snapshot_id, symbol, asset_type, quantity,"
                " average_price, market_value, day_pl, settled_quantity)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        sid,
                        p.symbol,
                        p.asset_type,
                        p.quantity,
                        _s(p.average_price),
                        _s(p.market_value),
                        _s(p.day_pl),
                        p.settled_quantity,
                    )
                    for p in s.positions
                ],
            )
        return sid

    async def record_orders(
        self, account_hash: str, rows: list[OrderRow], read_at: datetime
    ) -> None:
        async with self._transaction() as c:
            await c.executemany(
                "INSERT INTO order_snapshots(account_hash, read_at, order_id, status, order_type,"
                " duration, entered_at, symbol, quantity, filled_quantity, price, stop_price,"
                " legs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        account_hash,
                        read_at.isoformat(),
                        o.order_id,
                        o.status,
                        o.order_type,
                        o.duration,
                        o.entered_at.isoformat(),
                        o.symbol,
                        o.quantity,
                        o.filled_quantity,
                        _s(o.price),
                        _s(o.stop_price),
                        json.dumps([leg.model_dump() for leg in o.legs]),
                    )
                    for o in rows
                ],
            )

    async def append_tick(self, t: TickRow) -> None:
        d = t.model_dump()
        cols = ",".join(d)
        await self.execute(
            f"INSERT INTO ticks({cols}) VALUES ({','.join('?' * len(d))})",  # noqa: S608
            tuple(str(v) if isinstance(v, Decimal) else v for v in d.values()),
        )

    async def write_session_status(self, r: SessionStatusRow) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO session_status(date, close_value, hwm, halt, drawdown_pct,"
            " level, prior_hwm, ratcheted, intraday_high, written_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                r.date.isoformat(),
                _s(r.close_value),
                _s(r.hwm),
                _s(r.halt),
                _s(r.drawdown_pct),
                r.level,
                _s(r.prior_hwm),
                int(r.ratcheted),
                _s(r.intraday_high),
                _now(),
            ),
        )

    async def latest_session_status(self) -> SessionStatusRow | None:
        row = await self.fetchone("SELECT * FROM session_status ORDER BY date DESC LIMIT 1")
        if row is None:
            return None
        return SessionStatusRow(
            date=date.fromisoformat(row["date"]),
            close_value=Decimal(row["close_value"]),
            hwm=Decimal(row["hwm"]),
            halt=Decimal(row["halt"]),
            drawdown_pct=Decimal(row["drawdown_pct"]),
            level=row["level"],
            prior_hwm=Decimal(row["prior_hwm"]),
            ratcheted=bool(row["ratcheted"]),
            intraday_high=None if row["intraday_high"] is None else Decimal(row["intraday_high"]),
        )

    async def record_job_run(
        self,
        job: str,
        started: datetime,
        ended: datetime,
        verdict: str,
        detail: dict[str, Any],
    ) -> None:
        if verdict not in VERDICTS:
            raise ValueError(f"not a verdict: {verdict!r} (one of {sorted(VERDICTS)})")
        await self.execute(
            "INSERT INTO job_runs(job, started_at, ended_at, verdict, detail_json)"
            " VALUES (?,?,?,?,?)",
            (job, started.isoformat(), ended.isoformat(), verdict, json.dumps(detail, default=str)),
        )

    async def record_token_event(self, kind: str, detail: str) -> None:
        await self.execute(
            "INSERT INTO token_events(at, kind, detail) VALUES (?,?,?)", (_now(), kind, detail)
        )

    async def open_alert(self, kind: str, message: str) -> int:
        async with self._lock:
            cur = await self._c().execute(
                "INSERT INTO alerts(opened_at, kind, message) VALUES (?,?,?)",
                (_now(), kind, message),
            )
            return int(cur.lastrowid or 0)

    async def ack_alert(self, alert_id: int) -> None:
        await self.execute(
            "UPDATE alerts SET acked_at=? WHERE id=? AND acked_at IS NULL", (_now(), alert_id)
        )

    async def open_alerts(self) -> list[AlertRow]:
        rows = await self.fetchall("SELECT * FROM alerts WHERE acked_at IS NULL ORDER BY id")
        return [
            AlertRow(
                id=r["id"],
                opened_at=datetime.fromisoformat(r["opened_at"]),
                kind=r["kind"],
                message=r["message"],
                acked_at=None,
            )
            for r in rows
        ]

    async def record_rules_version(self, sha256: str, path: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO rules_versions(sha256, path, seen_at) VALUES (?,?,?)",
            (sha256, path, _now()),
        )
