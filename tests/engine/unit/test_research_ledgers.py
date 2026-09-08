from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tc.research.ledgers import (
    LedgerError,
    escalation_raise,
    escalation_score,
    evidence_append,
    ledger_append,
    sector_write,
    tombstone,
)
from tc.store.db import Store

D7 = date(2026, 9, 7)
OBS = {"claim": "queue times doubled at three sites", "url": "https://forum/1",
       "source_type": "end-user", "observed": "2026-09-07",
       "independence": "unaffiliated poster, no cross-links"}
RAISE = {"claim": "unit shipments up materially into the print", "direction": "up",
         "event_date": "2026-10-14", "source_types": ["end-user", "counterparty"]}


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield s
    await s.close()


async def test_evidence_happy_path(store: Store) -> None:
    assert (await evidence_append(store, "CSX", D7, OBS))["appended"] is True
    assert len(await store.evidence_for("CSX")) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        {"claim": ""},                    # empty string, not merely present
        {"claim": None},                  # explicit null -- has() would pass this
        {"url": None},
        {"independence": ""},
        {"source_type": "blog"},          # outside the closed set
        {"observed": "2026-09-06"},       # != the date argument
    ],
)
async def test_evidence_refusals(store: Store, mutate: dict[str, Any]) -> None:
    with pytest.raises(LedgerError):
        await evidence_append(store, "CSX", D7, {**OBS, **mutate})


async def test_evidence_symbol_charset(store: Store) -> None:
    await evidence_append(store, "BRK.B", D7, OBS)          # a dot is legal
    with pytest.raises(LedgerError):
        await evidence_append(store, "C SX", D7, OBS)


async def test_evidence_keeps_unnamed_fields_as_extra(store: Store) -> None:
    await evidence_append(store, "CSX", D7, {**OBS, "handle": "@someone", "score": 3})
    assert (await store.evidence_for("CSX"))[0]["extra"] == {"handle": "@someone", "score": 3}


async def test_mainstream_is_recordable_but_never_counts_toward_the_bar(store: Store) -> None:
    await evidence_append(store, "CSX", D7, {**OBS, "source_type": "mainstream"})
    with pytest.raises(LedgerError):
        await escalation_raise(
            store, "CSX", D7, {**RAISE, "source_types": ["end-user", "mainstream"]}
        )


@pytest.mark.parametrize(
    "mutate",
    [
        {"source_types": ["end-user"]},                  # one distinct type
        {"source_types": ["end-user", "end-user"]},      # two entries, one type
        {"source_types": "end-user,counterparty"},       # a string, not an array
        {"source_types": [1, 2]},                        # an array of non-strings
        {"direction": "sideways"},
        {"direction": None},
        {"claim": ""},
        {"event_date": "next month"},
        {"outcome": "right"},                            # a raise may not carry its outcome
        {"scored": "2026-09-08"},
        {"kind": "raise"},
    ],
)
async def test_escalation_bar(store: Store, mutate: dict[str, Any]) -> None:
    with pytest.raises(LedgerError):
        await escalation_raise(store, "CSX", D7, {**RAISE, **mutate})


async def test_escalation_bar_refuses_a_null_outcome_too(store: Store) -> None:
    # `has("outcome")` is what the bash tested, and an explicit null is still a
    # raise carrying its own result key.
    with pytest.raises(LedgerError):
        await escalation_raise(store, "CSX", D7, {**RAISE, "outcome": None})


async def test_escalation_id_is_canonical_and_deduped(store: Store) -> None:
    a = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    assert a.startswith("CSX-2026-09-07-")
    reordered = {k: RAISE[k] for k in reversed(list(RAISE))}
    with pytest.raises(LedgerError):        # same claim, different key order, same id
        await escalation_raise(store, "CSX", D7, reordered)


async def test_escalation_raise_lands_with_its_claim(store: Store) -> None:
    eid = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    rows = await store.escalations("CSX")
    assert [r["id"] for r in rows] == [eid]
    assert rows[0]["direction"] == "up"
    assert rows[0]["latest_outcome"] is None


async def test_score_needs_a_raise_and_a_legal_outcome(store: Store) -> None:
    eid = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    with pytest.raises(LedgerError):
        await escalation_score(store, eid, "maybe", D7)
    with pytest.raises(LedgerError):
        await escalation_score(store, "CSX-2026-09-07-nope", "right", D7)
    await escalation_score(store, eid, "right", D7)
    assert (await store.escalations())[0]["latest_outcome"] == "right"


async def test_last_score_wins(store: Store) -> None:
    eid = (await escalation_raise(store, "CSX", D7, RAISE))["id"]
    await escalation_score(store, eid, "wrong", D7)
    await escalation_score(store, eid, "void", D7)
    rows = await store.escalations()
    assert len(rows) == 1
    assert rows[0]["latest_outcome"] == "void"


async def test_sector_write_validates_every_row_before_writing_any(store: Store) -> None:
    with pytest.raises(LedgerError):
        await sector_write(
            store,
            [{"symbol": "CSX", "sector": "airlines-transport"},
             {"symbol": "NOPE", "sector": "biotech"}],
            D7,
        )
    assert await store.sectors() == []            # nothing was written


