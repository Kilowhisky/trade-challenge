"""One-shot import of the v2 bash store into the v3 engine store.

The v2 system wrote markdown documents, JSONL ledgers and a TSV under
`research/` and `status/data/`; the engine reads a SQLite store and a flat
document directory instead. This module moves the one into the other, once,
and it holds to four properties that are the whole reason it is not a `cat`
into a table:

* **Everything goes through the validators the tools use.** Documents through
  `DocStore.replace`, ledger rows through `tc.research.ledgers`, so a legacy
  row that today's writer would refuse is refused now — named in `skipped`
  with the reason, never stored. Importing invalid rows "so nothing is lost"
  would make the first content in the new store the exact content its rules
  reject.
* **It is idempotent.** A second run imports nothing: documents are compared
  byte for byte before a replace, ledger rows are compared as canonical JSON
  against what the table already holds, an escalation raise that exists is
  skipped rather than refused, and scores are matched positionally against the
  rows already filed under their id.
* **A NUL byte is a refusal, not a crash.** On 2026-08-31 docker's `json.log`
  put NUL bytes into a live JSONL ledger and grep went binary-silent for four
  days. Every file here is read as bytes; a line carrying a NUL is rejected on
  read and named in `skipped`, and so is a whole document that carries one —
  a corrupted document is not something to store a repaired guess of.
* **Nothing is fatal.** An unreadable file, an unparseable line, an unknown
  ledger kind: each is one line in `skipped` and the import continues. A
  migration that aborts halfway is worse than one that reports what it could
  not take.

Two v2 details survive verbatim on purpose:

* **Escalation ids are carried, never recomputed.** v2's id is a `cksum`
  CRC-32 over the canonical claim; `ledgers.escalation_id` uses `zlib.crc32`,
  which is a different function. Recomputing would mint ids the score rows in
  the same file do not reference, orphaning every prediction's outcome.
* **`research/universe.md` lands twice** — as the document Claude reads whole,
  and as the `universe` table's rows, with `asof` taken from its `Assembled:`
  line. They are two different readers of one file, not a duplicate.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from tc.research import ledgers
from tc.research.docs import DocKind, DocStore, DocValidationError
from tc.store.db import Store

DATED = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The stamp is written `Assembled: 2026-08-29T11:56:09Z — fetched ...`; only
# the date part is the sweep's `asof`.
ASSEMBLED = re.compile(r"^Assembled:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
FENCE = "```"

# The ten columns `universe-filter.sh` writes, in its order. Checked rather
# than assumed: `cohort.sh` indexes column 8 positionally, so a file whose
# columns moved must be reported, not silently mapped by position.
UNIVERSE_COLUMNS = (
    "symbol",
    "price",
    "adv10",
    "dollar_vol",
    "pct_from_52wk_high",
    "optionable",
    "leverage",
    "last_earnings",
    "is_etf",
    "session_range_pct",
)

# The five documents v2 kept at a fixed path. `preopen` is dated and is
# globbed separately; the engine stores it flat as `preopen-<date>.md`.
FLAT_DOCS: tuple[tuple[DocKind, str], ...] = (
    ("candidates", "candidates.md"),
    ("standing", "standing.md"),
    ("scorecard", "scorecard.md"),
    ("options-roster", "options-roster.md"),
    ("universe", "universe.md"),
)

# The four JSONL ledgers that live under `research/` with a date in the path
# or in the row. `events` comes from `status/data/` and is handled separately.
DATED_LEDGERS = (("screen", "screen"), ("iv", "iv"), ("oi", "oi"))


class ImportReport(BaseModel):
    """What landed, and what did not. Every count is rows *newly* written, so
    a second run of the same source reports zeroes across the board."""

    model_config = ConfigDict(extra="forbid")
    documents: int = 0
    evidence: int = 0
    escalations: int = 0
    sectors: int = 0
    tombstones: int = 0
    screen: int = 0
    iv: int = 0
    oi: int = 0
    events: int = 0
    universe: int = 0
    skipped: list[str] = Field(default_factory=list)

    def skip(self, where: str, reason: str) -> None:
        self.skipped.append(f"{where}: {reason}")

    def bump(self, ledger: str, by: int = 1) -> None:
        setattr(self, ledger, getattr(self, ledger) + by)


def _canonical(record: dict[str, Any]) -> str:
    """The dedupe key for a ledger row: the same bytes `Store.append_ledger`
    stores, so a row read back compares equal to the row about to be written.
    `default=str` because a legacy row may carry something json cannot type —
    that is a key collision risk of exactly zero and a crash avoided."""
    return json.dumps(record, sort_keys=True, default=str)


def _read_bytes(path: Path, where: str, report: ImportReport) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError as e:
        report.skip(where, f"unreadable: {e}")
        return None


def _read_text(path: Path, where: str, report: ImportReport) -> str | None:
    """A whole file, refused rather than repaired if it is corrupt."""
    data = _read_bytes(path, where, report)
    if data is None:
        return None
    if b"\x00" in data:
        report.skip(where, "NUL byte in the file, not imported (the 2026-08-31 corruption)")
        return None
    try:
        return data.decode()
    except UnicodeDecodeError as e:
        report.skip(where, f"undecodable bytes ({e.reason})")
        return None


def _read_jsonl(path: Path, where: str, report: ImportReport) -> list[tuple[int, dict[str, Any]]]:
    """Every well-formed object in a JSONL file, with its line number.

    Split on bytes before decoding: one corrupt line must cost that line and
    nothing else, and a NUL anywhere in the file used to cost the whole file
    by making every downstream reader treat it as binary.
    """
    data = _read_bytes(path, where, report)
    if data is None:
        return []
    out: list[tuple[int, dict[str, Any]]] = []
    for n, raw in enumerate(data.split(b"\n"), start=1):
        line = raw.strip()
        if not line:
            continue
        if b"\x00" in line:
            report.skip(f"{where}:{n}", "NUL byte in the line, rejected on read")
            continue
        try:
            text = line.decode()
        except UnicodeDecodeError as e:
            report.skip(f"{where}:{n}", f"undecodable bytes ({e.reason})")
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            report.skip(f"{where}:{n}", f"not JSON ({e.msg})")
            continue
        if not isinstance(obj, dict):
            report.skip(f"{where}:{n}", "not a JSON object")
            continue
        out.append((n, cast(dict[str, Any], obj)))
    return out


# The filesystem probes live in plain functions, as `DocStore`'s reads and
# writes do: a blocking stat inside a coroutine is what ASYNC240 flags, and a
# one-shot migration has no reason to pretend its file I/O is concurrent.
def _is_file(path: Path) -> bool:
    return path.is_file()


def _is_dir(path: Path) -> bool:
    return path.is_dir()


def _glob(base: Path, pattern: str) -> list[Path]:
    return sorted(base.glob(pattern))


def _rel(src: Path, path: Path) -> str:
    try:
        return str(path.relative_to(src))
    except ValueError:  # pragma: no cover - src always contains path here
        return str(path)


# --- documents ---------------------------------------------------------------


async def _import_doc(
    docs: DocStore, path: Path, kind: DocKind, d: date | None, src: Path, report: ImportReport
) -> None:
    if not _is_file(path):
        return
    where = _rel(src, path)
    body = _read_text(path, where, report)
    if body is None:
        return
    text = body if body.endswith("\n") else body + "\n"
    current = await docs.read(kind, d)
    if current.exists and current.body == text:
        return  # already imported, byte for byte
    try:
        await docs.replace(kind, body, d)
    except DocValidationError as e:
        report.skip(where, str(e))
        return
    report.documents += 1


async def _import_documents(docs: DocStore, src: Path, report: ImportReport) -> None:
    research = src / "research"
    for kind, name in FLAT_DOCS:
        await _import_doc(docs, research / name, kind, None, src, report)
    preopen = research / "preopen"
    if not _is_dir(preopen):
        return
    # `*.md` and not `*` on purpose: v2 kept a `.prev` snapshot beside each
    # replaced document, and a snapshot is history, not a second brief.
    for path in _glob(preopen, "*.md"):
        if not DATED.match(path.stem):
            report.skip(_rel(src, path), "filename is not a YYYY-MM-DD brief")
            continue
        await _import_doc(docs, path, "preopen", date.fromisoformat(path.stem), src, report)


# --- JSONL ledgers -----------------------------------------------------------


async def _import_ledger_file(
    store: Store,
    ledger: str,
    path: Path,
    d: date | None,
    src: Path,
    report: ImportReport,
) -> None:
    """One JSONL file into one ledger table.

    `d` is None for `tombstones`, whose rows carry their own date and whose
    file spans every day the account has run.

    Idempotency is a content compare, because none of these ledgers has a key
    beyond `oi`'s (date, symbol). The cost is that two byte-identical lines in
    one legacy file collapse to one row — accepted deliberately: a duplicated
    line carries no information the first one does not, and the alternative is
    a second import silently doubling every ledger in the store.
    """
    where = _rel(src, path)
    rows = _read_jsonl(path, where, report)
    if not rows:
        return
    seen: dict[str, set[str]] = {}
    for n, record in rows:
        row_date = d
        if row_date is None:
            raw = record.get("date")
            if not isinstance(raw, str) or not DATED.match(raw):
                report.skip(f"{where}:{n}", f"date must be YYYY-MM-DD, got {raw!r}")
                continue
            row_date = date.fromisoformat(raw)
        key = row_date.isoformat()
        if key not in seen:
            seen[key] = {
                _canonical(r["record"]) for r in await store.ledger_rows(ledger, row_date)
            }
        canonical = _canonical(record)
        if canonical in seen[key]:
            continue  # this exact row is already in the table
        try:
            result = await ledgers.ledger_append(store, ledger, row_date, record)
        except ledgers.LedgerError as e:
            report.skip(f"{where}:{n}", str(e))
            continue
        if not result["appended"]:
            # The §1.7 one-snapshot-per-underlying-per-day skip. Not an error,
            # but a row in the source that is not a row in the store, which is
            # exactly what `skipped` is for.
            report.skip(f"{where}:{n}", str(result["reason"]))
            continue
        seen[key].add(canonical)
        report.bump(ledger)


async def _import_dated_ledgers(store: Store, src: Path, report: ImportReport) -> None:
    research = src / "research"
    for directory, ledger in DATED_LEDGERS:
        base = research / directory
        if not _is_dir(base):
            continue
        for path in _glob(base, "*.jsonl"):
            if not DATED.match(path.stem):
                report.skip(_rel(src, path), "filename is not a YYYY-MM-DD ledger")
                continue
            await _import_ledger_file(
                store, ledger, path, date.fromisoformat(path.stem), src, report
            )
    tombstones = research / "tombstones.jsonl"
    if _is_file(tombstones):
        await _import_ledger_file(store, "tombstones", tombstones, None, src, report)


async def _import_events(store: Store, src: Path, report: ImportReport) -> None:
    """`status/data/YYYY-MM-DD-events.jsonl` only.

    The other four v2 data kinds — orders, quotes, decisions, counterfactuals
    — have no table in the v3 schema and no validator to write them through,
    so they are left in place rather than forced into `events`.
    """
    base = src / "status" / "data"
    if not _is_dir(base):
        return
    for path in _glob(base, "*-events.jsonl"):
        stem = path.name[: -len("-events.jsonl")]
        if not DATED.match(stem):
            report.skip(_rel(src, path), "filename is not a YYYY-MM-DD events ledger")
            continue
        await _import_ledger_file(store, "events", path, date.fromisoformat(stem), src, report)


# --- evidence ----------------------------------------------------------------


async def _import_evidence(store: Store, src: Path, report: ImportReport) -> None:
    """`research/evidence/SYMBOL.jsonl`. Absent in the live store today — the
    scout has never written one — so the directory not existing is the normal
    case, not a finding."""
    base = src / "research" / "evidence"
    if not _is_dir(base):
        return
    for path in _glob(base, "*.jsonl"):
        symbol = path.stem
        where = _rel(src, path)
        rows = _read_jsonl(path, where, report)
        if not rows:
            continue
        seen = {_evidence_key(r) for r in await store.evidence_for(symbol)}
        for n, record in rows:
            # The date is the row's own `observed` stamp: v2 took it as an
            # argument and refused any row whose `observed` disagreed, and the
            # per-symbol file carries no other date to check it against.
            observed = record.get("observed")
            if not isinstance(observed, str) or not DATED.match(observed):
                report.skip(f"{where}:{n}", f"observed must be YYYY-MM-DD, got {observed!r}")
                continue
            key = _canonical(record)
            if key in seen:
                continue
            try:
                await ledgers.evidence_append(
                    store, symbol, date.fromisoformat(observed), record
                )
            except ledgers.LedgerError as e:
                report.skip(f"{where}:{n}", str(e))
                continue
            seen.add(key)
            report.evidence += 1


def _evidence_key(row: dict[str, Any]) -> str:
    """A stored evidence row, back in the shape the source line had, so the
    two compare equal. `symbol` and `date` are dropped: they are the file's
    name and the row's own `observed`, not fields the source line carried."""
    rebuilt = {k: row[k] for k in ("claim", "url", "source_type", "observed", "independence")}
    rebuilt.update(row.get("extra", {}))
    return _canonical(rebuilt)


