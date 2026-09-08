"""The validating ledger writers, ported rule for rule from the bash they
replace (0c-writers-contract.md §1.3-§1.8).

Three properties are load-bearing and are why this is not a thin insert:

* **Typed and non-empty, never `has()`.** `has("claim")` is true for an
  explicit null, and v2 was hardened against exactly that after nulls reached
  the evidence ledger. Every required field here is checked for type AND
  emptiness.
* **`mainstream` is recordable evidence that never counts toward the escalation
  bar.** It is the kill-switch record -- the thing you write down when the story
  is already public -- so it must be storable and must not clear a bar.
* **Empty is a correct answer; already-done is not an error.** A second OI
  snapshot for a symbol on a day returns `appended: False` with a reason, and
  the caller skips that underlying and continues. v2 said this with exit 4 and
  a comment reading "Not an error; skip this underlying."

Every refusal is a `LedgerError`, never a bare `ValueError`: the MCP layer maps
that one type onto a tool error, so a validation refusal reaching the caller
carries the reason instead of a stack trace.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from tc.store.db import IN_SCOPE_SECTORS, LEDGER_TABLES, Store

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SOURCE_TYPES = (
    "end-user",
    "employee",
    "counterparty",
    "enthusiast",
    "primary-doc",
    "mainstream",
)
# The escalation bar counts these only. `mainstream` is deliberately absent:
# corroboration by something already on the wire is not an information edge.
BAR_SOURCE_TYPES = tuple(t for t in SOURCE_TYPES if t != "mainstream")
SECTORS = (*IN_SCOPE_SECTORS, "other")
DIRECTIONS = ("up", "down")
Outcome = Literal["right", "wrong", "void"]
OUTCOMES: tuple[Outcome, ...] = ("right", "wrong", "void")
# A raise may not carry its own result: a prediction and its outcome written in
# one call is the single property the escalation ledger exists to make
# impossible.
RESERVED_ON_RAISE = ("outcome", "scored", "kind")

# The §1.7 skip. Returned from two places -- the pre-check and the UNIQUE
# constraint that actually enforces it -- and they must be the same answer.
OI_ALREADY_SNAPSHOTTED: dict[str, Any] = {
    "appended": False,
    "reason": "already snapshotted today",
}

LEDGER_REQUIRED: dict[str, tuple[str, ...]] = {
    "screen": ("symbol", "t", "src"),
    "iv": ("symbol", "t", "atm_iv"),
    "oi": ("symbol", "t"),
    "tombstones": ("symbol", "date", "gate", "reason", "ref_price"),
    "events": ("t",),
}


class LedgerError(ValueError):
    """A refusal with a reason the caller can act on."""


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not value or not SYMBOL_RE.match(value):
        raise LedgerError(f"symbol must be non-empty and match [A-Za-z0-9.-]+, got {value!r}")
    return value


def _date_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise LedgerError(f"{field} must be YYYY-MM-DD, got {value!r}")
    return value


def _nonempty_str(record: dict[str, Any], key: str) -> str:
    v = record.get(key)
    if not isinstance(v, str) or not v:
        raise LedgerError(f"{key} must be a non-empty string, got {v!r}")
    return v


def _present(record: dict[str, Any], key: str) -> Any:
    v = record.get(key)
    if v is None or v == "":
        raise LedgerError(f"{key} must be present and not null")
    return v


async def evidence_append(
    store: Store, symbol: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    """One dated observation about one name (§1.4)."""
    sym = _symbol(symbol)
    ds = d.isoformat()
    claim = _nonempty_str(record, "claim")
    url = _nonempty_str(record, "url")
    independence = _nonempty_str(record, "independence")
    source_type = _nonempty_str(record, "source_type")
    observed = _nonempty_str(record, "observed")
    if source_type not in SOURCE_TYPES:
        raise LedgerError(f"source_type must be one of {SOURCE_TYPES}, got {source_type!r}")
    if observed != ds:
        # The ledger's value is the delta against a name's history; a wrong
        # observation date poisons every future comparison.
        raise LedgerError(f"observed {observed!r} must equal the date argument {ds!r}")
    extra = {
        k: v
        for k, v in record.items()
        if k not in {"claim", "url", "source_type", "observed", "independence"}
    }
    await store.insert_evidence(
        {
            "symbol": sym,
            "date": ds,
            "claim": claim,
            "url": url,
            "source_type": source_type,
            "observed": observed,
            "independence": independence,
            "extra": extra,
        }
    )
    return {"appended": True, "symbol": sym, "date": ds}


def escalation_id(symbol: str, d: date, record: dict[str, Any]) -> str:
    """CRC32 over the CANONICAL claim, so re-ordering keys cannot mint a
    second id for the same prediction."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return f"{symbol}-{d.isoformat()}-{zlib.crc32(canonical.encode()) & 0xFFFFFFFF}"


