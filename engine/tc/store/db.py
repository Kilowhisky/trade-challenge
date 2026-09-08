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

from tc.broker.models import RESTING, STOP_TYPES, AccountSnapshot, OrderRow, Position

Verdict = Literal["done", "noop", "content_failed", "failed", "timeout", "missed"]
VERDICTS: frozenset[str] = frozenset(
    {"done", "noop", "content_failed", "failed", "timeout", "missed"}
)
SCHEMA_VERSION = 1

# One dispatch table so `tc.research.ledgers` and the importer agree on which
# physical table backs each ledger name.
LEDGER_TABLES: dict[str, str] = {
    "screen": "screen_rows",
    "iv": "iv_series",
    "oi": "oi_snapshots",
    "tombstones": "tombstones",
    "events": "events",
}
IN_SCOPE_SECTORS = ("consumer-software", "airlines-transport", "semis-hardware")


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
        # Idempotent: a second open() used to build a second connection and
        # drop the first on the floor, leaking it (and its WAL file handle)
        # for the life of the process.
        if self._conn is not None:
            return
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

        There is deliberately no ``CancelledError`` handling here. A
        cancellation between BEGIN and COMMIT unwinds without the ROLLBACK
        above (``except Exception`` does not catch ``BaseException``), which
        is safe only because of one invariant the engine holds: cancellation
        of a store user is only ever followed by ``close()``
        (``Engine.stop()`` cancels every job task and then closes the store,
        in that order). Closing the connection rolls the open transaction
        back implicitly, so the half-written transaction can never be
        committed by a later caller. If a future caller ever cancels a task
        and keeps using the same store, this block needs an explicit
        ``except BaseException`` rollback.
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

    @staticmethod
    def _status_row(row: sqlite3.Row) -> SessionStatusRow:
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

    async def latest_session_status(self) -> SessionStatusRow | None:
        row = await self.fetchone("SELECT * FROM session_status ORDER BY date DESC LIMIT 1")
        return None if row is None else self._status_row(row)

    async def session_status_for(self, d: date) -> SessionStatusRow | None:
        row = await self.fetchone("SELECT * FROM session_status WHERE date=?", (d.isoformat(),))
        return None if row is None else self._status_row(row)

    async def ticks_for(self, at_et_prefix: str) -> list[TickRow]:
        rows = await self.fetchall(
            "SELECT * FROM ticks WHERE at_et LIKE ? ORDER BY id", (at_et_prefix + "%",)
        )
        return [TickRow(**{k: r[k] for k in TickRow.model_fields}) for r in rows]

    async def latest_positions(self) -> dict[str, int]:
        rows = await self.fetchall(
            "SELECT symbol, quantity FROM position_snapshots WHERE snapshot_id ="
            " (SELECT id FROM account_snapshots ORDER BY id DESC LIMIT 1)"
        )
        return {r["symbol"]: int(r["quantity"]) for r in rows}

    async def resting_stops_on(self, d: date) -> dict[str, tuple[Decimal, Decimal]]:
        """The resting stop map (task-11-brief.md, ruling 4) for the newest
        order snapshot read taken on `d`: symbol -> (stop_price, price) for
        every STOP/STOP_LIMIT row whose status is still resting. Used only by
        `tc.shadow.diff_day` -- this reads the store, it never calls the
        broker or writes anything."""
        prefix = d.isoformat()
        newest = await self.fetchone(
            "SELECT read_at FROM order_snapshots WHERE read_at LIKE ?"
            " ORDER BY read_at DESC LIMIT 1",
            (prefix + "%",),
        )
        if newest is None:
            return {}
        rows = await self.fetchall(
            "SELECT symbol, status, order_type, stop_price, price FROM order_snapshots"
            " WHERE read_at = ?",
            (newest["read_at"],),
        )
        result: dict[str, tuple[Decimal, Decimal]] = {}
        for r in rows:
            if r["order_type"] not in STOP_TYPES or r["status"] not in RESTING:
                continue
            if r["stop_price"] is None or r["price"] is None:
                continue
            result[r["symbol"]] = (Decimal(r["stop_price"]), Decimal(r["price"]))
        return result

    async def first_seen(self, symbol: str) -> datetime | None:
        """``read_at`` of the earliest account snapshot that carried `symbol`
        — the §3.5 leveraged-ETF clock's entry point for a position's hold
        count (tc.loops.clocks.run_clocks)."""
        row = await self.fetchone(
            "SELECT a.read_at FROM position_snapshots p"
            " JOIN account_snapshots a ON a.id = p.snapshot_id"
            " WHERE p.symbol = ? ORDER BY a.id LIMIT 1",
            (symbol,),
        )
        return None if row is None else datetime.fromisoformat(row["read_at"])

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

    async def backup_to(self, path: Path) -> None:
        """`VACUUM INTO`: a consistent copy taken through the live connection.

        Copying the file would race the WAL and could land a torn database on
        disk; VACUUM INTO writes a fully checkpointed one. SQLite refuses to
        overwrite an existing target, which is the behaviour we want — the
        backup job checks for today's file and skips rather than clobbering.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            await self._c().execute("VACUUM INTO ?", (str(path),))

    async def job_run_exists(self, job: str, started: datetime) -> bool:
        """Is this exact fire already in the ledger, with any verdict?

        A restarted engine re-enumerates the whole trading day, so most of
        what its scheduler reports as "missed" was actually run — by the
        process before it. Recording a `missed` row over a `done` one would
        turn every restart into a ledger claiming the day never happened.
        """
        row = await self.fetchone(
            "SELECT 1 FROM job_runs WHERE job=? AND started_at=? LIMIT 1",
            (job, started.isoformat()),
        )
        return row is not None

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

    async def ack_alerts_of_kind(self, kind: str) -> int:
        """Ack every open alert of one kind; returns how many were acked.

        The counterpart to `open_alert`. `token_dead` is opened by the token
        loop on every dead/absent check and nothing else ever closed it, so a
        re-auth left a permanent open alert behind — and a standing alert that
        outlives its condition is one an operator stops reading.
        """
        async with self._lock:
            cur = await self._c().execute(
                "UPDATE alerts SET acked_at=? WHERE kind=? AND acked_at IS NULL",
                (_now(), kind),
            )
            return int(cur.rowcount or 0)

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

    async def record_expectation(self, name: str, ok: bool, detail: str) -> None:
        await self.execute(
            "INSERT INTO expectations_log(at, name, ok, detail) VALUES (?,?,?,?)",
            (_now(), name, int(ok), detail),
        )

    async def record_rules_version(self, sha256: str, path: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO rules_versions(sha256, path, seen_at) VALUES (?,?,?)",
            (sha256, path, _now()),
        )

    async def latest_account(self) -> AccountSnapshot | None:
        row = await self.fetchone("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1")
        if row is None:
            return None
        positions = await self.fetchall(
            "SELECT symbol, asset_type, quantity, average_price, market_value, day_pl,"
            " settled_quantity FROM position_snapshots WHERE snapshot_id=?",
            (row["id"],),
        )
        return AccountSnapshot(
            account_hash=row["account_hash"],
            read_at=datetime.fromisoformat(row["read_at"]),
            liquidation_value=Decimal(row["liquidation_value"]),
            cash_available_for_trading=Decimal(row["cash_available_for_trading"]),
            unsettled_cash=Decimal(row["unsettled_cash"]),
            cash_balance=Decimal(row["cash_balance"]),
            cash_call=Decimal(row["cash_call"]),
            is_closing_only_restricted=bool(row["is_closing_only_restricted"]),
            positions=[
                Position(
                    symbol=p["symbol"],
                    asset_type=p["asset_type"],
                    quantity=int(p["quantity"]),
                    average_price=(
                        None if p["average_price"] is None else Decimal(p["average_price"])
                    ),
                    market_value=Decimal(p["market_value"]),
                    day_pl=Decimal(p["day_pl"]),
                    settled_quantity=int(p["settled_quantity"]),
                )
                for p in positions
            ],
        )

    async def latest_tick(self) -> TickRow | None:
        row = await self.fetchone("SELECT * FROM ticks ORDER BY id DESC LIMIT 1")
        return None if row is None else TickRow(**{k: row[k] for k in TickRow.model_fields})

    # --- research ledgers (spec §6) -----------------------------------------
    async def insert_evidence(self, row: dict[str, Any]) -> int:
        async with self._lock:
            cur = await self._c().execute(
                "INSERT INTO evidence(symbol, date, claim, url, source_type, observed,"
                " independence, extra_json, written_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    row["symbol"],
                    row["date"],
                    row["claim"],
                    row["url"],
                    row["source_type"],
                    row["observed"],
                    row["independence"],
                    json.dumps(row.get("extra", {}), sort_keys=True),
                    _now(),
                ),
            )
            return int(cur.lastrowid or 0)

    async def evidence_for(self, symbol: str) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT symbol, date, claim, url, source_type, observed, independence,"
            " extra_json, written_at FROM evidence WHERE symbol=? ORDER BY id",
            (symbol,),
        )
        return [
            {
                "symbol": r["symbol"],
                "date": r["date"],
                "claim": r["claim"],
                "url": r["url"],
                "source_type": r["source_type"],
                "observed": r["observed"],
                "independence": r["independence"],
                "extra": json.loads(r["extra_json"]),
                "written_at": r["written_at"],
            }
            for r in rows
        ]

    async def insert_escalation(self, row: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO escalations(id, kind, symbol, at, record_json, written_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                row["id"],
                row["kind"],
                row["symbol"],
                row["at"],
                json.dumps(row["record"], sort_keys=True),
                _now(),
            ),
        )

    async def escalation_raise_exists(self, escalation_id: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM escalations WHERE id=? AND kind='raise' LIMIT 1", (escalation_id,)
        )
        return row is not None

    async def escalations(self, symbol: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, symbol, at, record_json FROM escalations"
        sql += "" if symbol is None else " WHERE symbol = ?"
        sql += " ORDER BY row_id"
        rows = await self.fetchall(sql, () if symbol is None else (symbol,))
        raises: dict[str, dict[str, Any]] = {}
        for r in rows:
            rec = json.loads(r["record_json"])
            if r["kind"] == "raise":
                raises[r["id"]] = {
                    "id": r["id"],
                    "symbol": r["symbol"],
                    "raised": r["at"],
                    "latest_outcome": None,
                    **rec,
                }
            elif r["id"] in raises:
                # last score wins: this is a plain overwrite in row order
                raises[r["id"]]["latest_outcome"] = rec.get("outcome")
        return list(raises.values())

    async def upsert_sectors(self, rows: list[tuple[str, str, str]]) -> tuple[int, int]:
        """Replace each named symbol's tag; count what changed.

        `new` is a symbol that had no in-scope tag and now has one; `retired`
        is one that had an in-scope tag and is now `other`. That is what the
        v2 return line reported and what the weekly digest reads. Symbols are
        compared as TEXT throughout -- awk's numeric compare made tagging `1E2`
        delete the row for `100`, and on BSD awk the real ticker `NAN` too.
        """
        new = retired = 0
        async with self._transaction() as c:
            for symbol, sector, d in rows:
                cur = await c.execute("SELECT sector FROM sectors WHERE symbol=?", (symbol,))
                prev_row = await cur.fetchone()
                prev = None if prev_row is None else str(prev_row[0])
                if sector in IN_SCOPE_SECTORS and prev not in IN_SCOPE_SECTORS:
                    new += 1
                if sector == "other" and prev in IN_SCOPE_SECTORS:
                    retired += 1
                await c.execute(
                    "INSERT INTO sectors (symbol, sector, date) VALUES (?,?,?)"
                    " ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector, date=excluded.date",
                    (symbol, sector, d),
                )
        return new, retired

    async def sectors(self) -> list[dict[str, Any]]:
        rows = await self.fetchall("SELECT symbol, sector, date FROM sectors ORDER BY symbol")
        return [{"symbol": r["symbol"], "sector": r["sector"], "date": r["date"]} for r in rows]

    async def replace_universe(self, asof: date, rows: list[dict[str, Any]]) -> int:
        """Delete `asof`'s own rows first so a re-run of the same sweep is
        idempotent; older `asof` rows are left alone as history."""
        a = asof.isoformat()
        async with self._transaction() as c:
            await c.execute("DELETE FROM universe WHERE asof=?", (a,))
            await c.executemany(
                "INSERT INTO universe(asof, symbol, price, adv10, dollar_vol,"
                " pct_from_52wk_high, optionable, leverage, last_earnings, is_etf,"
                " session_range_pct, description, qualified)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        a,
                        r["symbol"],
                        _s(r["price"]),
                        _s(r["adv10"]),
                        _s(r["dollar_vol"]),
                        _s(r["pct_from_52wk_high"]),
                        int(r["optionable"]),
                        _s(r["leverage"]),
                        r["last_earnings"],
                        int(r["is_etf"]),
                        _s(r.get("session_range_pct")),
                        r["description"],
                        int(r["qualified"]),
                    )
                    for r in rows
                ],
            )
        return len(rows)

    async def universe_asof(self) -> date | None:
        row = await self.fetchone("SELECT asof FROM universe ORDER BY asof DESC LIMIT 1")
        return None if row is None else date.fromisoformat(row["asof"])

    async def universe_rows(self, qualified_only: bool = True) -> list[dict[str, Any]]:
        """Rows from the newest `asof` only; older sweeps are kept as history
        but never read back through this accessor."""
        asof = await self.universe_asof()
        if asof is None:
            return []
        sql = "SELECT * FROM universe WHERE asof=?"
        params: tuple[Any, ...] = (asof.isoformat(),)
        if qualified_only:
            sql += " AND qualified=1"
        rows = await self.fetchall(sql + " ORDER BY symbol", params)
        return [
            {
                "symbol": r["symbol"],
                "price": Decimal(r["price"]),
                "adv10": Decimal(r["adv10"]),
                "dollar_vol": Decimal(r["dollar_vol"]),
                "pct_from_52wk_high": Decimal(r["pct_from_52wk_high"]),
                "optionable": bool(r["optionable"]),
                "leverage": Decimal(r["leverage"]),
                "last_earnings": r["last_earnings"],
                "is_etf": bool(r["is_etf"]),
                "session_range_pct": (
                    None if r["session_range_pct"] is None else Decimal(r["session_range_pct"])
                ),
                "description": r["description"],
                "qualified": bool(r["qualified"]),
            }
            for r in rows
        ]

    async def append_ledger(
        self, name: str, d: date, symbol: str | None, record: dict[str, Any]
    ) -> None:
        table = LEDGER_TABLES[name]
        await self.execute(
            f"INSERT INTO {table} (date, symbol, record_json, written_at) VALUES (?,?,?,?)",  # noqa: S608
            (d.isoformat(), symbol or "", json.dumps(record, sort_keys=True), _now()),
        )

    async def ledger_rows(
        self, name: str, d: date | None = None, latest_before: date | None = None
    ) -> list[dict[str, Any]]:
        table = LEDGER_TABLES[name]
        if latest_before is not None:
            row = await self.fetchone(
                f"SELECT date FROM {table} WHERE date < ? ORDER BY date DESC LIMIT 1",  # noqa: S608
                (latest_before.isoformat(),),
            )
            if row is None:
                return []
            d = date.fromisoformat(row["date"])
        sql = f"SELECT date, symbol, record_json FROM {table}"  # noqa: S608
        params: tuple[Any, ...] = ()
        if d is not None:
            sql += " WHERE date = ?"
            params = (d.isoformat(),)
        rows = await self.fetchall(sql + " ORDER BY id", params)
        return [
            {"date": r["date"], "symbol": r["symbol"], "record": json.loads(r["record_json"])}
            for r in rows
        ]

    async def oi_symbol_seen(self, d: date, symbol: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM oi_snapshots WHERE date=? AND symbol=?", (d.isoformat(), symbol)
        )
        return row is not None

    async def record_artifact(
        self, kind: str, d: date | None, path: str, sha256: str, lines: int
    ) -> None:
        await self.execute(
            "INSERT INTO artifacts(kind, date, path, sha256, lines, written_at)"
            " VALUES (?,?,?,?,?,?)",
            (kind, None if d is None else d.isoformat(), path, sha256, lines, _now()),
        )
