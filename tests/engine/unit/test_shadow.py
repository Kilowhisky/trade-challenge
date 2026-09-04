"""Task 11: shadow diff against the old ledgers (Phase 0 exit criterion).

Fixtures under tests/engine/fixtures/legacy/ are synthetic: HASH_REDACTED,
order ids starting 1000000000001, never a real account.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc import cli
from tc.broker.models import OrderLeg, OrderRow
from tc.shadow import LegacyStatus, LegacyTick, diff_day, parse_legacy_status, parse_legacy_ticks
from tc.store.db import SessionStatusRow, Store, TickRow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legacy"
STATUS_PATH = FIXTURES / "status-2026-09-03.md"
TICKS_PATH = FIXTURES / "ticks-2026-09-03.tsv"
DAY = date(2026, 9, 3)
RESERVE = Decimal("900.00")
ACCOUNT_HASH = "HASH_REDACTED"


# --- parsers ----------------------------------------------------------------


def test_parse_legacy_status_reads_only_the_current_block() -> None:
    status = parse_legacy_status(STATUS_PATH)
    assert status == LegacyStatus(
        hwm=Decimal("3800.00"),
        account_value=Decimal("3728.71"),
        positions={"AMH": 29, "CSX": 4},
        stops={"AMH": (Decimal("32.01"), Decimal("30.40")), "CSX": (Decimal("45.20"), Decimal("42.93"))},
    )


def test_parse_legacy_status_ignores_the_superseded_block() -> None:
    """The file carries an earlier "State recorded — superseded" block with
    its own Account value / High-water mark lines. Those must never win."""
    status = parse_legacy_status(STATUS_PATH)
    assert status.hwm != Decimal("3750.00")
    assert status.account_value != Decimal("3700.00")


def test_parse_legacy_status_missing_hwm_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text("### State recorded — current\n\n- Account value: **$100.00**\n")
    with pytest.raises(ValueError, match="High-water mark"):
        parse_legacy_status(bad)


def test_parse_legacy_ticks_reads_14_columns() -> None:
    rows = parse_legacy_ticks(TICKS_PATH)
    assert rows == [
        LegacyTick(
            time_et="09:32", state="RTH", comp_capital=Decimal("2821.86"), hwm=Decimal("3800.00"),
            dd_pct=Decimal("-2.1"), level="OK", positions=2, stops=2, orders=0,
            settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
            flags="-", note="-",
        ),
        LegacyTick(
            time_et="12:00", state="RTH", comp_capital=Decimal("2828.71"), hwm=Decimal("3800.00"),
            dd_pct=Decimal("-1.9"), level="OK", positions=2, stops=2, orders=0,
            settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
            flags="-", note="-",
        ),
    ]


def test_parse_legacy_ticks_wrong_column_count_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tsv"
    bad.write_text("time_et\tstate\n09:32\tRTH\n")
    with pytest.raises(ValueError, match="14"):
        parse_legacy_ticks(bad)


# --- store fixtures -----------------------------------------------------------


def _stop_order(order_id: int, symbol: str, qty: int, trigger: Decimal, limit_: Decimal) -> OrderRow:
    return OrderRow(
        order_id=order_id, status="WORKING", order_type="STOP_LIMIT", duration="GTC",
        entered_at=datetime(2026, 9, 3, 14, 0, tzinfo=UTC), quantity=qty, filled_quantity=0,
        price=limit_, stop_price=trigger,
        legs=[OrderLeg(instruction="SELL", quantity=qty, symbol=symbol, asset_type="EQUITY")],
    )


async def _seed_matching_store(store: Store) -> None:
    await store.write_session_status(
        SessionStatusRow(
            date=DAY, close_value=Decimal("3728.71"), hwm=Decimal("3800.00"), halt=Decimal("3040.00"),
            drawdown_pct=Decimal("-1.88"), level="OK", prior_hwm=Decimal("3800.00"), ratcheted=False,
            intraday_high=None,
        )
    )
    await store.record_orders(
        ACCOUNT_HASH,
        [
            _stop_order(1000000000001, "AMH", 29, Decimal("32.01"), Decimal("30.40")),
            _stop_order(1000000000002, "CSX", 4, Decimal("45.20"), Decimal("42.93")),
        ],
        datetime(2026, 9, 3, 19, 15, tzinfo=UTC),
    )
    for time_et, comp_capital in (("09:32", Decimal("2821.86")), ("12:00", Decimal("2828.71"))):
        await store.append_tick(
            TickRow(
                at_et=f"{DAY.isoformat()} {time_et}", state="RTH",
                account_value=comp_capital + RESERVE, comp_capital=comp_capital, hwm=Decimal("3800.00"),
                drawdown_pct=Decimal("-2.1"), level="OK", positions=2, stops=2, orders=0,
                settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
                flags="-", note="-",
            )
        )


# --- diff_day -----------------------------------------------------------------


async def test_matching_day_is_ok(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    await _seed_matching_store(store)
    result = await diff_day(store, DAY, STATUS_PATH, TICKS_PATH, RESERVE)
    await store.close()
    assert result.hwm_match is True
    assert result.missing_engine_session is False
    assert result.stop_map_match is True
    assert result.value_diffs == []
    assert result.state_diffs == []
    assert result.missing_engine_ticks == []
    assert result.missing_legacy_ticks == []
    assert result.ok is True


async def test_value_drift_is_listed(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    await _seed_matching_store(store)
    # A $0.02 drift on an extra 15:00 tick, on top of the matching seed above.
    await store.append_tick(
        TickRow(
            at_et=f"{DAY.isoformat()} 15:00", state="RTH",
            account_value=Decimal("2821.86") + RESERVE + Decimal("0.02"), comp_capital=Decimal("2821.86"),
            hwm=Decimal("3800.00"), drawdown_pct=Decimal("-2.1"), level="OK", positions=2, stops=2,
            orders=0, settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
            flags="-", note="-",
        )
    )
    # Re-point a legacy tick at 15:00 too, by writing an extended ticks file.
    ticks_with_drift = tmp_path / "ticks.tsv"
    ticks_with_drift.write_text(
        TICKS_PATH.read_text()
        + "15:00\tRTH\t2821.86\t3800.00\t-2.1\tOK\t2\t2\t0\t2393.57\t0.00\t2393.57\t-\t-\n"
    )
    result = await diff_day(store, DAY, STATUS_PATH, ticks_with_drift, RESERVE)
    await store.close()
    assert result.value_diffs == [
        ("15:00", Decimal("2821.86") + RESERVE + Decimal("0.02"), Decimal("2821.86") + RESERVE)
    ]
    assert result.ok is False


async def test_stop_map_difference_is_false(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    await store.write_session_status(
        SessionStatusRow(
            date=DAY, close_value=Decimal("3728.71"), hwm=Decimal("3800.00"), halt=Decimal("3040.00"),
            drawdown_pct=Decimal("-1.88"), level="OK", prior_hwm=Decimal("3800.00"), ratcheted=False,
            intraday_high=None,
        )
    )
    # CSX's resting stop limit is 42.90 at the broker, not 42.93 in the legacy record.
    await store.record_orders(
        ACCOUNT_HASH,
        [
            _stop_order(1000000000001, "AMH", 29, Decimal("32.01"), Decimal("30.40")),
            _stop_order(1000000000002, "CSX", 4, Decimal("45.20"), Decimal("42.90")),
        ],
        datetime(2026, 9, 3, 19, 15, tzinfo=UTC),
    )
    for time_et, comp_capital in (("09:32", Decimal("2821.86")), ("12:00", Decimal("2828.71"))):
        await store.append_tick(
            TickRow(
                at_et=f"{DAY.isoformat()} {time_et}", state="RTH",
                account_value=comp_capital + RESERVE, comp_capital=comp_capital, hwm=Decimal("3800.00"),
                drawdown_pct=Decimal("-2.1"), level="OK", positions=2, stops=2, orders=0,
                settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
                flags="-", note="-",
            )
        )
    result = await diff_day(store, DAY, STATUS_PATH, TICKS_PATH, RESERVE)
    await store.close()
    assert result.stop_map_match is False
    assert result.ok is False


async def test_missing_engine_session_makes_hwm_match_false(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    # No write_session_status call at all -- session_status_for(DAY) is None.
    result = await diff_day(store, DAY, STATUS_PATH, TICKS_PATH, RESERVE)
    await store.close()
    assert result.missing_engine_session is True
    assert result.hwm_match is False
    assert result.ok is False


async def test_missing_ticks_on_either_side_are_reported(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    await store.write_session_status(
        SessionStatusRow(
            date=DAY, close_value=Decimal("3728.71"), hwm=Decimal("3800.00"), halt=Decimal("3040.00"),
            drawdown_pct=Decimal("-1.88"), level="OK", prior_hwm=Decimal("3800.00"), ratcheted=False,
            intraday_high=None,
        )
    )
    await store.record_orders(
        ACCOUNT_HASH,
        [
            _stop_order(1000000000001, "AMH", 29, Decimal("32.01"), Decimal("30.40")),
            _stop_order(1000000000002, "CSX", 4, Decimal("45.20"), Decimal("42.93")),
        ],
        datetime(2026, 9, 3, 19, 15, tzinfo=UTC),
    )
    # Only one of the two legacy tick times has an engine counterpart; the
    # engine also has a tick at a time the legacy ledger never recorded.
    await store.append_tick(
        TickRow(
            at_et=f"{DAY.isoformat()} 09:32", state="RTH", account_value=Decimal("2821.86") + RESERVE,
            comp_capital=Decimal("2821.86"), hwm=Decimal("3800.00"), drawdown_pct=Decimal("-2.1"),
            level="OK", positions=2, stops=2, orders=0, settled=Decimal("2393.57"),
            unsettled=Decimal("0.00"), reserve=Decimal("2393.57"), flags="-", note="-",
        )
    )
    await store.append_tick(
        TickRow(
            at_et=f"{DAY.isoformat()} 14:45", state="RTH", account_value=Decimal("2821.86") + RESERVE,
            comp_capital=Decimal("2821.86"), hwm=Decimal("3800.00"), drawdown_pct=Decimal("-2.1"),
            level="OK", positions=2, stops=2, orders=0, settled=Decimal("2393.57"),
            unsettled=Decimal("0.00"), reserve=Decimal("2393.57"), flags="-", note="-",
        )
    )
    result = await diff_day(store, DAY, STATUS_PATH, TICKS_PATH, RESERVE)
    await store.close()
    assert result.missing_engine_ticks == ["12:00"]
    assert result.missing_legacy_ticks == ["14:45"]
    assert result.ok is False


async def test_state_mismatch_is_listed(tmp_path: Path) -> None:
    store = Store(tmp_path / "e.db")
    await store.open()
    await store.write_session_status(
        SessionStatusRow(
            date=DAY, close_value=Decimal("3728.71"), hwm=Decimal("3800.00"), halt=Decimal("3040.00"),
            drawdown_pct=Decimal("-1.88"), level="OK", prior_hwm=Decimal("3800.00"), ratcheted=False,
            intraday_high=None,
        )
    )
    await store.record_orders(
        ACCOUNT_HASH,
        [
            _stop_order(1000000000001, "AMH", 29, Decimal("32.01"), Decimal("30.40")),
            _stop_order(1000000000002, "CSX", 4, Decimal("45.20"), Decimal("42.93")),
        ],
        datetime(2026, 9, 3, 19, 15, tzinfo=UTC),
    )
    # 09:32 is legacy RTH; the engine recorded it as STALE.
    for time_et, comp_capital, state in (
        ("09:32", Decimal("2821.86"), "STALE"),
        ("12:00", Decimal("2828.71"), "RTH"),
    ):
        await store.append_tick(
            TickRow(
                at_et=f"{DAY.isoformat()} {time_et}", state=state,
                account_value=comp_capital + RESERVE, comp_capital=comp_capital, hwm=Decimal("3800.00"),
                drawdown_pct=Decimal("-2.1"), level="OK", positions=2, stops=2, orders=0,
                settled=Decimal("2393.57"), unsettled=Decimal("0.00"), reserve=Decimal("2393.57"),
                flags="-", note="-",
            )
        )
    result = await diff_day(store, DAY, STATUS_PATH, TICKS_PATH, RESERVE)
    await store.close()
    assert result.state_diffs == [("09:32", "STALE", "RTH")]
    assert result.ok is False


# --- CLI ----------------------------------------------------------------------


CONFIG = """
engine: {timezone: America/New_York, data_dir: "%s", repo_dir: "%s", http_bind: 127.0.0.1:8080, reserve_usd: "900.00"}
token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://pi.example.ts.net/oauth/callback}
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\nTC_DISCORD_WEBHOOK_URL=https://d.example/h\n"


