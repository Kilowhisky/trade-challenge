from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal as D  # noqa: N817 -- brevity in a Decimal-heavy fixture table
from pathlib import Path
from typing import Any

import pytest

from tc.research.cohort import QUARTER_DAYS, cohort
from tc.rules.model import Rules
from tc.store.db import Store

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")
TODAY = date(2026, 9, 7)


def _u(symbol: str, last_earnings: str, qualified: bool = True) -> dict[str, Any]:
    return {"symbol": symbol, "price": D("50"), "adv10": D("1000000"),
            "dollar_vol": D("50000000"), "pct_from_52wk_high": D("1.0"), "optionable": True,
            "leverage": D("0"), "last_earnings": last_earnings, "is_etf": False,
            "session_range_pct": D("1.2"), "description": symbol, "qualified": qualified}


def _le(days_out: int) -> str:
    """A last_earnings date that puts the next print `days_out` from TODAY."""
    return (TODAY + timedelta(days=days_out - QUARTER_DAYS)).isoformat()


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_empty_store_is_an_empty_cohort_not_an_error(store: Store) -> None:
    assert await cohort(store, RULES, TODAY) == []


async def test_window_edges_are_inclusive(store: Store) -> None:
    lo = int(RULES.get("strategy", "scout_entry_window_min_days"))
    hi = int(RULES.get("strategy", "scout_entry_window_max_days"))
    await store.replace_universe(
        date(2026, 9, 5),
        [_u("EDGELO", _le(lo)), _u("EDGEHI", _le(hi)),
         _u("TOOSOON", _le(lo - 1)), _u("TOOFAR", _le(hi + 1))],
    )
    await store.upsert_sectors(
        [(s, "semis-hardware", "2026-09-05") for s in ("EDGELO", "EDGEHI", "TOOSOON", "TOOFAR")]
    )
    rows = await cohort(store, RULES, TODAY)
    assert [r.symbol for r in rows] == ["EDGELO", "EDGEHI"]
    assert rows[0].days_out == lo and rows[1].days_out == hi


async def test_untagged_and_other_are_out_of_scope(store: Store) -> None:
    await store.replace_universe(
        date(2026, 9, 5), [_u("TAGGED", _le(30)), _u("OTHER", _le(30)), _u("UNTAGGED", _le(30))]
    )
    await store.upsert_sectors(
        [("TAGGED", "consumer-software", "2026-09-05"), ("OTHER", "other", "2026-09-05")]
    )
    assert [r.symbol for r in await cohort(store, RULES, TODAY)] == ["TAGGED"]


async def test_unqualified_and_undated_rows_are_skipped_not_guessed(store: Store) -> None:
    await store.replace_universe(
        date(2026, 9, 5),
        [_u("NOEARN", ""), _u("BADDATE", "not-a-date"), _u("UNQUAL", _le(30), qualified=False)],
    )
    await store.upsert_sectors(
        [(s, "semis-hardware", "2026-09-05") for s in ("NOEARN", "BADDATE", "UNQUAL")]
    )
    assert await cohort(store, RULES, TODAY) == []


async def test_sorted_by_days_out_then_symbol(store: Store) -> None:
    await store.replace_universe(
        date(2026, 9, 5), [_u("ZZZZ", _le(25)), _u("AAAA", _le(25)), _u("MMMM", _le(22))]
    )
    await store.upsert_sectors(
        [(s, "airlines-transport", "2026-09-05") for s in ("ZZZZ", "AAAA", "MMMM")]
    )
    assert [r.symbol for r in await cohort(store, RULES, TODAY)] == ["MMMM", "AAAA", "ZZZZ"]


async def test_est_next_is_the_estimated_next_print_not_the_last_one(store: Store) -> None:
    await store.replace_universe(date(2026, 9, 5), [_u("CSX", _le(30))])
    await store.upsert_sectors([("CSX", "airlines-transport", "2026-09-05")])
    row = (await cohort(store, RULES, TODAY))[0]
    assert row.est_next_earnings == TODAY + timedelta(days=30)
