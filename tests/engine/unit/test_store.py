from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc.broker.models import AccountSnapshot, Position
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