def _cfg(tmp_path: Path) -> list[str]:
    (tmp_path / "config.yml").write_text(CONFIG % (tmp_path, tmp_path))
    (tmp_path / ".env").write_text(ENV)
    return ["--config", str(tmp_path / "config.yml"), "--env", str(tmp_path / ".env")]


def _store_dir(tmp_path: Path) -> Path:
    store_dir = tmp_path / "store"
    (store_dir / "status" / "ticks").mkdir(parents=True)
    (store_dir / "status" / "2026-09-03.md").write_text(STATUS_PATH.read_text())
    (store_dir / "status" / "ticks" / "2026-09-03.tsv").write_text(TICKS_PATH.read_text())
    return store_dir


def test_cli_shadow_diff_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _cfg(tmp_path)
    store_dir = _store_dir(tmp_path)

    async def seed() -> None:
        store = Store(tmp_path / "engine.db")
        await store.open()
        await _seed_matching_store(store)
        await store.close()

    asyncio.run(seed())
    rc = cli.main([*args, "shadow-diff", "2026-09-03", "--store-dir", str(store_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().endswith("SHADOW OK")


def test_cli_shadow_diff_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = _cfg(tmp_path)
    store_dir = _store_dir(tmp_path)

    async def seed() -> None:
        # No session status, no orders, no ticks at all: hwm_match False,
        # stop_map_match False, and every legacy tick is a missing_engine_tick.
        store = Store(tmp_path / "engine.db")
        await store.open()
        await store.close()

    asyncio.run(seed())
    rc = cli.main([*args, "shadow-diff", "2026-09-03", "--store-dir", str(store_dir)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "SHADOW DIFF" in out
    assert "HWM" in out


def test_cli_shadow_diff_missing_status_file_exits_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    store_dir = tmp_path / "store"
    (store_dir / "status" / "ticks").mkdir(parents=True)
    rc = cli.main([*args, "shadow-diff", "2026-09-03", "--store-dir", str(store_dir)])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: ")
    assert "status" in err


def test_cli_shadow_diff_missing_ticks_file_exits_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    store_dir = tmp_path / "store"
    (store_dir / "status" / "ticks").mkdir(parents=True)
    (store_dir / "status" / "2026-09-03.md").write_text(STATUS_PATH.read_text())
    rc = cli.main([*args, "shadow-diff", "2026-09-03", "--store-dir", str(store_dir)])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: ")
    assert "ticks" in err
