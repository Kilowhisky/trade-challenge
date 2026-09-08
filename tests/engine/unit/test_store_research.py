from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tc.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_evidence_round_trips_in_order(store: Store) -> None:
    for i, claim in enumerate(["first", "second"]):
        await store.insert_evidence(
            {"symbol": "CSX", "date": "2026-09-07", "claim": claim, "url": f"https://x/{i}",
             "source_type": "end-user", "observed": "2026-09-07", "independence": "unrelated",
             "extra": {}}
        )
    rows = await store.evidence_for("CSX")
    assert [r["claim"] for r in rows] == ["first", "second"]
    assert await store.evidence_for("NOPE") == []


async def test_escalation_last_score_wins(store: Store) -> None:
    await store.insert_escalation(
        {"id": "CSX-2026-09-07-123", "kind": "raise", "symbol": "CSX", "at": "2026-09-07",
         "record": {"claim": "c", "direction": "up", "event_date": "2026-10-10",
                    "source_types": ["end-user", "employee"]}}
    )
    assert await store.escalation_raise_exists("CSX-2026-09-07-123") is True
    for outcome in ("wrong", "right"):
        await store.insert_escalation(
            {"id": "CSX-2026-09-07-123", "kind": "score", "symbol": "CSX",
             "at": "2026-09-08", "record": {"outcome": outcome}}
        )
    rows = await store.escalations()
    assert len(rows) == 1                      # one prediction, one entry
    assert rows[0]["latest_outcome"] == "right"


async def test_sectors_upsert_counts_new_and_retired(store: Store) -> None:
    new, retired = await store.upsert_sectors([("CSX", "airlines-transport", "2026-09-07")])
    assert (new, retired) == (1, 0)
    new, retired = await store.upsert_sectors([("CSX", "other", "2026-09-08")])
    assert (new, retired) == (0, 1)
    assert [(r["symbol"], r["sector"]) for r in await store.sectors()] == [("CSX", "other")]


async def test_universe_replace_keeps_only_the_newest_sweep(store: Store) -> None:
    row = {"symbol": "MPC", "price": Decimal("368.83"), "adv10": Decimal("2349452"),
           "dollar_vol": Decimal("866548381"), "pct_from_52wk_high": Decimal("0.08"), "optionable": True,
           "leverage": Decimal("0.0"), "last_earnings": "2026-08-04", "is_etf": False,
           "session_range_pct": Decimal("1.62"), "description": "Marathon Petroleum Corp",
           "qualified": True}
    await store.replace_universe(date(2026, 8, 29), [row])
    await store.replace_universe(date(2026, 9, 5), [{**row, "symbol": "CSX"}])
    assert [r["symbol"] for r in await store.universe_rows()] == ["CSX"]
    assert await store.universe_asof() == date(2026, 9, 5)


async def test_oi_idempotency_is_per_symbol_per_day(store: Store) -> None:
    await store.append_ledger("oi", date(2026, 9, 7), "CSX", {"symbol": "CSX", "t": "16:26:00"})
    assert await store.oi_symbol_seen(date(2026, 9, 7), "CSX") is True
    assert await store.oi_symbol_seen(date(2026, 9, 8), "CSX") is False


async def test_ledger_latest_before_finds_the_prior_file(store: Store) -> None:
    for d, iv in ((date(2026, 9, 3), "0.248"), (date(2026, 9, 7), "0.208")):
        await store.append_ledger("iv", d, "CSX", {"symbol": "CSX", "t": "16:26:00", "atm_iv": iv})
    prior = await store.ledger_rows("iv", latest_before=date(2026, 9, 7))
    assert [r["record"]["atm_iv"] for r in prior] == ["0.248"]