# --- escalations -------------------------------------------------------------


def _validate_raise(record: dict[str, Any]) -> None:
    """`ledgers.escalation_raise`'s validation, minus the id and the insert.

    It is spelled out here rather than reused wholesale because that function
    computes the id it stores, and the whole point of this importer is that
    the v2 id — a `cksum` CRC-32, a different function from `zlib.crc32` —
    must be carried through unchanged or every score row orphans. Each check
    below still calls the same helper and reads the same constant that the
    tool path uses, so the rules have one definition even though the sequence
    is written twice.
    """
    for reserved in ledgers.RESERVED_ON_RAISE:
        # `in`, not a truth test: the v2 meta keys are stripped before this,
        # so anything left is the row claiming its own outcome.
        if reserved in record:
            raise ledgers.LedgerError(f"a raise may not carry {reserved!r}")
    ledgers._nonempty_str(record, "claim")
    direction = ledgers._nonempty_str(record, "direction")
    if direction not in ledgers.DIRECTIONS:
        raise ledgers.LedgerError(
            f"direction must be one of {ledgers.DIRECTIONS}, got {direction!r}"
        )
    ledgers._date_str(record.get("event_date"), "event_date")
    types = record.get("source_types")
    if not isinstance(types, list):
        raise ledgers.LedgerError("source_types must be a JSON array")
    distinct = {t for t in types if isinstance(t, str) and t in ledgers.BAR_SOURCE_TYPES}
    if len(distinct) < 2:
        raise ledgers.LedgerError(
            f"the bar is 2 distinct source types from {ledgers.BAR_SOURCE_TYPES}"
            f" (mainstream never counts); got {sorted(distinct)}"
        )


