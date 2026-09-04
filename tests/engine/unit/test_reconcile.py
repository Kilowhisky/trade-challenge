"""CLAUDE.md §4.5: session-open reconciliation. A stop either matches its
position or it does not — this is the one typed view every later loop reads."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tc.broker.fake import FakeBroker
from tc.loops.reconcile import reconcile
from tc.store.db import Store

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_view_matches_stops_to_positions(store: Store) -> None:
    v = await reconcile(FakeBroker(FIX, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert set(v.resting_stops) == {p.symbol for p in v.account.positions}
    assert v.naked == [] and v.partial == [] and v.orphaned_stops == []
    assert v.restricted is False
    assert await store.latest_positions() == {p.symbol: p.quantity for p in v.account.positions}


async def test_partial_and_naked(store: Store, tmp_path: Path) -> None:
    import shutil

    d = tmp_path / "fx"
    shutil.copytree(FIX, d)
    (d / "orders.json").write_text((FIX / "orders-partial.json").read_text())
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert ("AMH", 29, 20) in v.partial
    (d / "orders.json").write_text("[]")
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert sorted(v.naked) == sorted(p.symbol for p in v.account.positions)


async def test_orphaned_stop_without_position(store: Store, tmp_path: Path) -> None:
    import json
    import shutil

    d = tmp_path / "fx"
    shutil.copytree(FIX, d)
    acct = json.loads((d / "account.json").read_text())
    acct["securitiesAccount"]["positions"] = []
    (d / "account.json").write_text(json.dumps(acct))
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert len(v.orphaned_stops) == len(v.resting_stops) and v.naked == []


async def test_two_resting_stops_on_one_symbol_are_both_kept(
    store: Store, tmp_path: Path
) -> None:
    """Last-in-wins on a `{symbol: order}` dict silently dropped the extra
    stop: 29 shares carrying two GTC sell stops looked exactly like 29 shares
    carrying one, and whichever filled second would sell shares the account no
    longer owns — §1.5's accidental short, arriving through a protective
    order.

    `resting_stops` keeps the EARLIEST (the one the position was actually
    entered with, and the one watch 5 compares the fill against); the extras
    are named in `duplicate_stops`.
    """
    import shutil

    d = tmp_path / "fx"
    shutil.copytree(FIX, d)
    (d / "orders.json").write_text((FIX / "orders-duplicate-stop.json").read_text())
    v = await reconcile(FakeBroker(d, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))

    assert v.resting_stops["AMH"].order_id == 1000000000001
    assert [o.order_id for o in v.duplicate_stops["AMH"]] == [1000000000001, 1000000000003]
    assert v.resting_stop_count == 2  # not 1: the ledger must not look tidy
    assert v.naked == [] and v.partial == [] and v.orphaned_stops == []


async def test_a_single_stop_leaves_duplicate_stops_empty(store: Store) -> None:
    v = await reconcile(FakeBroker(FIX, NOW), store, "HASH_REDACTED", NOW, date(2026, 8, 14))
    assert v.duplicate_stops == {}
    assert v.resting_stop_count == len(v.resting_stops)
