"""The trading desk's tool surface (trading-desk design §5, §6, §13).

Bodies are thin: every rule lives in tc/desk/, where it is tested without a
server. This layer does three things only:

* resolves WHO is calling from the running job (`deps.active`), never from
  an argument -- an analyst cannot file under another analyst's name, and a
  tool reached outside a scheduled run is refused;
* turns a `DeskRefused` into a `ToolError` whose sentence the model reads;
* turns a broker fault into one line naming its class, never its message.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import BrokerError, BrokerUnauthorized
from tc.desk.briefing import Briefing, build_briefing
from tc.desk.models import JOB_ANALYST, Analyst, DeskRefused, Direction
from tc.desk.pitches import (
    EvidenceItem,
    PitchIn,
    last_close,
    submit_pitch,
    tradeable_symbols,
    withdraw_pitch,
)
from tc.desk.scoring import RecordRow, analyst_record
from tc.mcp.registry import Role

if TYPE_CHECKING:  # pragma: no cover -- import-cycle guard, as in tools_research
    from tc.mcp.server import McpDeps


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


def _analyst(deps: McpDeps) -> Analyst:
    a = JOB_ANALYST.get(deps.active.name or "")
    if a is None:
        raise ToolError(
            "no analyst job is running: the desk's analyst tools answer only inside a"
            " scheduled analyst run"
        )
    return a


def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    if role == "research":
        _register_analyst(server, deps)


def _register_analyst(server: FastMCP, deps: McpDeps) -> None:
    @server.tool(
        name="briefing",
        description=(
            "Your briefing: the engine-computed screen rows for your approach, "
            "context rows, movers (news), your own open pitches and your last 10 "
            "resolved pitches with how they turned out."
        ),
    )
    async def briefing() -> Briefing:
        a = _analyst(deps)
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
    ) -> PitchOut:
        a = _analyst(deps)
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
    async def pitch_withdraw(pitch_id: int) -> Withdrawn:
        a = _analyst(deps)
        with refusals():
            await withdraw_pitch(deps.store, analyst=a, pitch_id=pitch_id, now=deps.clock())
        return Withdrawn(withdrawn=pitch_id)

    @server.tool(name="my_record", description="Your last 10 resolved pitches, newest first.")
    async def my_record() -> Record:
        return Record(rows=await analyst_record(deps.store, _analyst(deps)))
