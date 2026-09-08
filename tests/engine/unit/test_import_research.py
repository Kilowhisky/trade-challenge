"""Task 15: the v2 store imports through the new validators, exactly once.

Everything under `tests/engine/fixtures/legacy-store/` is synthetic — invented
tickers (SYNA…SYNZ), invented prices, invented escalation ids, `example.invalid`
URLs. No account number, account hash or broker order id appears in it, and
none may be added: the fixture is committed to a public repository.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from tc import cli
from tc.research.docs import DocStore
from tc.research.importer import ImportReport, import_research
from tc.store.db import Store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "legacy-store"
# Named, because ruff reads the literal "Last pass: ..." beside a `last_pass`
# attribute as a hardcoded credential (S105).
STAMP_1444 = "Last pass: 2026-08-28 14:44 ET"
STAMP_1544 = "Last pass: 2026-08-28 15:44 ET"


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    s = Store(tmp_path / "engine.db")
    await s.open()
    yield s
    await s.close()


@pytest.fixture
async def docs(tmp_path: Path, store: Store) -> DocStore:
    return DocStore(tmp_path / "research", store)


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    """A writable copy, so a test may corrupt a file without touching the
    committed fixture."""
    dst = tmp_path / "legacy-store"
    shutil.copytree(FIXTURE, dst)
    return dst


def _skipped(report: ImportReport, needle: str) -> list[str]:
    return [s for s in report.skipped if needle in s]


# --- documents ----------------------------------------------------------------


async def test_documents_import_through_the_validators(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert (await docs.read("candidates")).exists is True
    assert (await docs.read("candidates")).last_pass == STAMP_1444
    assert (await docs.read("standing")).verified_as_of is not None
    assert (await docs.read("scorecard")).exists is True
    assert (await docs.read("options-roster")).exists is True
    assert (await docs.read("universe")).exists is True
    assert report.documents == 6


async def test_a_dated_brief_lands_flat_under_its_date(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    await import_research(store, docs, legacy)
    view = await docs.read("preopen", date(2026, 8, 28))
    assert view.exists is True
    assert Path(view.path).name == "preopen-2026-08-28.md"


async def test_a_document_the_validators_refuse_is_named_not_stored(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """The 2026-08-31 brief is missing its pre-market banner on purpose."""
    report = await import_research(store, docs, legacy)
    assert (await docs.read("preopen", date(2026, 8, 31))).exists is False
    named = _skipped(report, "preopen/2026-08-31.md")
    assert len(named) == 1
    assert "Pre-market data informs" in named[0]


async def test_a_document_carrying_a_nul_byte_is_refused_whole(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """A corrupt document is not stored with the corruption stripped out: a
    repaired guess at what a brief said is worse than no brief."""
    (legacy / "research" / "scorecard.md").write_bytes(b"# Research scorecard\n\x00\n")
    report = await import_research(store, docs, legacy)
    assert (await docs.read("scorecard")).exists is False
    assert _skipped(report, "NUL byte in the file")


# --- ledgers ------------------------------------------------------------------


async def test_every_ledger_lands_in_its_table(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert report.screen == 4  # three on 2026-08-28, one on 2026-08-31
    assert report.iv == 2
    assert report.oi == 2
    assert report.events == 2
    assert report.tombstones == 2
    assert len(await store.ledger_rows("iv")) == report.iv
    assert len(await store.ledger_rows("screen")) == report.screen
    assert len(await store.ledger_rows("events")) == report.events
    assert {r["symbol"] for r in await store.ledger_rows("oi")} == {"SYNA", "SYNB"}


async def test_only_the_events_data_ledger_is_imported(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """`status/data/` also holds orders, quotes, decisions and counterfactuals.
    None has a table or a validator here, so none is forced into `events`."""
    await import_research(store, docs, legacy)
    records = [r["record"] for r in await store.ledger_rows("events")]
    assert all(r["event"] == "deep_research" for r in records)


async def test_a_row_missing_a_required_field_is_named_not_stored(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert {r["symbol"] for r in await store.ledger_rows("screen")} == {
        "SYNA", "SYNB", "SYND", "SYNE",
    }
    assert _skipped(report, "screen/2026-08-28.jsonl:3")   # no src
    assert _skipped(report, "screen/2026-08-28.jsonl:5")   # never JSON
    assert _skipped(report, "tombstones.jsonl:3")          # no ref_price


async def test_a_nul_byte_line_is_skipped_with_a_reason(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """docker's json.log put NUL bytes into a live ledger on 2026-08-31 and
    grep went binary-silent for four days. The clean line beside it survives."""
    report = await import_research(store, docs, legacy)
    assert any("NUL" in s for s in report.skipped)
    assert _skipped(report, "screen/2026-08-31.jsonl:2")
    rows = await store.ledger_rows("screen", date(2026, 8, 31))
    assert [r["symbol"] for r in rows] == ["SYNE"]


async def test_a_second_oi_snapshot_for_a_symbol_is_a_skip_not_a_row(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert _skipped(report, "already snapshotted today")
    assert len(await store.ledger_rows("oi", date(2026, 8, 28))) == 2


# --- evidence -----------------------------------------------------------------


async def test_evidence_imports_under_its_filename_symbol(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    rows = await store.evidence_for("SYNA")
    assert [r["source_type"] for r in rows] == ["end-user", "employee"]
    assert report.evidence == 2
    assert _skipped(report, "source_type must be one of")


async def test_a_store_with_no_evidence_directory_is_normal(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """The live store has never had one — the scout has never written."""
    shutil.rmtree(legacy / "research" / "evidence")
    report = await import_research(store, docs, legacy)
    assert report.evidence == 0
    assert not _skipped(report, "evidence")


# --- escalations --------------------------------------------------------------


async def test_the_v2_escalation_id_is_carried_not_recomputed(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """v2's id is a cksum CRC-32; the new writer's is zlib.crc32, a different
    function. Recomputing would orphan every score row in the same file."""
    await import_research(store, docs, legacy)
    rows = await store.escalations()
    assert [r["id"] for r in rows] == ["SYNA-2026-08-28-1234567890"]
    assert rows[0]["latest_outcome"] == "right"


async def test_a_score_without_a_raise_is_skipped_not_orphaned(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert any("no such raise" in s for s in report.skipped)


async def test_a_raise_below_the_bar_is_refused_the_way_it_would_be_today(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """One `mainstream` source is corroboration by something already on the
    wire. It was never an information edge and it is not imported as one."""
    report = await import_research(store, docs, legacy)
    assert await store.escalation_raise_exists("SYNB-2026-08-28-2222222222") is False
    assert _skipped(report, "the bar is 2 distinct source types")


# --- sectors ------------------------------------------------------------------


async def test_sectors_import_row_by_row_so_one_bad_tag_costs_one_row(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert {r["symbol"]: r["sector"] for r in await store.sectors()} == {
        "SYNA": "semis-hardware",
        "SYNB": "airlines-transport",
        "SYND": "other",
    }
    assert report.sectors == 3
    assert _skipped(report, "sectors.tsv:3")


# --- universe -----------------------------------------------------------------


async def test_universe_md_becomes_rows_with_the_assembled_date(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert await store.universe_asof() == date(2026, 8, 29)
    rows = await store.universe_rows()
    assert [r["symbol"] for r in rows] == ["SYNA", "SYNB", "SYNC", "SYND"]
    assert all(r["qualified"] for r in rows)
    assert report.universe == 4


async def test_an_empty_last_earnings_and_a_dash_range_survive_the_round_trip(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """`-` is "the payload carried no usable high/low", not a zero range, and
    an empty earnings date is normal — cohort.sh reads column 8 positionally."""
    await import_research(store, docs, legacy)
    by_symbol = {r["symbol"]: r for r in await store.universe_rows()}
    assert by_symbol["SYNC"]["last_earnings"] == ""
    assert by_symbol["SYND"]["session_range_pct"] is None
    assert by_symbol["SYNA"]["session_range_pct"] is not None


async def test_an_unterminated_fence_is_still_the_universe_table(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """The live store's own shape: the improvised 2026-08-29 sweep wrote the
    opening fence and every row and never closed it. Refusing that file would
    drop the whole working universe over one missing line."""
    path = legacy / "research" / "universe.md"
    body = path.read_text()
    head, _, _ = body.rpartition("```")
    path.write_text(head)
    await import_research(store, docs, legacy)
    assert [r["symbol"] for r in await store.universe_rows()] == ["SYNA", "SYNB", "SYNC", "SYND"]


async def test_a_fence_that_is_not_the_universe_table_is_not_parsed_as_one(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    path = legacy / "research" / "universe.md"
    path.write_text(path.read_text().replace("symbol\tprice", "ticker\tprice"))
    report = await import_research(store, docs, legacy)
    assert await store.universe_asof() is None
    assert _skipped(report, "no fenced block carrying the ten-column universe header")


async def test_a_short_universe_row_is_named_not_guessed_at(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    report = await import_research(store, docs, legacy)
    assert _skipped(report, "expected 10 columns, got 4")


async def test_a_universe_without_an_assembled_line_has_no_asof(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    path = legacy / "research" / "universe.md"
    path.write_text(path.read_text().replace("Assembled:", "Written:"))
    report = await import_research(store, docs, legacy)
    assert await store.universe_asof() is None
    assert _skipped(report, "no 'Assembled:' line")


# --- idempotency --------------------------------------------------------------


async def test_import_is_idempotent(store: Store, docs: DocStore, legacy: Path) -> None:
    first = await import_research(store, docs, legacy)
    second = await import_research(store, docs, legacy)
    assert first.screen > 0
    for name in ImportReport.model_fields:
        if name != "skipped":
            assert getattr(second, name) == 0, f"{name} imported twice"


async def test_a_second_run_stores_no_second_copy_of_anything(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    await import_research(store, docs, legacy)
    before = {
        name: len(await store.ledger_rows(name))
        for name in ("screen", "iv", "oi", "events", "tombstones")
    }
    escalations = len(await store.fetchall("SELECT row_id FROM escalations"))
    await import_research(store, docs, legacy)
    after = {
        name: len(await store.ledger_rows(name))
        for name in ("screen", "iv", "oi", "events", "tombstones")
    }
    assert after == before
    assert len(await store.fetchall("SELECT row_id FROM escalations")) == escalations
    assert len(await store.evidence_for("SYNA")) == 2
    assert len(await store.universe_rows()) == 4


async def test_a_replayed_score_sequence_is_matched_positionally(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """An id may carry many scores and the last one wins, so "already scored?"
    is the wrong question: the right one is whether the Nth score in the file
    is already the Nth score in the table."""
    path = legacy / "research" / "escalations.jsonl"
    path.write_text(
        path.read_text()
        + '{"id":"SYNA-2026-08-28-1234567890","outcome":"wrong","scored":"2026-09-20",'
        '"kind":"score"}\n'
    )
    first = await import_research(store, docs, legacy)
    second = await import_research(store, docs, legacy)
    assert first.escalations == 3
    assert second.escalations == 0
    rows = await store.escalations()
    assert rows[0]["latest_outcome"] == "wrong"


async def test_a_document_edited_after_the_import_is_replaced_again(
    store: Store, docs: DocStore, legacy: Path
) -> None:
    """Idempotency is a content compare, not a "seen this path" flag: a v2
    file that changed between two runs must still reach the store."""
    await import_research(store, docs, legacy)
    path = legacy / "research" / "candidates.md"
    path.write_text(path.read_text().replace("14:44 ET", "15:44 ET"))
    report = await import_research(store, docs, legacy)
    assert report.documents == 1
    assert (await docs.read("candidates")).last_pass == STAMP_1544


# --- CLI ----------------------------------------------------------------------


CONFIG = """
engine: {timezone: America/New_York, data_dir: "%s", repo_dir: "%s", research_dir: "%s", http_bind: 127.0.0.1:8080, reserve_usd: "900.00"}
token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://pi.example.ts.net/oauth/callback}
runner: {url: 'http://127.0.0.1:8090'}
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n"


