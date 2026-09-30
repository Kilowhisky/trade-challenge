"""Task 12: the scorecard (spec §7.3) and the pre-registered checkpoint (§8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from desk_fixtures import RULES, bar, sessions

from tc.config import DeskConfig
from tc.desk.scorecard import (
    bootstrap_ci,
    build_scorecard,
    checkpoint_verdict,
    group_stats,
    mean,
    render_scorecard,
)
from tc.desk.scoring import How, Resolution
from tc.store.db import Store

D0 = date(2026, 10, 1)


def _r(ret: str, spy: str = "0", bench: str = "0", how: How = "horizon", i: int = 1) -> Resolution:
    return Resolution(kind="call", item_id=i, ref_date=D0, ref_price=Decimal(100),
                      resolved_on=D0, how=how, exit_price=Decimal(100),
                      ret_pct=Decimal(ret), spy_ret_pct=Decimal(spy), bench_ret_pct=Decimal(bench))


def test_mean_and_a_seeded_bootstrap_are_deterministic() -> None:
    xs = [Decimal(x) for x in ("1", "-2", "3", "0.5", "4", "-1")]
    assert mean(xs) == Decimal("5.5") / 6
    assert mean([]) is None
    lo, hi = bootstrap_ci(xs)
    assert (lo, hi) == bootstrap_ci(xs)         # same seed, same interval
    m = mean(xs)
    assert m is not None and lo <= m <= hi


def test_group_stats_hides_the_interval_below_the_minimum() -> None:
    small = group_stats("pm", [_r("2", "1", how="target")], ci_min=20)
    assert (small.n, small.hit_rate_pct, small.mean_excess_spy_pct) == (1, "100.00", "1.00")
    assert small.ci95_excess_spy is None
    big = group_stats("pm", [_r(str(i % 5), "1", i=i) for i in range(25)], ci_min=20)
    assert big.ci95_excess_spy is not None


@pytest.mark.parametrize("today,resolved,pm,all_,book,spy,verdict", [
    (date(2026, 12, 30), 50, "1", "0", "5", "1", "not_yet"),     # before the date
    (date(2027, 1, 4), 39, "1", "0", "5", "1", "not_yet"),       # too few calls
    (date(2027, 1, 4), 40, "0.5", "0", "5", "1", "keep"),
    (date(2027, 1, 4), 40, "-0.5", "0", "5", "1", "stop"),       # PM subtracts value
    (date(2027, 1, 4), 40, "-0.5", "-1", "5", "1", "rework"),    # negative but beats pitches
    (date(2027, 1, 4), 40, "0.5", "0", "0", "1", "rework"),      # calls ok, book lags SPY
])
def test_the_checkpoint_rule_is_the_one_written_down(
    today: date, resolved: int, pm: str, all_: str, book: str, spy: str, verdict: str,
) -> None:
    c = checkpoint_verdict(
        today=today, checkpoint_date=date(2026, 12, 31), min_calls=40, resolved_calls=resolved,
        pm_mean_excess=Decimal(pm), all_mean_excess=Decimal(all_),
        book_ret=Decimal(book), spy_ret=Decimal(spy),
    )
    assert c.verdict == verdict


async def test_build_scorecard_splits_analysts_pm_and_legacy(desk_store: Store) -> None:
    await desk_store.execute(
        "INSERT INTO pitches(analyst, filed_at, session, symbol, direction, thesis,"
        " evidence_json, target, invalidation, horizon_days, conviction, benchmark)"
        " VALUES ('technical','2026-09-30T20:45:00+00:00','2026-10-01','AAA','up','t','[]','110','95',5,3,'XLK')"
    )
    for origin in ("pm", "legacy"):
        await desk_store.execute(
            "INSERT INTO calls(made_at, session, origin, pitch_id, extends_call_id, symbol,"
            " direction, thesis, target, invalidation, horizon_days, conviction, benchmark,"
            " ref_price, spy_ref, bench_ref, funding) VALUES ('2026-10-01T13:55:00+00:00',"
            " '2026-10-01', ?, NULL, NULL, 'AAA', 'up', 't', '110', '95', 5, 3, 'XLK',"
            " '100', '500', '200', 'none')",
            (origin,),
        )
    for kind, item in (("pitch", 1), ("call", 1), ("call", 2)):
        await desk_store.execute(
            "INSERT INTO resolutions(kind, item_id, ref_date, ref_price, resolved_on, how,"
            " exit_price, ret_pct, spy_ret_pct, bench_ret_pct, written_at)"
            " VALUES (?, ?, '2026-10-01', '100', '2026-10-02', 'target', '110', '10', '1', '2', 'x')",
            (kind, item),
        )
    await desk_store.upsert_bars("SPY", [bar(d, 500, 500, 500, 500) for d in sessions(D0, 3)])
    sc = await build_scorecard(desk_store, RULES, DeskConfig(), date(2026, 10, 5))
    assert [(a.name, a.n) for a in sc.analysts] == [("technical", 1)]
    assert sc.pm_calls.n == 1                        # the legacy call is excluded
    assert sc.pm_calls.mean_excess_spy_pct == "9.00"
    assert sc.selection_edge_pct == "0.00"           # PM 9 - pitches 9
    assert sc.book.start_date is None                # paper book not started
    assert sc.checkpoint.verdict == "not_yet"
    text = render_scorecard(sc)
    assert "PM calls" in text and "technical" in text and "Checkpoint" in text
