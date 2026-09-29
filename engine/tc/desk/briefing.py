"""Briefings (spec §4.1): the numbers each analyst starts from, computed by
engine code from stored bars and one movers read. The model adds judgement
-- why a move matters, what comes next, where it is wrong -- not measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import DailyBar
from tc.config import DeskConfig
from tc.desk import indicators as ind
from tc.desk.models import Analyst
from tc.desk.pitches import open_pitches
from tc.desk.scoring import RecordRow, analyst_record
from tc.store.db import Store

SCREEN_ORDER = ("breakout", "breakdown", "volume_spike", "pullback", "rs_top", "rs_bottom")
MIN_HISTORY = 200
MAX_TECH_ROWS = 40
MAX_EARN_ROWS = 30
MAX_GAP_ROWS = 20
MOVERS_PER_SIDE = 10
RS_MIN_UNIVERSE = 30
RS_PICKS = 10
OPTION_BAND_MAX = Decimal(100)
HISTORY = 260
CENT = Decimal("0.01")


class BriefRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    screen: str
    last: str
    chg_1d_pct: str | None
    atr_pct: str | None
    vs_sma20_pct: str | None
    vs_sma50_pct: str | None
    vs_sma200_pct: str | None
    rs_3m: str | None
    rs_6m: str | None
    from_52w_high_pct: str | None
    from_52w_low_pct: str | None
    vol_ratio: str | None
    gap_pct: str | None
    option_band: bool


class MoverRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    last: str
    net_percent_change: str
    volume: int


class OpenPitchRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    symbol: str
    direction: str
    session: str
    target: str
    invalidation: str
    horizon_days: int
    conviction: int


class Briefing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyst: Analyst
    asof: str | None
    rows: list[BriefRow]
    context: list[BriefRow]
    movers: list[MoverRow]
    movers_error: str | None
    record: list[RecordRow]
    open_pitches: list[OpenPitchRow]
    notes: list[str]


def _q(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(CENT))


def row_for(
    symbol: str, bars: Sequence[DailyBar], spy: Sequence[DailyBar], screen: str,
    etfs: set[str],
) -> BriefRow:
    last = bars[-1].close
    hl = ind.high_low(bars)
    return BriefRow(
        symbol=symbol, screen=screen, last=str(last),
        chg_1d_pct=_q(ind.pct_change(bars, 1)), atr_pct=_q(ind.atr_pct(bars)),
        vs_sma20_pct=_q(ind.pct_from(last, ind.sma(bars, 20))),
        vs_sma50_pct=_q(ind.pct_from(last, ind.sma(bars, 50))),
        vs_sma200_pct=_q(ind.pct_from(last, ind.sma(bars, 200))),
        rs_3m=_q(ind.rel_strength(bars, spy, 63)), rs_6m=_q(ind.rel_strength(bars, spy, 126)),
        from_52w_high_pct=_q(ind.pct_from(last, hl[0])) if hl else None,
        from_52w_low_pct=_q(ind.pct_from(last, hl[1])) if hl else None,
        vol_ratio=_q(ind.volume_ratio(bars)), gap_pct=_q(ind.gap_pct(bars)),
        option_band=symbol in etfs or last <= OPTION_BAND_MAX,
    )


def technical_screen(
    series: Mapping[str, Sequence[DailyBar]], spy: Sequence[DailyBar], skip: set[str]
) -> list[tuple[str, str]]:
    """`skip` is every symbol that is not a single name: the ETF list, SPY and
    the macro context rows. Relative-strength extremes are only picked from a
    universe big enough for "top decile" to mean something."""
    picks: dict[str, str] = {}
    rs: list[tuple[Decimal, str]] = []
    for sym, bars in series.items():
        if sym in skip or len(bars) < MIN_HISTORY:
            continue
        last = bars[-1].close
        r6 = ind.rel_strength(bars, spy, 126)
        if r6 is not None:
            rs.append((r6, sym))
        prior, vr = ind.prior_high_low(bars), ind.volume_ratio(bars)
        if prior is not None and vr is not None and vr >= Decimal("1.5"):
            if last > prior[0]:
                picks[sym] = "breakout"
                continue
            if last < prior[1]:
                picks[sym] = "breakdown"
                continue
        if vr is not None and vr >= Decimal("2.5"):
            picks[sym] = "volume_spike"
            continue
        s20, s50, s200 = ind.sma(bars, 20), ind.sma(bars, 50), ind.sma(bars, 200)
        ch5 = ind.pct_change(bars, 5)
        if (
            s20 and s50 and s200 and ch5 is not None and last > s200 and s50 > s200 and ch5 < 0
            and (
                abs(last - s20) / s20 <= Decimal("0.02")
                or abs(last - s50) / s50 <= Decimal("0.02")
            )
        ):
            picks[sym] = "pullback"
    if len(rs) >= RS_MIN_UNIVERSE:
        rs.sort()
        n = min(RS_PICKS, len(rs) // 10)
        for _, sym in reversed(rs[-n:]):
            picks.setdefault(sym, "rs_top")
        for _, sym in rs[:n]:
            picks.setdefault(sym, "rs_bottom")
    ordered = sorted(picks.items(), key=lambda kv: (SCREEN_ORDER.index(kv[1]), kv[0]))
    return ordered[:MAX_TECH_ROWS]


def earnings_screen(
    series: Mapping[str, Sequence[DailyBar]], skip: set[str]
) -> list[tuple[str, str]]:
    found: list[tuple[Decimal, str, str]] = []
    for sym, bars in series.items():
        if sym in skip or len(bars) < 25:
            continue
        best: tuple[Decimal, int] | None = None
        for back in range(3):
            window = bars[: len(bars) - back]
            g, vr = ind.gap_pct(window), ind.volume_ratio(window)
            if g is not None and vr is not None and abs(g) >= 3 and vr >= 2:
                if best is None or abs(g) > abs(best[0]):
                    best = (g, back)
        if best is not None:
            found.append((abs(best[0]), sym, f"earnings_gap_{best[1]}d_ago"))
    found.sort(key=lambda t: (-t[0], t[1]))
    return [(sym, screen) for _, sym, screen in found[:MAX_EARN_ROWS]]


def gap_screen(series: Mapping[str, Sequence[DailyBar]], skip: set[str]) -> list[tuple[str, str]]:
    found: list[tuple[Decimal, str]] = []
    for sym, bars in series.items():
        if sym in skip:
            continue
        g = ind.gap_pct(bars)
        if g is not None and abs(g) >= 2:
            found.append((abs(g), sym))
    found.sort(key=lambda t: (-t[0], t[1]))
    return [(sym, "gap") for _, sym in found[:MAX_GAP_ROWS]]


async def build_briefing(
    store: Store, broker: Broker, analyst: Analyst, desk: DeskConfig
) -> Briefing:
    counts = await store.bar_counts()
    series = {s: await store.bars_for(s, limit=HISTORY) for s in counts}
    spy = series.get("SPY", [])
    etfs = set(desk.etf_list)
    skip = etfs | {"SPY"} | set(desk.context_symbols)
    notes: list[str] = []
    if not spy:
        notes.append("no SPY bars yet: relative strength is unknown until bars_refresh has run")
    rows: list[BriefRow] = []
    context: list[BriefRow] = []
    movers: list[MoverRow] = []
    movers_error: str | None = None
    if analyst == "technical":
        rows = [
            row_for(s, series[s], spy, scr, etfs) for s, scr in technical_screen(series, spy, skip)
        ]
    elif analyst == "earnings":
        rows = [
            row_for(s, series[s], spy, scr, etfs) for s, scr in earnings_screen(series, skip)
        ]
    elif analyst == "news":
        rows = [row_for(s, series[s], spy, scr, etfs) for s, scr in gap_screen(series, skip)]
        try:
            for direction in ("up", "down"):
                got = await broker.movers("EQUITY_ALL", direction)
                movers.extend(
                    MoverRow(symbol=m.symbol, direction=direction, last=str(m.last),
                             net_percent_change=str(m.net_percent_change.quantize(CENT)),
                             volume=m.volume)
                    for m in got[:MOVERS_PER_SIDE]
                )
        except BrokerUnauthorized:
            movers_error = "broker blind: token absent/dead"
        except (BrokerError, OSError, ValueError) as e:
            movers_error = f"movers read failed: {type(e).__name__}"
    else:
        rows = [
            row_for(s, series[s], spy, "etf", etfs)
            for s in desk.etf_list
            if len(series.get(s, [])) >= 2
        ]
        rows.sort(
            key=lambda r: (
                Decimal(r.rs_3m) if r.rs_3m is not None else Decimal("-1e9")
            ),
            reverse=True,
        )
        context = [
            row_for(s, series[s], spy, "context", etfs)
            for s in ("SPY", *desk.context_symbols)
            if len(series.get(s, [])) >= 2
        ]
    own = [
        OpenPitchRow(id=p.id, symbol=p.symbol, direction=p.direction, session=p.session.isoformat(),
                     target=str(p.target), invalidation=str(p.invalidation),
                     horizon_days=p.horizon_days, conviction=p.conviction)
        for p in await open_pitches(store) if p.analyst == analyst
    ]
    return Briefing(
        analyst=analyst, asof=spy[-1].date.isoformat() if spy else None, rows=rows,
        context=context, movers=movers, movers_error=movers_error,
        record=await analyst_record(store, analyst), open_pitches=own, notes=notes,
    )
