"""Shadow diff — the Phase 0 exit criterion (task-11-brief.md).

Compares one engine day (SQLite store) against the legacy ledgers it
replaces: the `status/YYYY-MM-DD.md` "State recorded — current" block
(parsed the same way as `scripts/latest-status.sh`'s awk — same heading,
same money regex, same "first match inside the block" rule) and
`status/ticks/YYYY-MM-DD.tsv` (the 14-column ledger from `tick.md` §D).

This module is read-only: it never calls the broker and never writes to the
store. Its only output is a `ShadowDiff` the CLI renders as lines plus an
OK/DIFF verdict.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tc.store.db import Store

# §7.2 rule: `$1,234.56`-shaped money tokens only -- exactly two decimal digits.
MONEY_RE = re.compile(r"\$[0-9][0-9,]*\.[0-9]{2}")
# "  - **AMH** 29 sh @ $34.79 avg (...) — stop 32.01/30.40 GTC WORKING ..."
POSITION_RE = re.compile(r"-\s*\*\*([A-Z][A-Z.]*)\*\*\s+(\d+)\s+sh\b")
STOP_RE = re.compile(r"\bstop\s+([0-9]+(?:\.[0-9]+)?)/([0-9]+(?:\.[0-9]+)?)\b")

# tick.md §D: time_et state comp_capital hwm dd_pct level positions stops
# orders settled unsettled reserve flags note
TICK_COLUMNS = (
    "time_et",
    "state",
    "comp_capital",
    "hwm",
    "dd_pct",
    "level",
    "positions",
    "stops",
    "orders",
    "settled",
    "unsettled",
    "reserve",
    "flags",
    "note",
)

VALUE_TOLERANCE = Decimal("0.01")


def _money(token: str) -> Decimal:
    return Decimal(token.lstrip("$").replace(",", ""))


class LegacyStatus(BaseModel):
    """The "State recorded — current" block of a legacy `status/*.md` file."""

    model_config = ConfigDict(extra="forbid")
    hwm: Decimal
    account_value: Decimal
    positions: dict[str, int]
    stops: dict[str, tuple[Decimal, Decimal]]  # symbol -> (trigger, limit)


class LegacyTick(BaseModel):
    """One row of a legacy `status/ticks/*.tsv` ledger."""

    model_config = ConfigDict(extra="forbid")
    time_et: str
    state: str
    comp_capital: Decimal
    hwm: Decimal
    dd_pct: Decimal
    level: str
    positions: int
    stops: int
    orders: int
    settled: Decimal
    unsettled: Decimal
    reserve: Decimal
    flags: str
    note: str


class ShadowDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hwm_match: bool
    missing_engine_session: bool
    stop_map_match: bool
    value_diffs: list[tuple[str, Decimal, Decimal]]  # (HH:MM, engine value, legacy comp+reserve)
    state_diffs: list[tuple[str, str, str]]  # (HH:MM, engine state, legacy state)
    missing_engine_ticks: list[str]  # HH:MM present in legacy, absent from engine
    missing_legacy_ticks: list[str]  # HH:MM present in engine, absent from legacy
    ok: bool


def _current_block(text: str) -> list[str]:
    """Port of `scripts/latest-status.sh`'s awk: from the exact heading
    `### State recorded — current` to the next line starting with `#`. A
    file may carry an earlier, superseded block with a near-identical
    heading (`### State recorded — superseded`); that heading does not match
    and its lines are never collected."""
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.startswith("### State recorded — current"):
            in_block = True
            continue
        if in_block and line.startswith("#"):
            break
        if in_block:
            out.append(line)
    return out


def parse_legacy_status(path: Path) -> LegacyStatus:
    block = _current_block(Path(path).read_text())
    hwm: Decimal | None = None
    account_value: Decimal | None = None
    positions: dict[str, int] = {}
    stops: dict[str, tuple[Decimal, Decimal]] = {}
    for line in block:
        if hwm is None and "High-water mark:" in line:
            m = MONEY_RE.search(line)
            if m:
                hwm = _money(m.group(0))
        if account_value is None and "Account value:" in line:
            m = MONEY_RE.search(line)
            if m:
                account_value = _money(m.group(0))
        pm = POSITION_RE.search(line)
        if pm:
            symbol, qty = pm.group(1), int(pm.group(2))
            positions[symbol] = qty
            sm = STOP_RE.search(line)
            if sm:
                stops[symbol] = (Decimal(sm.group(1)), Decimal(sm.group(2)))
    if hwm is None:
        raise ValueError(
            f"{path}: no High-water mark line in the 'State recorded — current' block"
        )
    if account_value is None:
        raise ValueError(f"{path}: no Account value line in the 'State recorded — current' block")
    return LegacyStatus(hwm=hwm, account_value=account_value, positions=positions, stops=stops)


def parse_legacy_ticks(path: Path) -> list[LegacyTick]:
    lines = [line for line in Path(path).read_text().splitlines() if line.strip()]
    if not lines:
        return []
    rows: list[LegacyTick] = []
    for line in lines[1:]:  # first line is the header
        cols = line.split("\t")
        if len(cols) != len(TICK_COLUMNS):
            raise ValueError(
                f"{path}: expected {len(TICK_COLUMNS)} tab-separated columns, "
                f"got {len(cols)}: {line!r}"
            )
        fields = dict(zip(TICK_COLUMNS, cols, strict=True))
        rows.append(
            LegacyTick(
                time_et=fields["time_et"],
                state=fields["state"],
                comp_capital=Decimal(fields["comp_capital"]),
                hwm=Decimal(fields["hwm"]),
                dd_pct=Decimal(fields["dd_pct"]),
                level=fields["level"],
                positions=int(fields["positions"]),
                stops=int(fields["stops"]),
                orders=int(fields["orders"]),
                settled=Decimal(fields["settled"]),
                unsettled=Decimal(fields["unsettled"]),
                reserve=Decimal(fields["reserve"]),
                flags=fields["flags"],
                note=fields["note"],
            )
        )
    return rows


async def diff_day(
    store: Store,
    d: date,
    status_path: Path,
    ticks_path: Path,
    reserve: Decimal,
) -> ShadowDiff:
    status = parse_legacy_status(status_path)
    legacy_ticks = parse_legacy_ticks(ticks_path)

    session = await store.session_status_for(d)
    missing_engine_session = session is None
    hwm_match = session is not None and session.hwm == status.hwm

    engine_ticks = await store.ticks_for(d.isoformat())
    engine_by_time = {t.at_et[11:16]: t for t in engine_ticks}
    legacy_by_time = {t.time_et: t for t in legacy_ticks}

    value_diffs: list[tuple[str, Decimal, Decimal]] = []
    state_diffs: list[tuple[str, str, str]] = []
    for hhmm, legacy_tick in legacy_by_time.items():
        engine_tick = engine_by_time.get(hhmm)
        if engine_tick is None:
            continue
        legacy_value = legacy_tick.comp_capital + reserve
        if abs(engine_tick.account_value - legacy_value) > VALUE_TOLERANCE:
            value_diffs.append((hhmm, engine_tick.account_value, legacy_value))
        if engine_tick.state != legacy_tick.state:
            state_diffs.append((hhmm, engine_tick.state, legacy_tick.state))

    missing_engine_ticks = sorted(set(legacy_by_time) - set(engine_by_time))
    missing_legacy_ticks = sorted(set(engine_by_time) - set(legacy_by_time))

    engine_stops = await store.resting_stops_on(d)
    stop_map_match = engine_stops == status.stops

    ok = (
        hwm_match
        and stop_map_match
        and not value_diffs
        and not state_diffs
        and not missing_engine_ticks
        and not missing_legacy_ticks
    )
    return ShadowDiff(
        hwm_match=hwm_match,
        missing_engine_session=missing_engine_session,
        stop_map_match=stop_map_match,
        value_diffs=value_diffs,
        state_diffs=state_diffs,
        missing_engine_ticks=missing_engine_ticks,
        missing_legacy_ticks=missing_legacy_ticks,
        ok=ok,
    )
