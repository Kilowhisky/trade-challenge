"""The scorecard (spec §7.3) and the pre-registered checkpoint (§8).

Everything is computed on read from `resolutions` and the paper book; there
is no scorecard table to drift from the ledger it summarises."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.config import DeskConfig
from tc.desk.models import ANALYSTS
from tc.desk.paper import book_row, book_state, closed_trades
from tc.desk.scoring import Resolution, resolved_rows
from tc.rules.model import Rules
from tc.store.db import Store

CENT = Decimal("0.01")
Verdict = Literal["not_yet", "keep", "stop", "rework"]


class GroupStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    n: int
    hit_rate_pct: str | None
    mean_ret_pct: str | None
    mean_excess_spy_pct: str | None
    mean_excess_bench_pct: str | None
    ci95_excess_spy: list[str] | None


class BookStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str | None
    start_equity: str | None
    equity: str | None
    ret_pct: str | None
    spy_ret_pct: str | None
    closed_trades: int
    mean_trade_ret_pct: str | None
    real_account_value: str | None


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    min_calls: int
    resolved_calls: int
    verdict: Verdict
    reason: str


class Scorecard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asof: str
    analysts: list[GroupStats]
    all_pitches: GroupStats
    pm_calls: GroupStats
    selection_edge_pct: str | None
    book: BookStats
    checkpoint: Checkpoint
    analyst_flags: list[str]


def _q(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(CENT))


def mean(xs: Sequence[Decimal]) -> Decimal | None:
    return None if not xs else sum(xs, Decimal(0)) / len(xs)


def bootstrap_ci(
    xs: Sequence[Decimal], resamples: int = 2000, seed: int = 20260927
) -> tuple[Decimal, Decimal]:
    """Percentile bootstrap of the mean, seeded so the same ledger always
    reports the same interval."""
    rng = random.Random(seed)  # noqa: S311 -- statistics, not security
    n = len(xs)
    means = sorted(
        sum((xs[rng.randrange(n)] for _ in range(n)), Decimal(0)) / n for _ in range(resamples)
    )
    return means[int(resamples * 0.025)], means[int(resamples * 0.975) - 1]


def group_stats(name: str, rs: Sequence[Resolution], ci_min: int) -> GroupStats:
    if not rs:
        return GroupStats(name=name, n=0, hit_rate_pct=None, mean_ret_pct=None,
                          mean_excess_spy_pct=None, mean_excess_bench_pct=None,
                          ci95_excess_spy=None)
    ex = [r.excess_spy for r in rs]
    ci = None
    if len(rs) >= ci_min:
        lo, hi = bootstrap_ci(ex)
        ci = [str(lo.quantize(CENT)), str(hi.quantize(CENT))]
    return GroupStats(
        name=name, n=len(rs),
        hit_rate_pct=_q(Decimal(sum(1 for r in rs if r.hit)) / len(rs) * 100),
        mean_ret_pct=_q(mean([r.ret_pct for r in rs])),
        mean_excess_spy_pct=_q(mean(ex)),
        mean_excess_bench_pct=_q(mean([r.excess_bench for r in rs])),
        ci95_excess_spy=ci,
    )


def checkpoint_verdict(
    *, today: date, checkpoint_date: date, min_calls: int, resolved_calls: int,
    pm_mean_excess: Decimal | None, all_mean_excess: Decimal | None,
    book_ret: Decimal | None, spy_ret: Decimal | None,
) -> Checkpoint:
    def cp(v: Verdict, reason: str) -> Checkpoint:
        return Checkpoint(date=checkpoint_date.isoformat(), min_calls=min_calls,
                          resolved_calls=resolved_calls, verdict=v, reason=reason)

    if today < checkpoint_date or resolved_calls < min_calls:
        return cp("not_yet", f"{resolved_calls}/{min_calls} PM calls resolved;"
                             f" checkpoint on or after {checkpoint_date.isoformat()}")
    if pm_mean_excess is None:
        return cp("rework", "no PM excess to judge")
    if (
        pm_mean_excess > 0 and book_ret is not None and spy_ret is not None
        and book_ret >= spy_ret
    ):
        return cp("keep", "PM calls beat SPY and the paper book at least matched it")
    if (
        pm_mean_excess < 0 and all_mean_excess is not None
        and pm_mean_excess < all_mean_excess
    ):
        return cp("stop", "PM calls trail SPY and trail pitches: selection subtracts value")
    return cp("rework", "mixed: see the groups")


async def build_scorecard(store: Store, rules: Rules, desk: DeskConfig, today: date) -> Scorecard:
    ci_min = int(rules.get("strategy", "scorecard_ci_min_n"))
    rows = await resolved_rows(store)
    pitches = [r.resolution for r in rows if r.resolution.kind == "pitch"]
    calls = [r.resolution for r in rows
             if r.resolution.kind == "call" and r.origin in ("pitch", "pm")]
    analysts = [
        group_stats(a, [r.resolution for r in rows if r.analyst == a], ci_min) for a in ANALYSTS
    ]
    analysts = [g for g in analysts if g.n > 0]
    pm_mean, all_mean = mean([r.excess_spy for r in calls]), mean([r.excess_spy for r in pitches])
    edge = None if pm_mean is None or all_mean is None else pm_mean - all_mean
    review_min = int(rules.get("strategy", "analyst_review_min_pitches"))
    flags = [
        f"{g.name}: {g.n} resolved, mean excess vs benchmark {g.mean_excess_bench_pct}% —"
        " drop or rebuild at the checkpoint"
        for g in analysts
        if g.n >= review_min and g.mean_excess_bench_pct is not None
        and Decimal(g.mean_excess_bench_pct) < 0
    ]
    book = await _book_stats(store)
    return Scorecard(
        asof=today.isoformat(), analysts=analysts,
        all_pitches=group_stats("all pitches", pitches, ci_min),
        pm_calls=group_stats("PM calls", calls, ci_min),
        selection_edge_pct=_q(edge), book=book,
        checkpoint=checkpoint_verdict(
            today=today, checkpoint_date=desk.checkpoint_date,
            min_calls=int(rules.get("strategy", "checkpoint_min_pm_calls")),
            resolved_calls=len(calls), pm_mean_excess=pm_mean, all_mean_excess=all_mean,
            book_ret=None if book.ret_pct is None else Decimal(book.ret_pct),
            spy_ret=None if book.spy_ret_pct is None else Decimal(book.spy_ret_pct),
        ),
        analyst_flags=flags,
    )


async def _book_stats(store: Store) -> BookStats:
    acct = await store.latest_account()
    real = None if acct is None else str(acct.liquidation_value)
    row = await book_row(store)
    if row is None:
        return BookStats(start_date=None, start_equity=None, equity=None, ret_pct=None,
                         spy_ret_pct=None, closed_trades=0, mean_trade_ret_pct=None,
                         real_account_value=real)
    start_date, start_equity = row
    state = await book_state(store)
    spy = await store.bars_for("SPY", since=start_date)
    spy_ret = None if len(spy) < 2 else (spy[-1].close - spy[0].close) / spy[0].close * 100
    trades = await closed_trades(store)
    return BookStats(
        start_date=start_date.isoformat(), start_equity=str(start_equity),
        equity=_q(state.equity), ret_pct=_q((state.equity - start_equity) / start_equity * 100),
        spy_ret_pct=_q(spy_ret), closed_trades=len(trades),
        mean_trade_ret_pct=_q(mean([t.ret_pct for t in trades])), real_account_value=real,
    )


def _line(g: GroupStats) -> str:
    if g.n == 0:
        return f"{g.name}: none resolved yet"
    if g.ci95_excess_spy is None:
        ci = ""
    else:
        ci = f" [95% {g.ci95_excess_spy[0]}..{g.ci95_excess_spy[1]}]"
    return (f"{g.name}: n={g.n} hit {g.hit_rate_pct}% | ret {g.mean_ret_pct}% | vs SPY"
            f" {g.mean_excess_spy_pct}% | vs sector {g.mean_excess_bench_pct}%{ci}")


def render_scorecard(sc: Scorecard) -> str:
    lines = [f"📊 DESK SCORECARD {sc.asof}", _line(sc.pm_calls), _line(sc.all_pitches)]
    if sc.selection_edge_pct is not None:
        lines.append(f"PM selection edge: {sc.selection_edge_pct} pts")
    lines += [_line(g) for g in sc.analysts]
    b = sc.book
    if b.start_date is not None:
        lines.append(f"Paper book: {b.equity} ({b.ret_pct}%) vs SPY {b.spy_ret_pct}% since"
                     f" {b.start_date} | {b.closed_trades} closed, mean {b.mean_trade_ret_pct}%")
    if b.real_account_value is not None:
        lines.append(f"Real account: {b.real_account_value}")
    c = sc.checkpoint
    lines.append(f"Checkpoint {c.date} ({c.min_calls} calls): {c.verdict} — {c.reason}")
    lines += [f"⚠️ {f}" for f in sc.analyst_flags]
    return "\n".join(lines)