def _outcome(value: Any) -> ledgers.Outcome:
    if value not in ledgers.OUTCOMES:
        raise ledgers.LedgerError(f"outcome must be one of {ledgers.OUTCOMES}, got {value!r}")
    return cast(ledgers.Outcome, value)


async def _existing_scores(store: Store) -> dict[str, list[str]]:
    """Every score already filed, in row order, per id.

    Scoring is append-only and the LAST score per id wins, so an id may
    legitimately carry `wrong` then `right`. That makes "has this id been
    scored?" the wrong idempotency question — the right one is "is the Nth
    score in the file already the Nth score in the table?", which needs the
    whole sequence.
    """
    rows = await store.fetchall(
        "SELECT id, record_json FROM escalations WHERE kind='score' ORDER BY row_id"
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["id"], []).append(str(json.loads(r["record_json"]).get("outcome")))
    return out


async def _import_escalations(store: Store, src: Path, report: ImportReport) -> None:
    path = src / "research" / "escalations.jsonl"
    if not _is_file(path):
        return
    where = _rel(src, path)
    prior = await _existing_scores(store)
    filed: dict[str, int] = {}
    # File order matters: a score is only valid after its raise, and v2 wrote
    # both to one append-only file.
    for n, record in _read_jsonl(path, where, report):
        kind = record.get("kind")
        if kind == "raise":
            await _import_raise(store, record, f"{where}:{n}", report)
        elif kind == "score":
            await _import_score(store, record, f"{where}:{n}", prior, filed, report)
        else:
            report.skip(f"{where}:{n}", f"kind must be 'raise' or 'score', got {kind!r}")


