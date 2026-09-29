"""Task 3: bar math. Pure functions, so most of this is properties."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from desk_fixtures import bar, flat_bars, sessions, trend_bars
from hypothesis import given
from hypothesis import strategies as st

from tc.broker.models import DailyBar
from tc.desk import indicators as ind

D0 = date(2026, 1, 5)


def test_atr_of_a_flat_series_is_zero_and_short_history_is_none() -> None:
    assert ind.atr_pct(flat_bars(D0, 20)) == 0
    assert ind.atr_pct(flat_bars(D0, 14)) is None


def test_atr_uses_the_true_range_across_a_gap() -> None:
    days = sessions(D0, 15)
    bars = [bar(d, 100, 101, 99, 100) for d in days[:14]] + [bar(days[14], 110, 111, 109, 110)]
    # 13 ranges of 2, then one true range of 111-100=11, over 14 sessions, / 110.
    expected = (Decimal(2) * 13 + 11) / 14 / 110 * 100
    assert ind.atr_pct(bars) == expected


def test_sma_pct_change_and_pct_from() -> None:
    bars = trend_bars(D0, 30, first="50", step="1")
    assert ind.sma(bars, 3) == (bars[-1].close + bars[-2].close + bars[-3].close) / 3
    assert ind.pct_change(bars, 1) == (bars[-1].close - bars[-2].close) / bars[-2].close * 100
    assert ind.pct_from(Decimal(110), Decimal(100)) == 10
    assert ind.pct_from(Decimal(110), None) is None


def test_relative_strength_of_a_series_against_itself_is_zero() -> None:
    bars = trend_bars(D0, 70)
    assert ind.rel_strength(bars, bars, 63) == 0


def test_relative_strength_needs_the_benchmark_on_the_same_dates() -> None:
    bars = trend_bars(D0, 70)
    assert ind.rel_strength(bars, bars[:-1], 63) is None


def test_high_low_and_prior_high_low_exclude_today_for_breakouts() -> None:
    days = sessions(D0, 3)
    bars = [bar(days[0], 10, 12, 9, 11), bar(days[1], 11, 13, 10, 12), bar(days[2], 12, 20, 11, 19)]
    assert ind.high_low(bars) == (Decimal(20), Decimal(9))
    assert ind.prior_high_low(bars) == (Decimal(13), Decimal(9))


def test_volume_ratio_and_gap() -> None:
    days = sessions(D0, 21)
    bars = [bar(d, 100, 101, 99, 100, v=1000) for d in days[:20]] + [
        bar(days[20], 104, 105, 103, 104, v=3000)
    ]
    assert ind.volume_ratio(bars) == 3
    assert ind.gap_pct(bars) == 4
    assert ind.volume_ratio([bar(d, 1, 1, 1, 1, v=0) for d in days]) is None


def test_correlation_of_a_series_with_itself_is_one_and_with_its_inverse_minus_one() -> None:
    a = [bar(d, 1, 1, 1, Decimal(100) + (i % 7) * 3) for i, d in enumerate(sessions(D0, 70))]
    inverse = [DailyBar(date=b.date, open=b.open, high=b.high, low=b.low,
                        close=Decimal(1) / b.close) for b in a]
    one = ind.log_return_corr(a, a)
    minus_one = ind.log_return_corr(a, inverse)
    assert one is not None and minus_one is not None
    assert abs(one - 1) < Decimal("1e-20")
    assert abs(minus_one + 1) < Decimal("1e-20")
    assert ind.log_return_corr(a[:30], a[:30]) is None


@given(st.lists(st.integers(min_value=1, max_value=10_000), min_size=61, max_size=80),
       st.lists(st.integers(min_value=1, max_value=10_000), min_size=61, max_size=80))
def test_correlation_is_always_within_minus_one_and_one(xs: list[int], ys: list[int]) -> None:
    n = min(len(xs), len(ys))
    days = sessions(D0, n)
    a = [bar(d, 1, 1, 1, x) for d, x in zip(days, xs, strict=False)]
    b = [bar(d, 1, 1, 1, y) for d, y in zip(days, ys, strict=False)]
    r = ind.log_return_corr(a, b)
    assert r is None or Decimal(-1) - Decimal("1e-20") <= r <= Decimal(1) + Decimal("1e-20")