async def test_sector_write_last_row_wins_and_counts(store: Store) -> None:
    out = await sector_write(
        store,
        [{"symbol": "CSX", "sector": "semis-hardware"},
         {"symbol": "CSX", "sector": "airlines-transport"}],
        D7,
    )
    assert out == {"written": 1, "new": 1, "retired": 0}
    assert (await store.sectors())[0]["sector"] == "airlines-transport"


async def test_sector_write_counts_a_retirement(store: Store) -> None:
    await sector_write(store, [{"symbol": "CSX", "sector": "airlines-transport"}], D7)
    out = await sector_write(store, [{"symbol": "CSX", "sector": "other"}], D7)
    assert out == {"written": 1, "new": 0, "retired": 1}


async def test_sector_write_refuses_an_empty_batch(store: Store) -> None:
    with pytest.raises(LedgerError):
        await sector_write(store, [], D7)


async def test_sector_symbols_are_compared_as_strings(store: Store) -> None:
    # awk's strnum compare made tagging `1E2` delete the row for `100`.
    await sector_write(
        store,
        [{"symbol": "1E2", "sector": "semis-hardware"},
         {"symbol": "100", "sector": "airlines-transport"}],
        D7,
    )
    assert {r["symbol"]: r["sector"] for r in await store.sectors()} == {
        "1E2": "semis-hardware", "100": "airlines-transport"
    }


@pytest.mark.parametrize(
    "name, record",
    [
        ("screen", {"symbol": "SAN", "t": "16:42:00", "src": "screener"}),
        ("iv", {"symbol": "CSX", "t": "16:26:00", "atm_iv": 0.208}),
        ("events", {"t": "08:26:00", "event": "deep_research", "mode": "preopen"}),
    ],
)
async def test_ledger_append_happy_paths(
    store: Store, name: str, record: dict[str, Any]
) -> None:
    assert (await ledger_append(store, name, D7, record))["appended"] is True
    assert len(await store.ledger_rows(name, D7)) == 1


@pytest.mark.parametrize(
    "name, record",
    [
        ("screen", {"symbol": "SAN", "t": "16:42:00"}),          # no src
        ("iv", {"symbol": "CSX", "t": "16:26:00", "atm_iv": None}),  # explicit null
        ("events", {"event": "x"}),                               # no t
        ("nosuch", {"t": "1"}),
    ],
)
async def test_ledger_append_refusals(
    store: Store, name: str, record: dict[str, Any]
) -> None:
    with pytest.raises(LedgerError):
        await ledger_append(store, name, D7, record)


async def test_oi_second_snapshot_is_a_skip_not_an_error(store: Store) -> None:
    rec = {"symbol": "CSX", "t": "16:26:00", "call_oi": 2874, "put_oi": 1902}
    assert (await ledger_append(store, "oi", D7, rec))["appended"] is True
    again = await ledger_append(store, "oi", D7, rec)
    assert again == {"appended": False, "reason": "already snapshotted today"}
    assert len(await store.ledger_rows("oi", D7)) == 1


async def test_oi_idempotency_is_per_symbol_per_day(store: Store) -> None:
    await ledger_append(store, "oi", D7, {"symbol": "CSX", "t": "16:26:00"})
    await ledger_append(store, "oi", D7, {"symbol": "MNDY", "t": "16:27:00"})
    assert (await ledger_append(store, "oi", date(2026, 9, 8), {"symbol": "CSX", "t": "16:26:00"}))[
        "appended"
    ] is True


async def test_tombstone_cross_checks_its_own_date(store: Store) -> None:
    await tombstone(store, "MNDY", D7, "gap-and-hold", "faded the gap", Decimal("88.50"))
    assert (await store.ledger_rows("tombstones", D7))[0]["record"]["date"] == "2026-09-07"
    with pytest.raises(LedgerError):
        await ledger_append(
            store, "tombstones", D7,
            {"symbol": "X", "date": "2026-09-06", "gate": "g", "reason": "r", "ref_price": 1.0},
        )


async def test_tombstone_keeps_decimals_exact_and_omits_absent_hypotheticals(
    store: Store,
) -> None:
    await tombstone(store, "MNDY", D7, "gap-and-hold", "faded", Decimal("88.50"), 4, Decimal("79.65"))
    await tombstone(store, "SAN", D7, "liquidity", "thin", Decimal("5.10"))
    rows = {r["symbol"]: r["record"] for r in await store.ledger_rows("tombstones", D7)}
    assert rows["MNDY"]["ref_price"] == "88.50"       # a string, not 88.5
    assert rows["MNDY"]["hypo_qty"] == 4
    assert rows["MNDY"]["hypo_stop"] == "79.65"
    assert "hypo_qty" not in rows["SAN"]


async def test_tombstone_refuses_a_bad_symbol(store: Store) -> None:
    with pytest.raises(LedgerError):
        await tombstone(store, "MN DY", D7, "gate", "reason", Decimal("1.00"))