async def _import_raise(
    store: Store, record: dict[str, Any], where: str, report: ImportReport
) -> None:
    try:
        eid = ledgers._nonempty_str(record, "id")
        symbol = ledgers._symbol(record.get("symbol"))
        raised = ledgers._date_str(record.get("raised"), "raised")
        claim = {k: v for k, v in record.items() if k not in {"id", "symbol", "raised", "kind"}}
        _validate_raise(claim)
    except ledgers.LedgerError as e:
        report.skip(where, str(e))
        return
    if await store.escalation_raise_exists(eid):
        return  # already imported
    await store.insert_escalation(
        {"id": eid, "kind": "raise", "symbol": symbol, "at": raised, "record": claim}
    )
    report.escalations += 1


async def _import_score(
    store: Store,
    record: dict[str, Any],
    where: str,
    prior: dict[str, list[str]],
    filed: dict[str, int],
    report: ImportReport,
) -> None:
    try:
        eid = ledgers._nonempty_str(record, "id")
        outcome = _outcome(record.get("outcome"))
        scored = ledgers._date_str(record.get("scored"), "scored")
    except ledgers.LedgerError as e:
        report.skip(where, str(e))
        return
    seen = filed.get(eid, 0)
    already = prior.get(eid, [])
    if seen < len(already) and already[seen] == outcome:
        filed[eid] = seen + 1
        return  # this score is already the Nth row under this id
    try:
        await ledgers.escalation_score(store, eid, outcome, date.fromisoformat(scored))
    except ledgers.LedgerError as e:
        # "no such raise: ..." — a score whose prediction never cleared the
        # bar, or was never written. It is not filed under a raise that does
        # not exist, and it is not silently dropped either.
        report.skip(where, str(e))
        return
    filed[eid] = seen + 1
    report.escalations += 1


