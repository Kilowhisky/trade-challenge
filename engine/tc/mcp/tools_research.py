"""The research role's tool surface. Bodies are thin on purpose: every rule
lives in tc/research/, where it can be tested without a server.

One mapping matters here and nowhere else -- which failures the model is meant
to READ. A LedgerError, a DocValidationError or a DocCasMismatch is an
anticipated refusal carrying an actionable reason ("source_type must be one of
...", "re-read, merge onto the fresh copy, retry once"), and ToolError is the
shape that delivers it: isError=True, the message visible to the model, no
traceback in the log. Everything else is a crash and stays one -- a crash shows
the model only "Error executing tool <name>", which is correct, because it has
nothing to act on.

Two shapes are deliberate and are not defects to tidy away:

* **Empty is a result, not an error.** An empty cohort between earnings
  seasons, a document that has never been written, a second OI snapshot for a
  name already snapshotted today -- each returns a value the caller can branch
  on. Raising there would make the model treat an honest "nothing" as a failure
  and retry it.
* **`sector_write` has only the batch form.** v2 had a single-row form too, and
  having two forms is what gave it two validation orderings. One row is
  `len(rows) == 1`.

Nothing in this module can reach the broker or an order path: it closes over
`deps.store` and `deps.docs` and imports no order module.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from tc.mcp.registry import Role
from tc.research import ledgers
from tc.research.cohort import CohortRow
from tc.research.cohort import cohort as compute_cohort
from tc.research.docs import (
    DocCasMismatch,
    DocKind,
    DocValidationError,
    DocView,
    DocWrite,
)
from tc.research.ledgers import LedgerError, Outcome
from tc.store.db import LEDGER_TABLES

if TYPE_CHECKING:  # pragma: no cover -- import-cycle guard, not a runtime need
    # `tc.mcp.server` is the module that will eventually import THIS one to
    # wire the registrar. Keeping the dependency annotation-only means that
    # wiring cannot become an import cycle later.
    from tc.mcp.server import McpDeps

# The refusals the model is meant to read. Listed once, caught once, so a new
# writer cannot quietly acquire a fourth failure mode that reaches the caller
# as a traceback.
REFUSALS = (LedgerError, DocValidationError, DocCasMismatch)

LedgerName = Literal["screen", "iv", "oi", "tombstones", "events"]
if set(get_args(LedgerName)) != set(LEDGER_TABLES):  # pragma: no cover
    # A Literal is what puts the five names in the tool's JSON schema, where
    # the model can read them; this keeps that copy from drifting from the
    # dispatch table the writers and the importer share.
    raise RuntimeError(f"LedgerName {get_args(LedgerName)} drifted from {sorted(LEDGER_TABLES)}")

SYMBOL = Annotated[str, Field(max_length=10, description="Ticker, e.g. CSX")]
DATE = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")]
OPT_DATE = Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD")]


def _guard(exc: Exception) -> ToolError:
    """Wrap an anticipated refusal so its reason survives the trip.

    `from exc` at the raise site keeps the original in the log for an operator
    while the model sees only the sentence written for it.
    """
    return ToolError(str(exc))


def _date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as e:
        raise ToolError(f"date must be YYYY-MM-DD, got {value!r}") from e


def _opt_date(value: str | None) -> dt.date | None:
    return None if value is None else _date(value)


def _decimal(value: str, field: str) -> Decimal:
    """Prices cross this boundary as strings, both ways. A float round-trip
    moves the number, and a tombstone is the record of a price that was
    refused -- the wrong price refused is a different fact."""
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise ToolError(f"{field} must be a decimal string, got {value!r}") from e


# --- argument and return models ---------------------------------------
# All extra="forbid": a misspelled field in a model's output is a mistake to
# report, never a key to accept and silently drop.


class SectorRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    sector: str


class Appended(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appended: bool
    reason: str | None = None
    detail: str = ""
    # The evidence ledger's row id. Null for the appenders that have no row of
    # their own to name (`ledger_append`, `tombstone`), and the value an
    # escalation's `evidence_ids` is built from for `evidence_append`.
    id: int | None = None


class Raised(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str


class Scored(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    outcome: str


class SectorWritten(BaseModel):
    model_config = ConfigDict(extra="forbid")
    written: int
    new: int
    retired: int


class Rows(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict[str, Any]]


class Symbols(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str]


class NameRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    description: str


class NamesPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[NameRow]
    total: int


class CohortOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[CohortRow]
    date: str


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exists: bool
    acknowledged: bool
    body: str


def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    """Add every research tool to `server`.

    `role` is unused and required. Every registrar shares the
    `ToolRegistrar` shape `(server, deps, role)` so the wiring can hold
    them in one list per role; this module happens to register the same
    set whatever the role is, and only `build_servers` decides which roles
    it is called for. A registrar with its own narrower signature would
    have to be adapted at every call site, and an adapter is a place a
    role can be dropped without anything noticing.
    """
    del role
    store = deps.store
    docs = deps.docs

    # --- writers ------------------------------------------------------

    @server.tool(
        name="evidence_append",
        description=(
            "Record one dated observation about one name. Refused unless claim, url, "
            "source_type, observed and independence are all present, source_type is one "
            "of the six known kinds, and observed equals the date argument. Returns the "
            "row's `id` — use those ids, as strings, for an escalation's evidence_ids."
        ),
    )
    async def evidence_append_tool(
        symbol: SYMBOL, date: DATE, record: dict[str, Any]
    ) -> Appended:
        try:
            out = await ledgers.evidence_append(store, symbol, _date(date), record)
        except REFUSALS as e:
            raise _guard(e) from e
        return Appended(appended=True, detail=str(out.get("symbol", "")), id=out["id"])

    @server.tool(
        name="escalation_raise",
        description=(
            "Raise one prediction. The bar is 2 distinct non-mainstream source types; a "
            "raise may not carry its own outcome. Returns the escalation id to score later."
        ),
    )
    async def escalation_raise_tool(symbol: SYMBOL, date: DATE, record: dict[str, Any]) -> Raised:
        try:
            out = await ledgers.escalation_raise(store, symbol, _date(date), record)
        except REFUSALS as e:
            raise _guard(e) from e
        return Raised(id=str(out["id"]))

    @server.tool(
        name="escalation_score",
        description=(
            "Score a previously raised prediction right/wrong/void. Append-only: this "
            "never rewrites the raise, and the last score for an id wins."
        ),
    )
    async def escalation_score_tool(
        escalation_id: Annotated[str, Field(max_length=64)], outcome: Outcome, date: DATE
    ) -> Scored:
        try:
            out = await ledgers.escalation_score(store, escalation_id, outcome, _date(date))
        except REFUSALS as e:
            raise _guard(e) from e
        return Scored(id=str(out["id"]), outcome=str(out["outcome"]))

    @server.tool(
        name="sector_write",
        description=(
            "Tag symbols into the scout sectors. Batch only -- one row is a list of one. "
            "Every row is validated before any row is written, so a bad row writes nothing."
        ),
    )
    async def sector_write_tool(rows: list[SectorRow], date: DATE) -> SectorWritten:
        try:
            out = await ledgers.sector_write(
                store, [r.model_dump() for r in rows], _date(date)
            )
        except REFUSALS as e:
            raise _guard(e) from e
        return SectorWritten(
            written=int(out["written"]), new=int(out["new"]), retired=int(out["retired"])
        )

    @server.tool(
        name="ledger_append",
        description=(
            "Append one record to a ledger (screen, iv, oi, tombstones, events). A second "
            "oi snapshot for a symbol on a day returns appended=false with a reason -- "
            "that is a skip, not a failure."
        ),
    )
    async def ledger_append_tool(
        name: LedgerName, date: DATE, record: dict[str, Any]
    ) -> Appended:
        try:
            out = await ledgers.ledger_append(store, name, _date(date), record)
        except REFUSALS as e:
            raise _guard(e) from e
        return Appended(appended=bool(out["appended"]), reason=out.get("reason"))

    @server.tool(
        name="tombstone",
        description=(
            "Record one name refused by one gate, with the price it was refused at. "
            "Prices are strings so they cannot be moved by a float round-trip."
        ),
    )
    async def tombstone_tool(
        symbol: SYMBOL,
        date: DATE,
        gate: str,
        reason: str,
        ref_price: Annotated[str, Field(description="Decimal as a string, e.g. '34.55'")],
        hypo_qty: int | None = None,
        hypo_stop: Annotated[str | None, Field(description="Decimal as a string")] = None,
    ) -> Appended:
        price = _decimal(ref_price, "ref_price")
        stop = None if hypo_stop is None else _decimal(hypo_stop, "hypo_stop")
        try:
            out = await ledgers.tombstone(
                store, symbol, _date(date), gate, reason, price, hypo_qty, stop
            )
        except REFUSALS as e:
            raise _guard(e) from e
        return Appended(appended=bool(out["appended"]), reason=out.get("reason"))

    @server.tool(
        name="doc_write",
        description=(
            "Replace one research document whole. The body is validated (first line, "
            "banners, minimum length) before anything is written. For candidates, pass "
            "expect_last_pass with the stamp you read; a mismatch means someone wrote "
            "since -- re-read, merge, retry once."
        ),
    )
    async def doc_write_tool(
        kind: DocKind,
        body: str,
        date: OPT_DATE = None,
        expect_last_pass: str | None = None,
    ) -> DocWrite:
        try:
            return await docs.replace(kind, body, _opt_date(date), expect_last_pass)
        except REFUSALS as e:
            raise _guard(e) from e

    # --- readers ------------------------------------------------------

    @server.tool(
        name="doc_read",
        description=(
            "Read one research document whole, with its 'Last pass:' / 'Verified as of:' "
            "stamps surfaced as fields. A document that has never been written reads as "
            "exists=false with an empty body, which is not an error."
        ),
    )
    async def doc_read_tool(kind: DocKind, date: OPT_DATE = None) -> DocView:
        try:
            return await docs.read(kind, _opt_date(date))
        except REFUSALS as e:
            raise _guard(e) from e

    @server.tool(
        name="evidence_read",
        description=(
            "Every dated observation recorded for one name, oldest first, each with its "
            "row `id`. This is the delta baseline: read it before searching, so today's "
            "pass records what is new. Cite those ids in an escalation's evidence_ids."
        ),
    )
    async def evidence_read_tool(symbol: SYMBOL) -> Rows:
        return Rows(rows=await store.evidence_for(symbol))

    @server.tool(
        name="escalations_read",
        description=(
            "One row per raised prediction, with the latest score already reduced into "
            "latest_outcome. Omit symbol for every name."
        ),
    )
    async def escalations_read_tool(symbol: SYMBOL | None = None) -> Rows:
        return Rows(rows=await store.escalations(symbol))

    @server.tool(
        name="ledger_read",
        description=(
            "Rows from one ledger. Pass date for one day, or latest_before to get the "
            "most recent day strictly before it -- the prior-day side of the oi diff."
        ),
    )
    async def ledger_read_tool(
        name: LedgerName, date: OPT_DATE = None, latest_before: OPT_DATE = None
    ) -> Rows:
        return Rows(
            rows=await store.ledger_rows(name, _opt_date(date), _opt_date(latest_before))
        )

    @server.tool(
        name="sectors_read",
        description=(
            "Every symbol's sector tag. An empty list means the weekly tagger has not run "
            "yet -- that is an answer, and the caller decides what to do about it."
        ),
    )
    async def sectors_read_tool() -> Rows:
        return Rows(rows=await store.sectors())

    @server.tool(
        name="cohort",
        description=(
            "The active earnings cohort for a date: qualified, in-scope-sector names whose "
            "estimated next print falls inside the scout entry window. Empty is correct "
            "between seasons and before the first sweep."
        ),
    )
    async def cohort_tool(date: DATE) -> CohortOut:
        rows = await compute_cohort(store, deps.rules, _date(date))
        return CohortOut(rows=rows, date=date)

    @server.tool(
        name="universe_symbols",
        description=(
            "Every qualified symbol from the newest weekly sweep, sorted. The whole "
            "working universe in one call."
        ),
    )
    async def universe_symbols_tool() -> Symbols:
        rows = await store.universe_rows(qualified_only=True)
        return Symbols(symbols=[str(r["symbol"]) for r in rows])

    @server.tool(
        name="universe_names_page",
        description=(
            "One page of the qualified universe as symbol + company name, for tagging. "
            "total is the whole universe, so the caller knows when it has seen it all."
        ),
    )
    async def universe_names_page_tool(
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=1000)] = 200,
    ) -> NamesPage:
        rows = await store.universe_rows(qualified_only=True)
        page = rows[offset : offset + limit]
        return NamesPage(
            rows=[
                NameRow(symbol=str(r["symbol"]), description=str(r["description"]))
                for r in page
            ],
            total=len(rows),
        )

    @server.tool(
        name="alert_read",
        description=(
            "Whether an unacknowledged alert is open. An open alert means closing-only "
            "posture: no new research-driven entries until Chris answers."
        ),
    )
    async def alert_read_tool() -> Alert:
        # `open_alerts()` returns only rows with acked_at IS NULL, so `exists`
        # and `acknowledged` are two readings of one fact here. Both are kept:
        # the prompts branch on `acknowledged`, and an acked-alert history
        # accessor would change only this body.
        rows = await store.open_alerts()
        return Alert(
            exists=bool(rows),
            acknowledged=not rows,
            body="\n".join(f"{a.kind}: {a.message}" for a in rows),
        )