def _cfg(tmp_path: Path) -> list[str]:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (tmp_path / "config.yml").write_text(CONFIG % (data, tmp_path, data / "research"))
    (tmp_path / ".env").write_text(ENV)
    return ["--config", str(tmp_path / "config.yml"), "--env", str(tmp_path / ".env")]


def _iv_rows(tmp_path: Path) -> list[dict[str, object]]:
    """The engine store's iv ledger, read back in its own event loop.

    These four tests are synchronous because `cli.main` calls `asyncio.run`,
    which refuses to start inside a loop pytest-asyncio is already running.
    """

    async def read() -> list[dict[str, object]]:
        store = Store(tmp_path / "data" / "engine.db")
        await store.open()
        try:
            return await store.ledger_rows("iv")
        finally:
            await store.close()

    return asyncio.run(read())


def test_cli_import_research_writes_the_store(
    tmp_path: Path, legacy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    rc = cli.main([*args, "import-research", "--store-dir", str(legacy)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().endswith("IMPORT OK")
    assert "iv 2" in out
    assert len(_iv_rows(tmp_path)) == 2
    assert (tmp_path / "data" / "research" / "candidates.md").exists()


def test_cli_dry_run_writes_nothing(
    tmp_path: Path, legacy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    rc = cli.main([*args, "import-research", "--store-dir", str(legacy), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().endswith("IMPORT DRY-RUN")
    assert "iv 2" in out
    assert not (tmp_path / "data" / "research").exists()
    assert _iv_rows(tmp_path) == []


def test_cli_dry_run_reports_against_the_live_store_not_an_empty_one(
    tmp_path: Path, legacy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run after a real import must report zeroes, not "would import
    everything" — that is the one question it is asked before a re-run."""
    args = _cfg(tmp_path)
    assert cli.main([*args, "import-research", "--store-dir", str(legacy)]) == 0
    capsys.readouterr()
    assert cli.main([*args, "import-research", "--store-dir", str(legacy), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "iv 0" in out and "documents 0" in out


def test_cli_reports_what_it_refused_without_failing(
    tmp_path: Path, legacy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    rc = cli.main([*args, "import-research", "--store-dir", str(legacy)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no such raise" in out
    assert "NUL byte" in out


def test_cli_a_missing_store_directory_is_an_operator_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cfg(tmp_path)
    rc = cli.main([*args, "import-research", "--store-dir", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert rc == 4
    assert err.startswith("tc: no legacy store at ")