async def escalation_raise(
    store: Store, symbol: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    """Raise one prediction (§1.5). One prediction is one row."""
    sym = _symbol(symbol)
    for reserved in RESERVED_ON_RAISE:
        # `in`, not a truth test: an explicit null is still the caller writing
        # a result key into a raise.
        if reserved in record:
            raise LedgerError(f"a raise may not carry {reserved!r}: scoring is a separate row")
    _nonempty_str(record, "claim")
    direction = _nonempty_str(record, "direction")
    if direction not in DIRECTIONS:
        raise LedgerError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    _date_str(record.get("event_date"), "event_date")
    types = record.get("source_types")
    # Type before length: a bare string would clear a "2 entries" bar on its
    # character count.
    if not isinstance(types, list):
        raise LedgerError("source_types must be a JSON array")
    distinct = {t for t in types if isinstance(t, str) and t in BAR_SOURCE_TYPES}
    if len(distinct) < 2:
        raise LedgerError(
            f"the bar is 2 distinct source types from {BAR_SOURCE_TYPES}"
            f" (mainstream never counts); got {sorted(distinct)}"
        )
    eid = escalation_id(sym, d, record)
    # TOCTOU, unguarded: this read and the insert below are two separate
    # acquisitions of the store lock, and `escalations.id` carries no UNIQUE
    # constraint (it cannot -- one raise legitimately gathers many score rows
    # under the same id). Two concurrent raises of the identical claim would
    # both pass here and write two raise rows, and `store.escalations()` would
    # reduce them to one entry with the second silently overwriting the first.
    # Nothing in this system raises the same claim twice concurrently today:
    # the scout is one scheduled job at a time. A partial unique index over
    # `WHERE kind='raise'` is the real fix and belongs to a schema task, not
    # this one.
    if await store.escalation_raise_exists(eid):
        raise LedgerError(f"already raised: {eid}")
    await store.insert_escalation(
        {"id": eid, "kind": "raise", "symbol": sym, "at": d.isoformat(), "record": record}
    )
    return {"id": eid}


async def escalation_score(
    store: Store, eid: str, outcome: Outcome, d: date
) -> dict[str, Any]:
    """Score a raise (§1.5). Append-only: an id may carry many score rows and
    the LAST one wins, which is why this never rewrites the raise.

    The runtime membership test below is NOT redundant with the `Outcome`
    annotation. The caller is an MCP tool relaying whatever the model said,
    and a type checker never ran on that value.
    """
    if outcome not in OUTCOMES:
        raise LedgerError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    if not await store.escalation_raise_exists(eid):
        # Matched as a literal id, never a pattern: tickers carry dots and a
        # regex would attach an outcome to the wrong prediction.
        raise LedgerError(f"no such raise: {eid}")
    await store.insert_escalation(
        {
            "id": eid,
            "kind": "score",
            # rsplit, not split: the id is "<symbol>-<YYYY>-<MM>-<DD>-<crc>" and
            # a symbol may itself carry a hyphen, so the LAST four fields are
            # the fixed suffix. split("-")[0] read BRK-B as BRK.
            "symbol": eid.rsplit("-", 4)[0],
            "at": d.isoformat(),
            "record": {"outcome": outcome},
        }
    )
    return {"id": eid, "outcome": outcome}


async def sector_write(store: Store, rows: list[dict[str, Any]], d: date) -> dict[str, Any]:
    """Tag symbols into the scout sectors (§1.6). Every row is validated
    before any row is written -- a half-applied batch would leave the cohort
    join reading a universe nobody chose."""
    if not rows:
        raise LedgerError("no rows to write")
    ds = d.isoformat()
    validated: dict[str, tuple[str, str, str]] = {}
    for i, row in enumerate(rows, start=1):
        sym = _symbol(row.get("symbol"))
        sector = row.get("sector")
        if sector not in SECTORS:
            # An unrecognised tag would silently shrink the universe the scout
            # sweeps, so the whole batch is refused with the offending row named.
            raise LedgerError(f"row {i}: sector must be one of {SECTORS}, got {sector!r}")
        validated[sym] = (sym, str(sector), ds)  # a repeated symbol keeps the LAST row
    new, retired = await store.upsert_sectors(list(validated.values()))
    return {"written": len(validated), "new": new, "retired": retired}


async def ledger_append(
    store: Store, name: str, d: date, record: dict[str, Any]
) -> dict[str, Any]:
    """The JSONL appends of §1.3/§1.7/§1.8, one validator for all five."""
    if name not in LEDGER_REQUIRED or name not in LEDGER_TABLES:
        raise LedgerError(f"unknown ledger {name!r}, expected one of {sorted(LEDGER_REQUIRED)}")
    if not isinstance(record, dict):
        raise LedgerError("record must be an object")
    for key in LEDGER_REQUIRED[name]:
        _present(record, key)
    if name == "tombstones" and record["date"] != d.isoformat():
        raise LedgerError(
            f"tombstone date {record['date']!r} != the date argument {d.isoformat()!r}"
        )
    symbol = record.get("symbol")
    if name == "oi":
        sym = _symbol(symbol)
        # Not an error: the caller skips this underlying and continues. The
        # read and the write are two separate acquisitions of the store lock,
        # so this pre-check is only the fast path -- two concurrent appends for
        # the same (date, symbol) both see nothing here. What actually enforces
        # one snapshot per underlying per day is UNIQUE(date, symbol) on
        # `oi_snapshots`, and its IntegrityError has to arrive as the SAME skip
        # rather than as an untyped sqlite error escaping a validating writer.
        if await store.oi_symbol_seen(d, sym):
            return dict(OI_ALREADY_SNAPSHOTTED)
        try:
            await store.append_ledger(name, d, sym, record)
        except sqlite3.IntegrityError:
            return dict(OI_ALREADY_SNAPSHOTTED)
        return {"appended": True, "reason": None}
    await store.append_ledger(name, d, None if symbol is None else str(symbol), record)
    return {"appended": True, "reason": None}


async def tombstone(
    store: Store,
    symbol: str,
    d: date,
    gate: str,
    reason: str,
    ref_price: Decimal,
    hypo_qty: int | None = None,
    hypo_stop: Decimal | None = None,
) -> dict[str, Any]:
    """The typed front door onto the same validator `ledger_append` uses, so a
    tombstone cannot be written two ways with two sets of rules.

    Decimals are serialised as strings: a tombstone is the record of a price
    that was refused, and a float round-trip would move it.
    """
    record: dict[str, Any] = {
        "symbol": _symbol(symbol),
        "date": d.isoformat(),
        "gate": gate,
        "reason": reason,
        "ref_price": str(ref_price),
    }
    if hypo_qty is not None:
        record["hypo_qty"] = hypo_qty
    if hypo_stop is not None:
        record["hypo_stop"] = str(hypo_stop)
    return await ledger_append(store, "tombstones", d, record)