# --- sectors -----------------------------------------------------------------


async def _import_sectors(store: Store, src: Path, report: ImportReport) -> None:
    path = src / "research" / "sectors.tsv"
    if not _is_file(path):
        return
    where = _rel(src, path)
    text = _read_text(path, where, report)
    if text is None:
        return
    existing = {r["symbol"]: (r["sector"], r["date"]) for r in await store.sectors()}
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) != 3:
            report.skip(f"{where}:{n}", f"expected 3 tab-separated columns, got {len(fields)}")
            continue
        symbol, sector, stamp = fields
        if existing.get(symbol) == (sector, stamp):
            continue
        # One row at a time, not one batch: `sector_write` refuses a whole
        # batch when any row is bad, which is right for a live tag pass and
        # wrong for a migration, where one unrecognised legacy tag must not
        # cost the other 3,000 names their sector.
        try:
            await ledgers.sector_write(
                store, [{"symbol": symbol, "sector": sector}], _stamp(stamp)
            )
        except ledgers.LedgerError as e:
            report.skip(f"{where}:{n}", str(e))
            continue
        existing[symbol] = (sector, stamp)
        report.sectors += 1


def _stamp(value: str) -> date:
    if not DATED.match(value):
        raise ledgers.LedgerError(f"date must be YYYY-MM-DD, got {value!r}")
    return date.fromisoformat(value)


# --- universe ----------------------------------------------------------------


def _is_universe_block(block: list[str] | None) -> bool:
    return bool(block) and block is not None and block[0].startswith("symbol\t")


