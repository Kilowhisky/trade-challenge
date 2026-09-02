import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc.broker.models import AccountSnapshot, OrderLeg, OrderRow, Position
from tc.store.db import SessionStatusRow, Store, TickRow


def _snap() -> AccountSnapshot:
    return AccountSnapshot(
        account_hash="H", read_at=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
        liquidation_value=Decimal("3781.06"), cash_available_for_trading=Decimal("2393.57"),
        unsettled_cash=Decimal("0"), cash_balance=Decimal("2393.57"), cash_call=Decimal("0"),
        is_closing_only_restricted=False,
        positions=[Position(symbol="AMH", asset_type="EQUITY", quantity=29, average_price=Decimal("34.79"),
                            market_value=Decimal("990.64"), day_pl=Decimal("-11.60"), settled_quantity=29)],
    )


def _snap_two_positions() -> AccountSnapshot:
    base = _snap()
    second = base.positions[0].model_copy(update={"symbol": "BMH"})
    return base.model_copy(update={"positions": [base.positions[0], second]})


def _order_row(order_id: int = 1) -> OrderRow:
    return OrderRow(
        order_id=order_id, status="WORKING", order_type="LIMIT", duration="DAY",
        entered_at=datetime(2026, 9, 2, 14, 0, tzinfo=UTC), quantity=10, filled_quantity=0,
        price=Decimal("34.79"), stop_price=None,
        legs=[OrderLeg(instruction="BUY", quantity=10, symbol="AMH", asset_type="EQUITY")],
    )


