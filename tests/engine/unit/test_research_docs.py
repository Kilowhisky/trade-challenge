from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest

from tc.research.docs import DocCasMismatch, DocStore, DocValidationError
from tc.store.db import Store

CANDIDATES = "\n".join(
    ["# Research candidates", "",
     "**This file is never a source for order parameters — every entry re-verifies",
     "live under §4.9/§4.10.**", "",
     "Last pass: 2026-09-07 14:44 ET", "", "## HOT", "", "(none)", "", "## WATCH", "", "(none)"]
)
# Stamps live in named constants because ruff's S105/S106 read the literal
# "Last pass: ..." beside a `last_pass` name as a hardcoded credential.
STAMP_1444 = "Last pass: 2026-09-07 14:44 ET"
STAMP_1300 = "Last pass: 2026-09-07 13:00 ET"
STAMP_0900 = "Last pass: 2026-09-07 09:00 ET"
ANY_STAMP = "Last pass: whatever"
STANDING = "\n".join(
    ["# Standing research reference", "",
     "never a source for order parameters", "",
     "Verified as of: 2026-09-07 16:47 ET (postclose deep run)", "", "body"]
)


@pytest.fixture
async def docs(tmp_path: Path) -> AsyncIterator[DocStore]:
    s = Store(tmp_path / "e.db")
    await s.open()
    yield DocStore(tmp_path / "research" / "docs", s)
    await s.close()


async def test_replace_then_read_surfaces_last_pass_as_a_field(docs: DocStore) -> None:
    w = await docs.replace("candidates", CANDIDATES)
    assert w.lines == len(CANDIDATES.splitlines())
    v = await docs.read("candidates")
    assert v.exists is True
    assert v.last_pass == STAMP_1444
    assert v.verified_as_of is None


async def test_read_of_a_missing_document_is_not_an_error(docs: DocStore) -> None:
    v = await docs.read("scorecard")
    assert v.exists is False and v.body == "" and v.last_pass is None


async def test_standing_surfaces_verified_as_of(docs: DocStore) -> None:
    await docs.replace("standing", STANDING)
    va = (await docs.read("standing")).verified_as_of
    assert va is not None and va.startswith("Verified as of: 2026-09-07")


@pytest.mark.parametrize(
    "body, why",
    [
        ("# Research candidates\nshort", "line count"),
        (CANDIDATES.replace("# Research candidates", "# Candidates"), "first line"),
        (CANDIDATES.replace("never a source for order parameters", "trust me"), "banner"),
        (CANDIDATES.replace("Last pass: 2026-09-07 14:44 ET", "passed today"), "Last pass"),
    ],
)
async def test_candidates_validators(docs: DocStore, body: str, why: str) -> None:
    with pytest.raises(DocValidationError):
        await docs.replace("candidates", body)
    # a refused body leaves nothing behind: no file, no artifact row
    assert docs.path_for("candidates", None).exists() is False
    assert await docs.store.fetchall("SELECT id FROM artifacts") == []


async def test_standing_requires_the_anchored_stamp(docs: DocStore) -> None:
    with pytest.raises(DocValidationError):
        await docs.replace("standing", STANDING.replace("Verified as of:", "verified as of:"))


async def test_cas_refuses_a_stale_writer_and_accepts_the_fresh_one(docs: DocStore) -> None:
    await docs.replace("candidates", CANDIDATES)
    stale = CANDIDATES.replace("14:44", "13:00")
    with pytest.raises(DocCasMismatch):
        await docs.replace("candidates", stale, expect_last_pass=STAMP_1300)
    fresh = CANDIDATES.replace("14:44", "15:12")
    w = await docs.replace("candidates", fresh, expect_last_pass=STAMP_1444)
    assert w.lines > 10
    lp = (await docs.read("candidates")).last_pass
    assert lp is not None and lp.endswith("15:12 ET")


async def test_a_refused_cas_does_not_touch_the_file(docs: DocStore) -> None:
    """The stale writer's body must not reach the disk, and must not be the
    thing the .prev snapshot preserves either."""
    await docs.replace("candidates", CANDIDATES)
    with pytest.raises(DocCasMismatch):
        await docs.replace(
            "candidates", CANDIDATES.replace("14:44", "13:00"),
            expect_last_pass=STAMP_0900,
        )
    assert "14:44" in docs.path_for("candidates", None).read_text()
    assert docs.path_for("candidates", None).with_suffix(".md.prev").exists() is False


