"""Task 7: briefings are computed, not researched (spec §4.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from desk_fixtures import bar, sessions

from tc.broker.fake import FakeBroker
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk.briefing import build_briefing, earnings_screen, technical_screen
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 28, 20, 45, tzinfo=UTC)
DAYS = sessions(date(2025, 9, 1), 230)


def _flat(v: int = 1000) -> list[DailyBar]:
    return [bar(d, 100, 101, 99, 100, v) for d in DAYS[:-1]]


def _with_last(o: float, h: float, lo: float, c: float, v: int) -> list[DailyBar]:
    return [*_flat(), bar(DAYS[-1], o, h, lo, c, v)]


def test_breakouts_and_breakdowns_need_volume_and_etfs_are_macros() -> None:
    series = {
        "UPP": _with_last(100, 111, 100, 110, 3000),
        "DWN": _with_last(100, 100, 89, 90, 3000),
        "QUIET": _with_last(100, 111, 100, 110, 1000),   # breakout without volume
        "XLK": _with_last(100, 111, 100, 110, 3000),     # an ETF: macro's, not technical's
        "SHORT": _with_last(100, 111, 100, 110, 3000)[-50:],
    }
    spy = _flat()
    picks = dict(technical_screen(series, spy, {"XLK"}))
    assert picks["UPP"] == "breakout" and picks["DWN"] == "breakdown"
    assert "XLK" not in picks and "SHORT" not in picks
    assert picks.get("QUIET") != "breakout"


def test_an_earnings_gap_two_sessions_back_is_found() -> None:
    bars = [bar(d, 100, 101, 99, 100, 1000) for d in DAYS[:-2]]
    bars += [bar(DAYS[-2], 106, 107, 105, 106, 5000), bar(DAYS[-1], 106, 107, 105, 106, 1000)]
    assert earnings_screen({"ERN": bars}, set()) == [("ERN", "earnings_gap_1d_ago")]


async def _seed(store: Store) -> None:
    await store.upsert_bars("SPY", _flat())
    await store.upsert_bars("UPP", _with_last(100, 111, 100, 110, 3000))
    await store.upsert_bars("XLK", _with_last(100, 101, 99, 101, 1000))
    await store.upsert_bars("UUP", _flat())


async def test_the_technical_briefing_carries_rows_and_asof(desk_store: Store) -> None:
    await _seed(desk_store)
    b = await build_briefing(desk_store, FakeBroker(FIX, NOW), "technical",
                             DeskConfig(etf_list=["XLK"]))
    assert b.asof == DAYS[-2].isoformat()
    assert [r.symbol for r in b.rows] == ["UPP"] and b.rows[0].screen == "breakout"
    assert b.rows[0].option_band is False     # 110 > $100 and not an ETF


async def test_the_news_briefing_reads_movers_and_survives_their_failure(
    desk_store: Store, tmp_path: Path,
) -> None:
    await _seed(desk_store)
    ok = await build_briefing(desk_store, FakeBroker(FIX, NOW), "news", DeskConfig())
    assert ok.movers and ok.movers_error is None
    broken = await build_briefing(desk_store, FakeBroker(tmp_path, NOW), "news", DeskConfig())
    assert broken.movers == [] and broken.movers_error is not None


async def test_the_macro_briefing_is_the_etf_list_plus_context(desk_store: Store) -> None:
    await _seed(desk_store)
    b = await build_briefing(desk_store, FakeBroker(FIX, NOW), "macro",
                             DeskConfig(etf_list=["XLK"], context_symbols=["UUP"]))
    assert [r.symbol for r in b.rows] == ["XLK"]
    assert [r.symbol for r in b.context] == ["SPY", "UUP"]
