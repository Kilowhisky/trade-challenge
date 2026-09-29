"""Task 4: which symbols get bars, and the paced fetch that fills them."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk.bars import bars_symbols, carried_symbols, refresh_bars
from tc.store.db import Store

NOW = datetime(2026, 9, 28, 20, 10, tzinfo=UTC)


def _candles(n: int) -> dict[str, list[dict[str, float | int]]]:
    base = 1_756_771_200_000  # 2025-09-02
    return {"candles": [
        {"datetime": base + i * 86_400_000, "open": 10, "high": 11, "low": 9, "close": 10,
         "volume": 1000}
        for i in range(n)
    ]}


def _fx(tmp_path: Path, symbols: Sequence[str], n: int = 5) -> Path:
    for s in symbols:
        (tmp_path / f"bars-{s}.json").write_text(json.dumps(_candles(n)))
    return tmp_path


def _row(symbol: str, dollar_vol: int) -> dict[str, Any]:
    return {
        "symbol": symbol, "price": Decimal("50"), "adv10": Decimal("100000"),
        "dollar_vol": Decimal(dollar_vol), "pct_from_52wk_high": Decimal("1"),
        "optionable": True, "leverage": Decimal("0"), "last_earnings": "",
        "is_etf": False, "session_range_pct": Decimal("1.5"), "description": symbol,
        "qualified": True,
    }


async def test_symbols_are_the_most_liquid_names_plus_etfs_context_and_carried(
    desk_store: Store,
) -> None:
    await desk_store.replace_universe(date(2026, 9, 26), [
        _row("LOW", 10), _row("MID", 20), _row("TOP", 30),
    ])
    desk = DeskConfig(etf_list=["XLK"], context_symbols=["UUP"], bars_universe_size=2)
    got = await bars_symbols(desk_store, desk, {"HELD"})
    assert got == sorted({"TOP", "MID", "XLK", "UUP", "SPY", "HELD"})


async def test_carried_symbols_is_empty_on_a_fresh_store(desk_store: Store) -> None:
    assert await carried_symbols(desk_store) == set()


async def test_refresh_fetches_a_year_for_a_new_symbol_and_ten_days_after(
    tmp_path: Path, desk_store: Store,
) -> None:
    asked: list[tuple[str, int]] = []

    class Spy(FakeBroker):
        async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
            asked.append((symbol, days))
            return await super().daily_bars(symbol, days)

    broker = Spy(_fx(tmp_path, ["AAA"], n=300), NOW)
    desk = DeskConfig(bars_request_spacing_s=0)
    rep = await refresh_bars(broker, desk_store, ["AAA"], desk)
    assert (rep.fetched, rep.failed, rep.blind) == (1, [], False)
    rep = await refresh_bars(broker, desk_store, ["AAA"], desk)
    assert asked == [("AAA", 260), ("AAA", 10)]


async def test_a_symbol_the_broker_cannot_answer_is_named_not_fatal(
    tmp_path: Path, desk_store: Store,
) -> None:
    broker = FakeBroker(_fx(tmp_path, ["AAA"]), NOW)  # no bars-BBB.json
    rep = await refresh_bars(broker, desk_store, ["AAA", "BBB"], DeskConfig(bars_request_spacing_s=0))
    assert (rep.fetched, rep.failed, rep.blind) == (1, ["BBB"], False)


async def test_a_dead_token_mid_sweep_stops_and_says_blind(
    tmp_path: Path, desk_store: Store,
) -> None:
    class DiesAfterOne(FakeBroker):
        calls = 0

        async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
            DiesAfterOne.calls += 1
            if DiesAfterOne.calls > 1:
                raise BrokerUnauthorized("401")
            return await super().daily_bars(symbol, days)

    broker = DiesAfterOne(_fx(tmp_path, ["AAA", "BBB", "CCC"]), NOW)
    rep = await refresh_bars(broker, desk_store, ["AAA", "BBB", "CCC"],
                             DeskConfig(bars_request_spacing_s=0))
    assert rep.blind is True and rep.fetched == 1
    assert await desk_store.bar_counts() == {"AAA": 5}   # what was fetched is kept


async def test_requests_are_paced(tmp_path: Path, desk_store: Store) -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    broker = FakeBroker(_fx(tmp_path, ["AAA", "BBB"]), NOW)
    await refresh_bars(broker, desk_store, ["AAA", "BBB"], DeskConfig(bars_request_spacing_s=0.5),
                       sleep=sleep)
    assert slept == [0.5]  # between calls, never after the last
