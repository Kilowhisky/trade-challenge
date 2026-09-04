"""CLAUDE.md §7.2 session close and the §3.6 high-water mark: the ratchet is
one-directional, seeded exactly once from a legacy basis-converted mark, and
never adopts an intraday high into the recorded mark."""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal as D  # noqa: N817 — brevity in a Decimal-heavy fixture table
from pathlib import Path

import pytest

from tc.loops.session import close_session, seed_hwm
from tc.rules.model import Rules
from tc.store.db import Store, TickRow

RULES = Rules.load(Path(__file__).resolve().parents[3] / "rules.yml")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_seed_converts_pre_amendment_basis(store: Store) -> None:
    r = await seed_hwm(store, D("2900.00"), date(2026, 8, 25), D("900.00"))
    assert r.hwm == D("3800.00") and r.prior_hwm == D("3800.00") and r.ratcheted is False


async def test_seed_refuses_twice(store: Store) -> None:
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    with pytest.raises(ValueError):
        await seed_hwm(store, D("1.00"), date(2026, 9, 3), D("900.00"))


async def test_close_ratchets_up_only(store: Store) -> None:
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    r = await close_session(store, RULES, date(2026, 9, 4), D("3728.71"))
    assert r.hwm == D("3800.00") and r.ratcheted is False and r.level == "OK"
    assert r.halt == D("3040.00") and r.drawdown_pct == D("-1.88")
    r2 = await close_session(store, RULES, date(2026, 9, 8), D("3900.00"))
    assert r2.hwm == D("3900.00") and r2.ratcheted is True and r2.prior_hwm == D("3800.00")


async def test_close_records_intraday_high_above_mark_without_ratcheting(store: Store) -> None:
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    row = TickRow(
        at_et="2026-09-04 11:32", state="RTH", account_value=D("3850.00"), comp_capital=D("2950.00"),
        hwm=D("3800.00"), drawdown_pct=D("1.32"), level="OK", positions=3, stops=3, orders=0,
        settled=D("1"), unsettled=D("0"), reserve=D("1"), flags="-", note="-",
    )
    await store.append_tick(row)
    r = await close_session(store, RULES, date(2026, 9, 4), D("3728.71"))
    assert r.hwm == D("3800.00") and r.intraday_high == D("3850.00")


async def test_close_without_seed_refuses(store: Store) -> None:
    with pytest.raises(ValueError):
        await close_session(store, RULES, date(2026, 9, 4), D("1"))


async def test_halt_level(store: Store) -> None:
    await seed_hwm(store, D("3800.00"), date(2026, 9, 3), D("900.00"))
    r = await close_session(store, RULES, date(2026, 9, 4), D("3040.00"))
    assert r.level == "HALT"
