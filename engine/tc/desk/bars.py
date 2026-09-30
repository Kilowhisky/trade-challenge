"""The desk's price history: one paced fetch per symbol per evening, into
the `bars` table every briefing, screen and score is computed from.

Which symbols: the liquid universe's head by DOLLAR VOLUME (not the weekly
sweep's proximity rank, which only holds names near their highs -- a desk
that looks for breakdowns needs the rest), the ETF list, macro context rows,
SPY always, and every symbol the desk is already carrying.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.clock import trading_days_between
from tc.config import DeskConfig
from tc.store.db import Store

TOP_UP_DAYS = 10
# A symbol whose newest bar is more than this many sessions old gets the full
# history again, not the top-up: after a blind stretch longer than the top-up,
# ten bars would leave a permanent hole that every item spanning it -- and
# every ATR/SMA read across it -- would inherit. Set a little inside
# TOP_UP_DAYS so a late daily candle cannot open a one-session gap either
# (and `trading_days_between` counts holidays as sessions, erring early).
STALE_AFTER_SESSIONS = TOP_UP_DAYS - 2


async def carried_symbols(store: Store) -> set[str]:
    """Symbols of open pitches and calls, their benchmarks, and every paper
    proposal's underlying: a name the desk is scoring must keep its bars
    even after it drops out of the liquid head."""
    rows = await store.fetchall(
        "SELECT symbol FROM pitches"
        " WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='pitch')"
        " UNION SELECT symbol FROM calls"
        " WHERE id NOT IN (SELECT item_id FROM resolutions WHERE kind='call')"
        " UNION SELECT benchmark FROM pitches UNION SELECT benchmark FROM calls"
        " UNION SELECT underlying FROM proposals"
    )
    return {str(r[0]) for r in rows}


async def bars_symbols(store: Store, desk: DeskConfig, extra: Iterable[str]) -> list[str]:
    rows = await store.universe_rows(qualified_only=True)
    rows.sort(key=lambda r: r["dollar_vol"], reverse=True)
    top = [str(r["symbol"]) for r in rows[: desk.bars_universe_size]]
    return sorted({*top, *desk.etf_list, *desk.context_symbols, "SPY", *extra})


@dataclass(frozen=True)
class BarsReport:
    requested: int
    fetched: int
    failed: list[str] = field(default_factory=list)
    blind: bool = False


async def refresh_bars(
    broker: Broker,
    store: Store,
    symbols: Sequence[str],
    desk: DeskConfig,
    *,
    today: date,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> BarsReport:
    """A year for a symbol with little or stale history, ten days to top one
    up.

    A dead token stops the sweep at once -- every further call would fail the
    same way -- and keeps what was already written; the job reports `failed`
    so the deadman sees a partial evening. Any other per-symbol failure (an
    unknown ticker, a 500, a fixture broker with no file) is named and
    skipped: one bad symbol must not cost the evening's scoring.
    """
    counts = await store.bar_counts()
    latest = await store.bar_latest()
    fetched = 0
    failed: list[str] = []
    for i, sym in enumerate(symbols):
        have = counts.get(sym, 0)
        need = desk.bars_history_days - TOP_UP_DAYS
        newest = latest.get(sym)
        stale = newest is None or trading_days_between(newest, today) > STALE_AFTER_SESSIONS
        days = desk.bars_history_days if have < need or stale else TOP_UP_DAYS
        try:
            bars = await broker.daily_bars(sym, days)
        except BrokerUnauthorized:
            return BarsReport(len(symbols), fetched, failed, blind=True)
        except (BrokerError, OSError, ValueError):
            failed.append(sym)
        else:
            await store.upsert_bars(sym, bars)
            fetched += 1
        if desk.bars_request_spacing_s and i + 1 < len(symbols):
            await sleep(desk.bars_request_spacing_s)
    return BarsReport(len(symbols), fetched, failed, blind=False)