async def test_cas_on_a_missing_file_is_allowed(docs: DocStore) -> None:
    await docs.replace("candidates", CANDIDATES, expect_last_pass=ANY_STAMP)


async def test_preopen_needs_its_date_and_the_em_dash(docs: DocStore) -> None:
    body = "\n".join(
        ["# Pre-open brief — 2026-09-08", "",
         "Pre-market data informs, it never qualifies", "", "a", "b", "c"]
    )
    w = await docs.replace("preopen", body, d=date(2026, 9, 8))
    assert w.path.endswith("preopen-2026-09-08.md")
    with pytest.raises(DocValidationError):
        await docs.replace("preopen", body)                       # no date
    with pytest.raises(DocValidationError):
        await docs.replace("preopen", body, d=date(2026, 9, 9))   # H1 names another day
    with pytest.raises(DocValidationError):
        await docs.replace("candidates", CANDIDATES, d=date(2026, 9, 8))  # date on a 1-arity kind


async def test_read_rejects_the_same_date_arity_as_replace(docs: DocStore) -> None:
    with pytest.raises(DocValidationError):
        await docs.read("preopen")
    with pytest.raises(DocValidationError):
        await docs.read("candidates", date(2026, 9, 8))


async def test_the_other_kinds_carry_their_own_banners(docs: DocStore) -> None:
    roster = "\n".join(
        ["# Options-viable roster", "", "never a source for order parameters",
         "TTL: 2 sessions", "", "AAPL", "MSFT"]
    )
    await docs.replace("options-roster", roster)
    with pytest.raises(DocValidationError):                       # TTL is a banner here
        await docs.replace("options-roster", roster.replace("TTL: 2 sessions", "expires soon"))

    scorecard = "\n".join(
        ["# Research scorecard", "", "A gate never loosens a gate in-flight;",
         "changing one is an explicit conversation with Chris.", "", "hits: 3", "misses: 4"]
    )
    await docs.replace("scorecard", scorecard)
    with pytest.raises(DocValidationError):                       # both banners are required
        await docs.replace(
            "scorecard", scorecard.replace("explicit conversation with Chris", "my call")
        )

    universe = "\n".join(
        ["# Fallback universe", "", "never a source for order parameters", "",
         "AAPL", "MSFT", "NVDA"]
    )
    await docs.replace("universe", universe)
    assert (await docs.read("universe")).exists is True


async def test_an_unknown_kind_is_a_validation_error_not_a_key_error(docs: DocStore) -> None:
    with pytest.raises(DocValidationError):
        await docs.read("roster")  # type: ignore[arg-type]  # v2's name for options-roster


async def test_previous_version_is_kept_and_the_artifact_is_indexed(docs: DocStore) -> None:
    await docs.replace("candidates", CANDIDATES)
    await docs.replace("candidates", CANDIDATES.replace("14:44", "15:12"))
    prev = docs.path_for("candidates", None).with_suffix(".md.prev")
    assert "14:44" in prev.read_text()
    rows = await docs.store.fetchall("SELECT kind, lines FROM artifacts ORDER BY id")
    assert [r["kind"] for r in rows] == ["candidates", "candidates"]
    assert [r["lines"] for r in rows] == [len(CANDIDATES.splitlines())] * 2


async def test_a_body_is_stored_with_exactly_one_trailing_newline(docs: DocStore) -> None:
    """v2's `printf '%s\\n'` added the newline the heredoc lost; a body that
    already ends in one must not gain a second (it would move every line
    count and every sha)."""
    await docs.replace("candidates", CANDIDATES + "\n")
    text = docs.path_for("candidates", None).read_text()
    assert text.endswith("(none)\n") and not text.endswith("\n\n")
    row = await docs.store.fetchone("SELECT lines FROM artifacts ORDER BY id DESC LIMIT 1")
    assert row is not None and row["lines"] == len(CANDIDATES.splitlines())