async def test_opens_migrates_and_is_wal(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    row = await s.fetchone("PRAGMA journal_mode")
    assert row is not None and row[0] == "wal"
    v = await s.fetchone("SELECT MAX(version) FROM schema_version")
    assert v is not None and v[0] >= 1
    await s.close()


async def test_record_account_and_positions(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    sid = await s.record_account(_snap())
    rows = await s.fetchall("SELECT symbol, quantity, market_value FROM position_snapshots WHERE snapshot_id=?", (sid,))
    assert [tuple(r) for r in rows] == [("AMH", 29, "990.64")]
    acct = await s.fetchone("SELECT liquidation_value FROM account_snapshots WHERE id=?", (sid,))
    assert acct is not None and Decimal(acct[0]) == Decimal("3781.06")
    await s.close()


async def test_ticks_are_append_only(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    await s.append_tick(TickRow(at_et="2026-09-02 14:35", state="RTH", account_value=Decimal("3781.06"),
                                comp_capital=Decimal("2881.06"), hwm=Decimal("3800"), drawdown_pct=Decimal("-0.50"),
                                level="OK", positions=1, stops=1, orders=0, settled=Decimal("2393.57"),
                                unsettled=Decimal("0"), reserve=Decimal("2393.57"), flags="-", note=""))
    with pytest.raises(Exception, match="append-only"):
        await s.execute("UPDATE ticks SET level='HALT'")
    with pytest.raises(Exception, match="append-only"):
        await s.execute("DELETE FROM ticks")
    await s.close()


# One minimal INSERT per append-only ledger, by raw SQL so the test asserts the
# TRIGGER rather than whatever the typed helper happens to do. Every ledger in
# schema.sql that carries the no_update/no_delete pair belongs in this table:
# a ledger added later without triggers is only caught if it is listed here.
LEDGER_INSERTS: dict[str, str] = {
    "ticks": (
        "INSERT INTO ticks(at_et, state, account_value, comp_capital, hwm, drawdown_pct, level,"
        " positions, stops, orders, settled, unsettled, reserve, flags, note)"
        " VALUES ('t','RTH','1','1','1','0','OK',0,0,0,'0','0','0','-','')"
    ),
    "job_runs": (
        "INSERT INTO job_runs(job, started_at, ended_at, verdict, detail_json)"
        " VALUES ('tick','a','b','done','{}')"
    ),
    "order_snapshots": (
        "INSERT INTO order_snapshots(account_hash, read_at, order_id, status, order_type,"
        " duration, entered_at, symbol, quantity, filled_quantity, legs_json)"
        " VALUES ('H','a',1,'WORKING','LIMIT','DAY','a','AMH',1,0,'[]')"
    ),
    "token_events": "INSERT INTO token_events(at, kind, detail) VALUES ('a','refresh','ok')",
    "trade_log": (
        "INSERT INTO trade_log(at_et, symbol, action, quantity, price, pretrade_json, note)"
        " VALUES ('a','AMH','BUY',1,'1.00','{}','')"
    ),
}


@pytest.mark.parametrize("ledger", sorted(LEDGER_INSERTS))
async def test_every_ledger_is_append_only(tmp_path: Path, ledger: str) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    await s.execute(LEDGER_INSERTS[ledger])
    with pytest.raises(Exception, match="append-only"):
        await s.execute(f"UPDATE {ledger} SET id = id")  # noqa: S608 -- name from the table above
    with pytest.raises(Exception, match="append-only"):
        await s.execute(f"DELETE FROM {ledger}")  # noqa: S608 -- name from the table above
    await s.close()


async def test_open_twice_is_idempotent(tmp_path: Path) -> None:
    """A second open() used to replace the connection and leak the first."""
    s = Store(tmp_path / "e.db")
    await s.open()
    first = s._conn
    await s.open()
    assert s._conn is first
    await s.close()
    assert s._conn is None


async def test_position_with_no_cost_basis_round_trips_as_null(tmp_path: Path) -> None:
    """average_price is nullable end to end: None must reach SQL as NULL, not '0'."""
    s = Store(tmp_path / "e.db")
    await s.open()
    snap = _snap()
    unknown = snap.positions[0].model_copy(update={"average_price": None})
    sid = await s.record_account(snap.model_copy(update={"positions": [unknown]}))
    row = await s.fetchone(
        "SELECT average_price FROM position_snapshots WHERE snapshot_id=?", (sid,)
    )
    assert row is not None and row[0] is None
    await s.close()


async def test_session_status_roundtrip_and_latest(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    assert await s.latest_session_status() is None
    r1 = SessionStatusRow(date=date(2026, 9, 1), close_value=Decimal("3758"), hwm=Decimal("3800"), halt=Decimal("3040"),
                          drawdown_pct=Decimal("-1.11"), level="OK", prior_hwm=Decimal("3800"), ratcheted=False,
                          intraday_high=None)
    r2 = r1.model_copy(update={"date": date(2026, 9, 2), "close_value": Decimal("3810"), "hwm": Decimal("3810"),
                               "ratcheted": True})
    await s.write_session_status(r1)
    await s.write_session_status(r2)
    latest = await s.latest_session_status()
    assert latest is not None and latest.date == date(2026, 9, 2) and latest.hwm == Decimal("3810")
    await s.close()


async def test_alerts_and_job_runs(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    aid = await s.open_alert("token", "days_until_dead=1.2")
    assert [a.id for a in await s.open_alerts()] == [aid]
    await s.ack_alert(aid)
    assert await s.open_alerts() == []
    t = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)
    await s.record_job_run("tick", t, t, "content_failed", {"reason": "no structured verdict"})
    row = await s.fetchone("SELECT verdict FROM job_runs")
    assert row is not None and row[0] == "content_failed"
    with pytest.raises(ValueError):
        await s.record_job_run("tick", t, t, "ok", {})  # 'ok' is not a verdict; the old deadman's mistake
    await s.close()


async def test_concurrent_read_never_sees_half_written_snapshot(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()

    async def reader() -> list[tuple[int, int]]:
        rows = await s.fetchall(
            "SELECT a.id, COUNT(p.snapshot_id) FROM account_snapshots a"
            " LEFT JOIN position_snapshots p ON p.snapshot_id = a.id"
            " GROUP BY a.id"
        )
        return [(r[0], r[1]) for r in rows]

    results = await asyncio.gather(
        s.record_account(_snap_two_positions()), *[reader() for _ in range(5)]
    )
    for reader_rows in results[1:]:
        assert isinstance(reader_rows, list)
        for _snapshot_id, position_count in reader_rows:
            assert position_count == 2
    await s.close()


async def test_record_orders_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    assert s._conn is not None

    def failing(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(s._conn, "executemany", failing)
    with pytest.raises(RuntimeError, match="boom"):
        await s.record_orders("H", [_order_row()], datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    row = await s.fetchone("SELECT COUNT(*) FROM order_snapshots")
    assert row is not None and row[0] == 0

    monkeypatch.undo()
    await s.record_orders("H", [_order_row()], datetime(2026, 9, 2, 14, 0, tzinfo=UTC))
    row = await s.fetchone("SELECT COUNT(*) FROM order_snapshots")
    assert row is not None and row[0] == 1
    await s.close()
