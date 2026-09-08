"""The read tools: the clock, the market, the store's own numbers.

`register(server, deps, role)` adds every one of them to a role except `book`,
which is the held-position view and stays on `decide` (0c-writers-contract.md
§4.3: the research roles have never had account tools). Nothing is registered
at import: the wiring calls `register` explicitly, the same way
`tools_research` is wired, so importing the module can never widen a server's
surface as a side effect.

Three properties hold for every tool in this module, and they are the reason
it is a module rather than a handful of lambdas:

* **Every return is a pydantic model.** A `-> dict` return makes FastMCP send
  `structuredContent=None` and the model is left parsing prose
  (0c-sdk-facts.md §3.4). Money is rendered as a *string*, never a float: a
  Decimal that round-trips through a float puts 48.989999999999995 in front of
  a rule check.
* **Every broker fault is a `ToolError` carrying one line.** FastMCP returns a
  `ToolError` to the caller as an ordinary `isError` result with the message
  intact and logs it at INFO; any other exception is a crash the model sees as
  "Error executing tool <name>". The line names the failure class only —
  never the exception text, which for a real 401 is a Schwab response body and
  for a network fault can carry a URL with credentials in the query string.
* **Nothing here is unbounded.** `quotes` caps its symbol list and
  `price_history` its day count, because the cost of a read tool is the
  context it spends, and the model cannot see that cost before it pays it.

The compact `QuoteOut` is the whole point of the two-tier universe design:
the fundamentals block (avg10DaysVolume, leverage factor, last earnings) is
what `universe.md` is for, and it does not travel through a per-symbol quote.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from datetime import UTC, date, time
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import BrokerError, BrokerUnauthorized, ContractType, MoverDirection
from tc.broker.models import Expiration, Instrument, Mover, OptionChainView, Position
from tc.mcp.registry import Role
from tc.mcp.server import McpDeps

ET = ZoneInfo("America/New_York")

# The standard NYSE regular session, in ET. Not a rule parameter and not a
# trading-day claim: it is the fallback window reported when the broker's
# payload carries none, which is every closed day and every after-hours read
# of an open one. `is_trading_day` is the separate answer to "is it open".
RTH_START = time(9, 30)
RTH_END = time(16, 0)

MAX_SYMBOLS = 50
MAX_HISTORY_DAYS = 400
MAX_STRIKE_COUNT = 50

Projection = Literal[
    "symbol-search", "symbol-regex", "desc-search", "desc-regex", "search", "fundamental"
]


class Now(BaseModel):
    """The Eastern date and time, from the engine's clock.

    Every prompt calls this first and none of them read the machine clock: the
    laptop runs Pacific, the server runs UTC, and every rule in the manual is
    stated in Eastern."""

    model_config = ConfigDict(extra="forbid")
    date: str
    time_et: str
    iso_utc: str
    tz: str


class Hours(BaseModel):
    """Two separate facts about one date.

    `is_trading_day` is the only claim about whether the market opens; the
    window is always answered, because a caller planning an order needs the
    session bounds on a closed-day read too. When the broker reports no
    window -- every closed day, and every after-hours read of an open one --
    the standard session is reported, which is not adjusted for an early
    close.
    """

    model_config = ConfigDict(extra="forbid")
    date: str
    is_trading_day: bool
    rth_start_et: str
    rth_end_et: str


class QuoteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    last: str
    bid: str
    ask: str
    quote_time: str
    description: str
    # From the reference/quote blocks of the same read, absent when the
    # symbol answered the compact read but not the fielded one. None means
    # "not reported", never "no" — §1.4 and §3.2 turn on `optionable`.
    week52_high: str | None
    optionable: bool | None


class Quotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotes: list[QuoteOut]
    # Named, not dropped: Schwab omits a symbol it does not know rather than
    # erroring, and a silently short list reads as "I checked them all".
    missing: list[str]


class BarOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    open: str
    high: str
    low: str
    close: str


class Bars(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    bars: list[BarOut]


class Expirations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    expirations: list[Expiration]


class Instruments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruments: list[Instrument]


class Movers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movers: list[Mover]


class SessionStatusOut(BaseModel):
    """The §7.2 close row, as fields.

    This replaces `scripts/latest-status.sh --hwm`, which scraped the mark out
    of a markdown block with a money regex because `status/` was gitignored
    and Glob returned nothing under an ignored path. The numbers arrive typed
    now; the scraping failure mode is gone with it."""

    model_config = ConfigDict(extra="forbid")
    date: str
    close_value: str
    hwm: str
    halt: str
    drawdown_pct: str
    level: str
    prior_hwm: str
    ratcheted: bool
    intraday_high: str | None


class TickOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_et: str
    state: str
    account_value: str
    hwm: str
    drawdown_pct: str
    level: str
    positions: int
    stops: int
    orders: int
    settled: str
    unsettled: str
    flags: str
    note: str


class StatusLatest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_status: SessionStatusOut | None
    last_tick: TickOut | None


class RulesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manual: dict[str, str]
    strategy: dict[str, str]
    source: str


class PositionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    asset_type: str
    quantity: int
    average_price: str | None
    market_value: str
    day_pl: str
    settled_quantity: int


class StopOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    stop_price: str
    limit_price: str


class Book(BaseModel):
    """The last reconciled state of the account, from the store.

    Deliberately not a live broker read: §4.5 reconciliation is the engine's
    job and happens on a schedule, so this is the same state every loop and
    every ledger row was computed from. `read_at is None` means no reconcile
    has been recorded yet — that is a "stop and say so", not an empty book."""

    model_config = ConfigDict(extra="forbid")
    read_at: str | None
    account_value: str | None
    settled_cash: str | None
    unsettled_cash: str | None
    positions: list[PositionOut]
    stops: list[StopOut]
    restricted: bool


def _s(v: Decimal) -> str:
    return str(v)


def _s_opt(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


@contextlib.contextmanager
def _broker_faults(what: str) -> Iterator[None]:
    """Broker exceptions in, one-line `ToolError`s out.

    `BrokerUnauthorized` gets its own wording because it is the one fault the
    model can neither retry nor route around: the token is dead and only a
    human re-auth revives it. Everything else names its class and nothing
    else."""
    try:
        yield
    except BrokerUnauthorized as e:
        raise ToolError("broker blind: token absent/dead") from e
    except BrokerError as e:
        raise ToolError(f"{what} failed: {type(e).__name__}") from e


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise ToolError(f"{field} must be YYYY-MM-DD, got {value!r}") from e


def _checked_symbols(symbols: Sequence[str]) -> list[str]:
    """The cap, enforced in the body and not only in the schema.

    A `Field(max_length=...)` annotation is what the model is *told*; it is
    applied by FastMCP when it validates the call, and every internal caller
    of this function bypasses it. The cost of an unbounded quote list is
    context, which is the one budget nothing else in the system defends."""
    out = [s.strip().upper() for s in symbols if s.strip()]
    if not out:
        raise ToolError("symbols must name at least one symbol")
    if len(out) > MAX_SYMBOLS:
        raise ToolError(f"at most {MAX_SYMBOLS} symbols per call, got {len(out)}")
    return out


def _position_out(p: Position) -> PositionOut:
    return PositionOut(
        symbol=p.symbol,
        asset_type=p.asset_type,
        quantity=p.quantity,
        average_price=_s_opt(p.average_price),
        market_value=_s(p.market_value),
        day_pl=_s(p.day_pl),
        settled_quantity=p.settled_quantity,
    )


def register(server: FastMCP, deps: McpDeps, role: Role) -> None:
    """Add every read tool to `server`. `book` only for the decide role."""

    @server.tool(
        name="get_datetime",
        description=(
            "The current Eastern date and time. Call this first: never read a "
            "date from anywhere else."
        ),
    )
    async def get_datetime() -> Now:
        now = deps.clock()
        return Now(
            date=now.astimezone(ET).date().isoformat(),
            time_et=now.astimezone(ET).strftime("%H:%M:%S"),
            iso_utc=now.astimezone(UTC).isoformat(),
            tz="America/New_York",
        )

    @server.tool(
        name="market_hours",
        description=(
            "Whether a date is a trading day, and the regular-session window in "
            "Eastern time. The two are separate answers: the window is reported "
            "even on a closed day, as the standard session when the broker "
            "gives none."
        ),
    )
    async def market_hours(
        date: Annotated[str, Field(description="YYYY-MM-DD, Eastern")],
    ) -> Hours:
        d = _parse_date(date, "date")
        with _broker_faults("market hours read"):
            window = await deps.broker.market_window(d)
        start = window.rth_start.astimezone(ET).time() if window.rth_start else RTH_START
        end = window.rth_end.astimezone(ET).time() if window.rth_end else RTH_END
        return Hours(
            date=d.isoformat(),
            is_trading_day=window.is_trading_day,
            rth_start_et=start.isoformat(),
            rth_end_et=end.isoformat(),
        )

    @server.tool(
        name="quotes",
        description=(
            "Compact quotes for up to 50 symbols: price, bid/ask, the quote "
            "timestamp, and whether the name is optionable. Symbols the broker "
            "did not answer for come back under `missing`."
        ),
    )
    async def quotes(
        symbols: Annotated[
            list[str], Field(min_length=1, max_length=MAX_SYMBOLS, description="Tickers")
        ],
    ) -> Quotes:
        wanted = _checked_symbols(symbols)
        with _broker_faults("quote read"):
            got = await deps.broker.quotes(wanted)
            # The same endpoint with the fielded request. Two typed views over
            # it exist because the universe sweep needs the fundamentals and
            # this tool must not carry them; neither view alone holds both the
            # quote timestamp (the §4.10 stale-quote gate) and `optionable`.
            reference = await deps.broker.quotes_verbose(wanted)
        out: list[QuoteOut] = []
        for symbol in wanted:
            q = got.get(symbol)
            if q is None:
                continue
            v = reference.get(symbol)
            out.append(
                QuoteOut(
                    symbol=q.symbol,
                    last=_s(q.last),
                    bid=_s(q.bid),
                    ask=_s(q.ask),
                    quote_time=q.quote_time.astimezone(ET).isoformat(),
                    description=q.description or (v.description if v else ""),
                    week52_high=None if v is None else _s(v.week52_high),
                    optionable=None if v is None else v.optionable,
                )
            )
        return Quotes(quotes=out, missing=[s for s in wanted if s not in got])

    @server.tool(
        name="price_history",
        description="Daily OHLC bars for a symbol, most recent last.",
    )
    async def price_history(
        symbol: Annotated[str, Field(min_length=1, max_length=12)],
        days: Annotated[int, Field(ge=1, le=MAX_HISTORY_DAYS, description="Sessions")],
    ) -> Bars:
        if not 1 <= days <= MAX_HISTORY_DAYS:
            raise ToolError(f"days must be between 1 and {MAX_HISTORY_DAYS}")
        with _broker_faults("price history read"):
            bars = await deps.broker.daily_bars(symbol.strip().upper(), days)
        return Bars(
            symbol=symbol.strip().upper(),
            bars=[
                BarOut(
                    date=b.date.isoformat(),
                    open=_s(b.open),
                    high=_s(b.high),
                    low=_s(b.low),
                    close=_s(b.close),
                )
                for b in bars
            ],
        )

    @server.tool(
        name="option_chain",
        description=(
            "Option contracts for a symbol within an expiry window, flattened "
            "and sorted. Carries delta, open interest, bid/ask and DTE — the "
            "§3.2 quality floors."
        ),
    )
    async def option_chain(
        symbol: Annotated[str, Field(min_length=1, max_length=12)],
        from_date: Annotated[str, Field(description="YYYY-MM-DD, earliest expiry")],
        to_date: Annotated[str, Field(description="YYYY-MM-DD, latest expiry")],
        strike_count: Annotated[int, Field(ge=1, le=MAX_STRIKE_COUNT)],
        contract_type: ContractType,
    ) -> OptionChainView:
        frm = _parse_date(from_date, "from_date")
        to = _parse_date(to_date, "to_date")
        if to < frm:
            raise ToolError("to_date is before from_date")
        with _broker_faults("option chain read"):
            return await deps.broker.option_chain(
                symbol.strip().upper(), frm, to, strike_count, contract_type
            )

    @server.tool(
        name="expiration_chain",
        description="Every listed expiry for a symbol, with days to expiration.",
    )
    async def expiration_chain(
        symbol: Annotated[str, Field(min_length=1, max_length=12)],
    ) -> Expirations:
        clean = symbol.strip().upper()
        with _broker_faults("expiration chain read"):
            rows = await deps.broker.expiration_chain(clean)
        return Expirations(symbol=clean, expirations=rows)

    @server.tool(
        name="instruments",
        description=(
            "Instrument lookup: the §4.10 identifier round-trip. The returned "
            "description and exchange must match the written thesis."
        ),
    )
    async def instruments(
        query: Annotated[str, Field(min_length=1, max_length=64)],
        projection: Projection = "symbol-search",
    ) -> Instruments:
        with _broker_faults("instrument read"):
            rows = await deps.broker.instruments(query.strip(), projection)
        return Instruments(instruments=rows)

    @server.tool(
        name="movers",
        description="The day's biggest movers on an index, up or down.",
    )
    async def movers(
        index: Annotated[
            str, Field(description="EQUITY_ALL, NASDAQ, NYSE, SPX, DJI, COMPX, ...")
        ],
        direction: MoverDirection,
    ) -> Movers:
        with _broker_faults("movers read"):
            rows = await deps.broker.movers(index.strip().upper(), direction)
        return Movers(movers=rows)

    @server.tool(
        name="status_latest",
        description=(
            "The latest session-close row (high-water mark, halt level, "
            "drawdown) and the latest tick, as fields. Both are null before the "
            "first close of the engine's life."
        ),
    )
    async def status_latest() -> StatusLatest:
        row = await deps.store.latest_session_status()
        tick = await deps.store.latest_tick()
        return StatusLatest(
            session_status=None
            if row is None
            else SessionStatusOut(
                date=row.date.isoformat(),
                close_value=_s(row.close_value),
                hwm=_s(row.hwm),
                halt=_s(row.halt),
                drawdown_pct=_s(row.drawdown_pct),
                level=row.level,
                prior_hwm=_s(row.prior_hwm),
                ratcheted=row.ratcheted,
                intraday_high=_s_opt(row.intraday_high),
            ),
            last_tick=None
            if tick is None
            else TickOut(
                at_et=tick.at_et,
                state=tick.state,
                account_value=_s(tick.account_value),
                hwm=_s(tick.hwm),
                drawdown_pct=_s(tick.drawdown_pct),
                level=tick.level,
                positions=tick.positions,
                stops=tick.stops,
                orders=tick.orders,
                settled=_s(tick.settled),
                unsettled=_s(tick.unsettled),
                flags=tick.flags,
                note=tick.note,
            ),
        )

    @server.tool(
        name="rules",
        description=(
            "Every rule parameter from rules.yml, as strings: `manual` values "
            "are §9-gated, `strategy` values are discretionary and stricter."
        ),
    )
    async def rules() -> RulesOut:
        return RulesOut(
            manual={k: str(v) for k, v in deps.rules.manual.items()},
            strategy={k: str(v) for k, v in deps.rules.strategy.items()},
            source=str(deps.rules.source),
        )

    if role != "decide":
        return

    @server.tool(
        name="book",
        description=(
            "Positions, resting stops and cash from the last reconcile. "
            "`read_at` null means the engine has not reconciled yet — stop and "
            "say so rather than reading an empty book as a flat account."
        ),
    )
    async def book() -> Book:
        account = await deps.store.latest_account()
        if account is None:
            return Book(
                read_at=None,
                account_value=None,
                settled_cash=None,
                unsettled_cash=None,
                positions=[],
                stops=[],
                restricted=False,
            )
        # Same read date as the account snapshot: `reconcile()` records both
        # from one `now`, so the stops belonging to this snapshot are the ones
        # filed under its own date. A resting stop with no limit price is not
        # represented -- §3.4 requires stop-limit, and the naked-position and
        # orphan watches read the whole order set (tc.loops.tick), not this
        # summary, so nothing safety-bearing hangs off the omission.
        stops = await deps.store.resting_stops_on(account.read_at.date())
        return Book(
            read_at=account.read_at.astimezone(ET).isoformat(),
            account_value=_s(account.liquidation_value),
            settled_cash=_s(account.cash_available_for_trading),
            unsettled_cash=_s(account.unsettled_cash),
            positions=[_position_out(p) for p in account.positions],
            stops=[
                StopOut(symbol=s, stop_price=_s(stop), limit_price=_s(limit))
                for s, (stop, limit) in sorted(stops.items())
            ],
            restricted=account.is_closing_only_restricted or account.cash_call != 0,
        )
