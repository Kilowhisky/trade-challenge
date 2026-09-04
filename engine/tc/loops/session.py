"""Session close (CLAUDE.md §7.2) and the one irreversible number: the
high-water mark ratchets only here, only up, only from a CLOSE
(session-close.md §3). An intraday high is recorded, never adopted."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tc.rules import arith
from tc.rules.model import Rules
from tc.store.db import SessionStatusRow, Store


async def seed_hwm(
    store: Store, recorded: Decimal, recorded_on: date, reserve: Decimal
) -> SessionStatusRow:
    if await store.latest_session_status() is not None:
        raise ValueError("session_status already seeded; refusing to overwrite the mark")
    hwm = arith.legacy_hwm_to_account_basis(recorded, recorded_on, reserve)
    row = SessionStatusRow(date=recorded_on, close_value=hwm, hwm=hwm, halt=Decimal("0"),
                           drawdown_pct=Decimal("0"), level="OK", prior_hwm=hwm,
                           ratcheted=False, intraday_high=None)
    await store.write_session_status(row)
    return row


async def close_session(
    store: Store, rules: Rules, d: date, close_value: Decimal
) -> SessionStatusRow:
    prior = await store.latest_session_status()
    if prior is None:
        raise ValueError("no high-water mark on record: run `tc seed-hwm` first")
    hwm = arith.ratchet_hwm(prior.hwm, close_value)
    ticks = await store.ticks_for(d.isoformat())
    highs = [t.account_value for t in ticks if t.state == "RTH"]
    intraday = max(highs) if highs and max(highs) > hwm else None
    row = SessionStatusRow(
        date=d, close_value=close_value, hwm=hwm, halt=arith.halt_threshold(hwm, rules),
        drawdown_pct=arith.drawdown_pct(close_value, hwm),
        level="HALT" if arith.is_halted(close_value, hwm, rules) else "OK",
        prior_hwm=prior.hwm, ratcheted=hwm > prior.hwm, intraday_high=intraday,
    )
    await store.write_session_status(row)
    return row