def _fenced_tsv(text: str) -> list[str] | None:
    """The fenced block whose header line is the ten-column TSV header.

    Searched for by its header rather than taken as "the first fence": the
    document carries prose above it and may carry another fence later, and a
    block that is not the universe table must not be parsed as one.

    An unterminated fence at end of file still counts. That is not leniency
    for its own sake -- it is the live store's actual shape: the improvised
    2026-08-29 sweep wrote the opening fence and 500 rows and never closed it.
    Refusing that file would drop the whole working universe over a missing
    line, while every row inside it is still checked column by column below.
    """
    block: list[str] | None = None
    inside = False
    for line in text.splitlines():
        if line.startswith(FENCE):
            if inside and _is_universe_block(block):
                return block
            inside = not inside
            block = [] if inside else None
            continue
        if inside and block is not None:
            block.append(line)
    return block if inside and _is_universe_block(block) else None


def _universe_row(fields: list[str]) -> dict[str, Any]:
    return {
        "symbol": fields[0],
        "price": Decimal(fields[1]),
        "adv10": Decimal(fields[2]),
        "dollar_vol": Decimal(fields[3]),
        "pct_from_52wk_high": Decimal(fields[4]),
        "optionable": fields[5] == "true",
        "leverage": Decimal(fields[6]),
        # An empty last_earnings is normal: not every listed name has one, and
        # `cohort.sh` reads the empty string as "no estimate", not as an error.
        "last_earnings": fields[7],
        "is_etf": fields[8] == "true",
        # `-` is "the payload carried no usable high/low", which is not zero.
        "session_range_pct": None if fields[9] == "-" else Decimal(fields[9]),
        # v2 kept descriptions in `universe-names.tsv`, which the improvised
        # 2026-08-29 sweep dropped. Empty is the honest record of that.
        "description": "",
    }


async def _import_universe(store: Store, src: Path, report: ImportReport) -> None:
    path = src / "research" / "universe.md"
    if not _is_file(path):
        return
    where = _rel(src, path)
    text = _read_text(path, where, report)
    if text is None:
        return
    stamp = ASSEMBLED.search(text)
    if stamp is None:
        report.skip(where, "no 'Assembled:' line, so the sweep has no asof date")
        return
    asof = date.fromisoformat(stamp.group(1))
    block = _fenced_tsv(text)
    if block is None:
        report.skip(where, "no fenced block carrying the ten-column universe header")
        return
    header = block[0].split("\t")
    if tuple(header) != UNIVERSE_COLUMNS:
        report.skip(where, f"universe columns moved: {header}")
        return
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(block[1:], start=2):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(UNIVERSE_COLUMNS):
            report.skip(
                f"{where} (universe row {n})",
                f"expected {len(UNIVERSE_COLUMNS)} columns, got {len(fields)}",
            )
            continue
        try:
            rows.append(_universe_row(fields))
        except InvalidOperation:
            report.skip(f"{where} (universe row {n})", "a numeric column is not a number")
    if not rows:
        return
    # Every row in the file qualified: v2's `universe.md` is written from the
    # gated pass, so a name that is in it is a name that cleared the gates.
    for row in rows:
        row["qualified"] = True
    if await store.universe_asof() == asof:
        stored = await store.universe_rows(qualified_only=False)
        if {r["symbol"] for r in stored} == {r["symbol"] for r in rows}:
            return  # this sweep is already the store's universe
    report.universe = await store.replace_universe(asof, rows)


# --- the whole import ---------------------------------------------------------


async def import_research(store: Store, docs: DocStore, src: Path) -> ImportReport:
    """Import the v2 store rooted at `src` (the directory holding `research/`
    and `status/`). Safe to run twice; the second run reports zeroes."""
    report = ImportReport()
    await _import_documents(docs, src, report)
    await _import_dated_ledgers(store, src, report)
    await _import_events(store, src, report)
    await _import_evidence(store, src, report)
    await _import_escalations(store, src, report)
    await _import_sectors(store, src, report)
    await _import_universe(store, src, report)
    return report
