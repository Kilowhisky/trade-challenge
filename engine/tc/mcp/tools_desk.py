"""The trading desk's tool surface (trading-desk design §5, §6, §13).

Bodies are thin: every rule lives in tc/desk/, where it is tested without a
server. This layer does three things only:

* resolves WHO is calling from the runner-stamped `X-TC-Job` request header
  (`_caller_job`), falling back to the engine's own ActiveJob (`deps.active`)
  when no header is present -- never from an argument. An analyst cannot file
  under another analyst's name, and a tool reached outside a scheduled run is
  refused;
* turns a `DeskRefused` into a `ToolError` whose sentence the model reads;
* turns a broker fault into one line naming its class, never its message.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from tc.broker.client import BrokerError, BrokerUnauthorized
from tc.desk.briefing import Briefing, build_briefing
from tc.desk.models import JOB_ANALYST, PM_JOBS, Analyst, DeskRefused, Direction, Funding
from tc.desk.options import OptionCandidate, fetch_candidates
from tc.desk.paper import book_state
from tc.desk.pitches import (
    EvidenceItem,
    PitchIn,
    last_close,
    submit_pitch,
    tradeable_symbols,
    withdraw_pitch,
)
from tc.desk.pm import (
    CallIn,
    CallOut,
    PaperBookOut,
    PitchesOut,
    PmContext,
    extend_call,
    paper_book_view,
    pitches_view,
    request_exit_for,
    submit_call,
    tighten_call,
)
from tc.desk.scorecard import Scorecard, build_scorecard
from tc.desk.scoring import RecordRow, analyst_record
from tc.desk.sizing import conviction_pct
from tc.mcp.registry import Role
from tc.rules.arith import cap_dollars

if TYPE_CHECKING:  # pragma: no cover -- import-cycle guard
    from tc.mcp.server import McpDeps

# `Context` must be a REAL (not TYPE_CHECKING-only) import: FastMCP finds the
# context parameter with `typing.get_type_hints`, which has to resolve this
# name out of the tool functions' `__globals__` at registration time, and
# `from __future__ import annotations` means every annotation below is a
# string until then. The three type arguments are the ones `FastMCP.get_context`
# itself returns (server.py `get_context`); `Any` stands in for the lifespan
# type this module has no reason to name.
DeskContext = Context[ServerSession, Any, Request]


class PitchOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    session: str
    symbol: str
    direction: str
    target: str
    invalidation: str
    horizon_days: int


class Withdrawn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    withdrawn: int


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[RecordRow]


@contextlib.contextmanager
def refusals() -> Iterator[None]:
    try:
        yield
    except DeskRefused as e:
        raise ToolError(str(e)) from e
    except BrokerUnauthorized as e:
        raise ToolError("broker blind: token absent/dead") from e
    except BrokerError as e:
        raise ToolError(f"broker read failed: {type(e).__name__}") from e


def _caller_job(deps: McpDeps, ctx: DeskContext | None) -> str | None:
    """Whose job this call belongs to.

    The runner stamps the job it is running as an `X-TC-Job` header on every
    MCP call (runner/tc_runner/app.py `mcp_servers`), and a call that came
    over MCP answers from that header ALONE, because the caller and the
    engine serving this request are not always the same process: `tc run
    --once analyst_technical` dials the SERVING engine's HTTP mount from a
    separate one-off process, whose run that engine's `deps.active` never
    saw.

    A live request with no header is refused (None), never attributed to
    whatever job this engine happens to be running: a second MCP client
    holding the research bearer -- a laptop session tunnelled to the server,
    say -- calling `pitch_submit` during the evening chain would otherwise be
    filed as the running analyst's pitch. The same goes for a context with
    no request behind it: nothing served over MCP arrives that way.

    `deps.active.name` (set only around the calls THIS engine dispatches
    itself, tc/main.py `_job_claude`) answers only when there is no context
    at all -- a direct in-process `_tool_manager.call_tool`, which is how
    every desk-tool unit test drives the tools.
    """
    if ctx is None:
        return deps.active.name
    try:
        request = ctx.request_context.request
    except ValueError:
        return None
    if request is None:
        return None
    job = request.headers.get("x-tc-job")
    return str(job) if job else None


def _analyst(deps: McpDeps, ctx: DeskContext | None) -> Analyst:
    a = JOB_ANALYST.get(_caller_job(deps, ctx) or "")
    if a is None:
        raise ToolError(
            "no analyst job is running: the desk's analyst tools answer only inside a"
            " scheduled analyst run"
        )
    return a


def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    if role == "research":
        _register_analyst(server, deps)
    elif role == "decide":
        _register_pm(server, deps)


def _register_analyst(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(
        name="briefing",
        description=(
            "Your briefing: the engine-computed screen rows for your approach, "
            "context rows, movers (news), your own open pitches and your last 10 "
            "resolved pitches with how they turned out."
        ),
    )
    async def briefing(ctx: DeskContext | None = None) -> Briefing:
        a = _analyst(deps, ctx)
        return await build_briefing(deps.store, deps.broker, a, deps.settings.desk)

    @server.tool(
        name="pitch_submit",
        description=(
            "File one pitch: a dated, falsifiable prediction. Prices are decimal "
            "strings. Up: target > last > invalidation; down: the reverse; both "
            "within 30% of the last price. horizon_days 2-20 trading days. The "
            "engine stamps the reference price (next session's open) -- never you."
        ),
    )
    async def pitch_submit(
        symbol: Annotated[str, Field(max_length=10)],
        direction: Direction,
        thesis: Annotated[str, Field(min_length=10, max_length=400)],
        evidence: Annotated[list[EvidenceItem], Field(min_length=1, max_length=5)],
        target: str,
        invalidation: str,
        horizon_days: int,
        conviction: Annotated[int, Field(ge=1, le=5)],
        benchmark: str,
        ctx: DeskContext | None = None,
    ) -> PitchOut:
        a = _analyst(deps, ctx)
        sym = symbol.strip().upper()
        with refusals():
            last = await last_close(deps.store, sym)
            if last is None:
                quotes = await deps.broker.quotes([sym])
                if sym not in quotes:
                    raise DeskRefused(f"no price for {sym}: not in the bars set and no quote")
                last = quotes[sym].last
            pin = PitchIn(symbol=sym, direction=direction, thesis=thesis, evidence=evidence,
                          target=target, invalidation=invalidation, horizon_days=horizon_days,
                          conviction=conviction, benchmark=benchmark)
            p = await submit_pitch(
                deps.store, analyst=a, pin=pin, now=deps.clock(), last=Decimal(last),
                tradeable=await tradeable_symbols(deps.store, deps.settings.desk),
                rules=deps.rules, is_trading_day=deps.trading_day,
            )
        return PitchOut(id=p.id, session=p.session.isoformat(), symbol=p.symbol,
                        direction=p.direction, target=str(p.target),
                        invalidation=str(p.invalidation), horizon_days=p.horizon_days)

    @server.tool(
        name="pitch_withdraw",
        description="Withdraw one of your own pitches, only before its session opens.",
    )
    async def pitch_withdraw(pitch_id: int, ctx: DeskContext | None = None) -> Withdrawn:
        a = _analyst(deps, ctx)
        with refusals():
            await withdraw_pitch(deps.store, analyst=a, pitch_id=pitch_id, now=deps.clock())
        return Withdrawn(withdrawn=pitch_id)

    @server.tool(name="my_record", description="Your last 10 resolved pitches, newest first.")
    async def my_record(ctx: DeskContext | None = None) -> Record:
        return Record(rows=await analyst_record(deps.store, _analyst(deps, ctx)))


class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premium_cap: str
    rows: list[OptionCandidate]


class Tightened(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    invalidation: str


class ExitQueued(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    book: str


def _pm(deps: McpDeps, ctx: DeskContext | None, *, making_calls: bool = False) -> PmContext:
    name = _caller_job(deps, ctx) or ""
    if name not in PM_JOBS:
        raise ToolError(
            "no PM job is running: the desk's PM tools answer only inside a scheduled PM run"
        )
    if making_calls and name != "pm":
        raise ToolError("no new calls at midday: exit, tighten or hold only (spec §4)")
    return PmContext(store=deps.store, broker=deps.broker, rules=deps.rules,
                     desk=deps.settings.desk, reserve=deps.settings.engine.reserve_usd,
                     now=deps.clock())


def _register_pm(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(name="paper_book", description=(
        "The desk's paper book: equity, cash and open premium (both already net of pending"
        " proposals at their max entry price, which is what the caps size against), positions"
        " with their call's target/invalidation/horizon, pending proposals, and the real"
        " account's legacy positions with any legacy call attached."))
    async def paper_book(ctx: DeskContext | None = None) -> PaperBookOut:
        return await paper_book_view(_pm(deps, ctx))

    @server.tool(name="pitches_read", description=(
        "Every open pitch from every analyst, plus each analyst's resolved count and mean"
        " excess return."))
    async def pitches_read(ctx: DeskContext | None = None) -> PitchesOut:
        return await pitches_view(_pm(deps, ctx))

    @server.tool(
        name="scorecard",
        description="The desk scorecard (spec §7.3) and checkpoint status.",
    )
    async def scorecard(ctx: DeskContext | None = None) -> Scorecard:
        pctx = _pm(deps, ctx)
        return await build_scorecard(pctx.store, pctx.rules, pctx.desk, pctx.today)

    @server.tool(name="option_candidates", description=(
        "Up to 3 live contracts that clear every manual §3.2 floor for this direction and"
        " horizon, within this conviction's premium cap. Fund an option call ONLY with one"
        " of these symbols."))
    async def option_candidates(symbol: str, direction: Direction, horizon_days: int,
                                conviction: Annotated[int, Field(ge=1, le=5)],
                                ctx: DeskContext | None = None) -> Candidates:
        pctx = _pm(deps, ctx)
        with refusals():
            cap = cap_dollars(conviction_pct("option", conviction, pctx.rules),
                              (await book_state(pctx.store)).equity)
            rows = await fetch_candidates(pctx.broker, symbol.strip().upper(), direction,
                                          horizon_days, cap, pctx.rules, pctx.today)
        return Candidates(premium_cap=str(cap), rows=rows)

    @server.tool(name="call_submit", description=(
        "Make one call (max 5 new a day): adopt a pitch by pitch_id or originate your own."
        " Prices are decimal strings. funding: none | shares (up only; needs"
        " max_entry_price) | call | put (needs option_symbol from option_candidates)."
        " Conviction 1-2 is never funded. legacy=true records a view on a real-account"
        " position (funding none). The engine stamps the reference from a live quote."))
    async def call_submit(
        symbol: Annotated[str, Field(max_length=10)], direction: Direction,
        thesis: Annotated[str, Field(min_length=10, max_length=400)], target: str,
        invalidation: str, horizon_days: int, conviction: Annotated[int, Field(ge=1, le=5)],
        benchmark: str, pitch_id: int | None = None, funding: Funding = "none",
        max_entry_price: str | None = None, option_symbol: str | None = None,
        legacy: bool = False, ctx: DeskContext | None = None,
    ) -> CallOut:
        pctx = _pm(deps, ctx, making_calls=True)
        with refusals():
            return await submit_call(pctx, CallIn(
                pitch_id=pitch_id, symbol=symbol, direction=direction, thesis=thesis,
                target=target, invalidation=invalidation, horizon_days=horizon_days,
                conviction=conviction, benchmark=benchmark, funding=funding,
                max_entry_price=max_entry_price, option_symbol=option_symbol, legacy=legacy,
            ))

    @server.tool(name="call_extend", description=(
        "Extend a funded call that reached its horizon at the last close, ONCE: opens a new"
        " call with new levels and horizon and keeps the paper position. Not at midday (spec"
        " §6): exit, tighten or hold only."))
    async def call_extend(call_id: int, target: str, invalidation: str, horizon_days: int,
                          thesis: Annotated[str, Field(min_length=10, max_length=400)],
                          ctx: DeskContext | None = None) -> CallOut:
        # An extension inserts a new call row exactly as call_submit does, and
        # spec §6 draws the midday line at "no new calls" -- extending is one.
        pctx = _pm(deps, ctx, making_calls=True)
        with refusals():
            return await extend_call(pctx, call_id, target=target, invalidation=invalidation,
                                     horizon_days=horizon_days, thesis=thesis)

    @server.tool(name="call_tighten", description=(
        "Move a held call's invalidation toward the price (never away). The paper stop follows."))
    async def call_tighten(call_id: int, invalidation: str,
                           ctx: DeskContext | None = None) -> Tightened:
        pctx = _pm(deps, ctx)
        with refusals():
            new = await tighten_call(pctx, call_id, invalidation)
        return Tightened(call_id=call_id, invalidation=str(new))

    @server.tool(name="exit_request", description=(
        "Exit every paper position on this underlying at the next desk_watch; on a legacy"
        " real-account position, posts a recommendation for Chris instead."))
    async def exit_request(symbol: str,
                           reason: Annotated[str, Field(min_length=5, max_length=300)],
                           ctx: DeskContext | None = None) -> ExitQueued:
        pctx = _pm(deps, ctx)
        with refusals():
            where = await request_exit_for(pctx, symbol, reason)
        return ExitQueued(symbol=symbol.strip().upper(), book=where)
