"""The PM's side of the desk (spec §6, §9): calls, funding, extensions,
tightenings and exit requests. The engine stamps every price from a fresh
quote; the model supplies judgement and levels, never a reference price."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import Broker, BrokerError, BrokerUnauthorized
from tc.broker.models import Quote
from tc.clock import ET
from tc.config import DeskConfig
from tc.desk import indicators as ind
from tc.desk.calls import (
    NewCall,
    calls_made_on,
    current_call_id,
    effective_invalidation,
    get_call,
    insert_call,
    is_extended,
    open_call_for,
    tighten,
)
from tc.desk.models import ANALYSTS, DeskRefused, Direction, Funding, Instrument
from tc.desk.options import fetch_candidates, same_osi
from tc.desk.paper import (
    Proposal,
    book_row,
    book_state,
    create_proposal,
    marks,
    open_positions,
    pending_proposals,
    request_exit,
)
from tc.desk.pitches import (
    check_benchmark,
    check_horizon,
    check_levels,
    get_pitch,
    open_pitches,
    parse_price,
    tradeable_symbols,
)
from tc.desk.scorecard import mean
from tc.desk.scoring import resolution_for, resolved_rows
from tc.desk.sizing import BookState, check_book, conviction_pct, option_quantity, share_quantity
from tc.money import cents
from tc.rules.arith import cap_dollars
from tc.rules.model import Rules
from tc.store.db import Store

QUOTE_MAX_AGE_S = 300
CORR_BARS = 80
CENT = Decimal("0.01")


@dataclass(frozen=True)
class PmContext:
    store: Store
    broker: Broker
    rules: Rules
    desk: DeskConfig
    reserve: Decimal
    now: datetime

    @property
    def today(self) -> date:
        return self.now.astimezone(ET).date()


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pitch_id: int | None = None
    symbol: str = Field(min_length=1, max_length=10)
    direction: Direction
    thesis: str = Field(min_length=10, max_length=400)
    target: str
    invalidation: str
    horizon_days: int
    conviction: int = Field(ge=1, le=5)
    benchmark: str
    funding: Funding = "none"
    max_entry_price: str | None = None
    option_symbol: str | None = None
    legacy: bool = False


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    instrument: str
    symbol: str
    quantity: int
    max_entry_price: str


class CallOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    origin: str
    ref_price: str
    proposal: ProposalOut | None


@dataclass(frozen=True)
class _Plan:
    instrument: Instrument
    symbol: str
    quantity: int
    max_entry: Decimal
    atr_pct: Decimal | None


def _proposal_out(p: Proposal) -> ProposalOut:
    return ProposalOut(id=p.id, instrument=p.instrument, symbol=p.symbol, quantity=p.quantity,
                       max_entry_price=str(p.max_entry_price))


async def fresh_quotes(ctx: PmContext, symbols: Iterable[str]) -> dict[str, Quote]:
    wanted = list(dict.fromkeys(symbols))
    try:
        got = await ctx.broker.quotes(wanted)
    except BrokerUnauthorized:
        raise DeskRefused("broker blind: token absent/dead; nothing can be priced") from None
    except BrokerError as e:
        raise DeskRefused(f"quote read failed ({type(e).__name__}); try again") from None
    for s in wanted:
        q = got.get(s)
        if q is None:
            raise DeskRefused(f"no quote for {s}")
        age = (ctx.now - q.quote_time).total_seconds()
        if age > QUOTE_MAX_AGE_S:
            raise DeskRefused(
                f"the {s} quote is {int(age)}s old (§4.10 stale-quote gate); try again in a minute"
            )
    return got


async def _correlated(ctx: PmContext, symbol: str, book: BookState) -> frozenset[str]:
    threshold = ctx.rules.get("manual", "correlation_threshold")
    mine = await ctx.store.bars_for(symbol, limit=CORR_BARS)
    out: set[str] = set()
    for h in book.holdings:
        if h.symbol == symbol:
            continue
        r = ind.log_return_corr(mine, await ctx.store.bars_for(h.symbol, limit=CORR_BARS))
        if r is not None and r > threshold:
            out.add(h.symbol)
    return frozenset(out)


async def _fund(
    ctx: PmContext, cin: CallIn, symbol: str, bench: str, last: Decimal, inval: Decimal
) -> _Plan:
    rules = ctx.rules
    conviction_pct("shares" if cin.funding == "shares" else "option", cin.conviction, rules)
    chase = rules.get("strategy", "max_entry_chase_pct")
    book = await book_state(ctx.store)
    correlated = await _correlated(ctx, symbol, book)
    if cin.funding == "shares":
        if cin.direction != "up":
            raise DeskRefused("shares fund up calls only; a down call is funded with a put")
        if cin.max_entry_price is None:
            raise DeskRefused("share funding needs max_entry_price")
        max_entry = parse_price(cin.max_entry_price, "max_entry_price")
        if max_entry > last * (1 + chase / 100):
            raise DeskRefused(
                f"max_entry_price {max_entry} chases more than {chase}% above the last price {last}"
            )
        atr = ind.atr_pct(await ctx.store.bars_for(symbol, limit=CORR_BARS))
        if atr is None:
            raise DeskRefused(f"no ATR for {symbol} yet; fund it as an option or leave it unfunded")
        ceiling = rules.get("strategy", "max_daily_atr_pct")
        if atr > ceiling:
            raise DeskRefused(
                f"{symbol} daily ATR {atr.quantize(CENT)}% is over the {ceiling}% share ceiling;"
                " express it as an option or leave it unfunded"
            )
        if inval >= max_entry:
            raise DeskRefused("the invalidation must sit below max_entry_price")
        qty = share_quantity(cin.conviction, book.equity, max_entry, rules)
        check_book(book, symbol=symbol, benchmark=bench, notional=max_entry * qty,
                   is_option=False, correlated=correlated, rules=rules, reserve=ctx.reserve)
        return _Plan("shares", symbol, qty, max_entry, atr)
    want: Instrument = "call" if cin.direction == "up" else "put"
    if cin.funding != want:
        raise DeskRefused(f"a {cin.direction} call is funded with a {want}")
    if not cin.option_symbol:
        raise DeskRefused("option funding needs option_symbol: pick one from option_candidates")
    cap = cap_dollars(conviction_pct("option", cin.conviction, rules), book.equity)
    try:
        cands = await fetch_candidates(ctx.broker, symbol, cin.direction, cin.horizon_days, cap,
                                       rules, ctx.today)
    except BrokerError as e:
        raise DeskRefused(f"option chain read failed ({type(e).__name__}); try again") from None
    chosen = next((c for c in cands if same_osi(c.osi, cin.option_symbol)), None)
    if chosen is None:
        raise DeskRefused(
            f"{cin.option_symbol} is not among the contracts that clear §3.2 right now:"
            f" {[c.osi for c in cands]}"
        )
    # The chain fetch above can be stale by the time a decision is made; the
    # contract actually funded is priced off its OWN fresh quote (§4.10),
    # never the chain snapshot's ask.
    ask = (await fresh_quotes(ctx, [chosen.osi]))[chosen.osi].ask
    ceiling = cents(ask * (1 + chase / 100))
    if cin.max_entry_price is not None:
        max_entry = parse_price(cin.max_entry_price, "max_entry_price")
        if max_entry > ceiling:
            raise DeskRefused(
                f"max_entry_price {max_entry} chases more than {chase}% above the ask {ask}"
            )
    else:
        max_entry = ceiling
    # Quantity and the book check both size off max_entry_price, never the raw
    # ask: a conviction-5 fill up to max_entry_price must itself clear the
    # manual §3.2 caps, not merely the (lower) ask-priced notional.
    qty = option_quantity(cin.conviction, book.equity, max_entry, rules)
    check_book(book, symbol=symbol, benchmark=bench, notional=max_entry * 100 * qty,
               is_option=True, correlated=correlated, rules=rules, reserve=ctx.reserve)
    return _Plan(want, chosen.osi, qty, max_entry, None)


async def submit_call(ctx: PmContext, cin: CallIn) -> CallOut:
    symbol = cin.symbol.strip().upper()
    bench = check_benchmark(cin.benchmark)
    check_horizon(cin.horizon_days, ctx.rules)
    target = parse_price(cin.target, "target")
    inval = parse_price(cin.invalidation, "invalidation")
    if cin.legacy:
        return await _legacy_call(ctx, cin, symbol, bench, target, inval)
    if symbol not in await tradeable_symbols(ctx.store, ctx.desk):
        raise DeskRefused(f"{symbol} is not in the qualified universe or on the ETF list")
    cap = int(ctx.rules.get("strategy", "desk_max_new_calls_per_day"))
    if await calls_made_on(ctx.store, ctx.today) >= cap:
        raise DeskRefused(
            f"today's {cap} new calls are made; the remaining pitches are scored without you"
        )
    existing = await open_call_for(ctx.store, symbol, cin.direction)
    if existing is not None:
        raise DeskRefused(
            f"call {existing.id} on {symbol} {cin.direction} is still open; reaffirming it is"
            " not a new call"
        )
    origin: Literal["pitch", "pm"] = "pm"
    if cin.pitch_id is not None:
        p = await get_pitch(ctx.store, cin.pitch_id)
        if p is None or p.withdrawn or await resolution_for(ctx.store, "pitch", p.id) is not None:
            raise DeskRefused(f"pitch {cin.pitch_id} is not open")
        if (p.symbol, p.direction) != (symbol, cin.direction):
            raise DeskRefused(
                f"pitch {p.id} is {p.symbol} {p.direction}; adopt it on the same symbol and"
                " direction, or originate your own call"
            )
        origin = "pitch"
    q = await fresh_quotes(ctx, [symbol, "SPY", bench])
    last = q[symbol].last
    check_levels(cin.direction, last, target, inval, ctx.rules)
    plan = None if cin.funding == "none" else await _fund(ctx, cin, symbol, bench, last, inval)
    call = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin=origin, pitch_id=cin.pitch_id,
        extends_call_id=None, symbol=symbol, direction=cin.direction, thesis=cin.thesis,
        target=target, invalidation=inval, horizon_days=cin.horizon_days,
        conviction=cin.conviction, benchmark=bench, ref_price=last, spy_ref=q["SPY"].last,
        bench_ref=q[bench].last, funding=cin.funding,
    ))
    prop = None
    if plan is not None:
        prop = await create_proposal(
            ctx.store, call_id=call.id, created_at=ctx.now, instrument=plan.instrument,
            symbol=plan.symbol, underlying=symbol, quantity=plan.quantity,
            max_entry_price=plan.max_entry, atr_pct=plan.atr_pct,
        )
    return CallOut(call_id=call.id, origin=call.origin, ref_price=str(last),
                   proposal=None if prop is None else _proposal_out(prop))


async def _legacy_call(
    ctx: PmContext, cin: CallIn, symbol: str, bench: str, target: Decimal, inval: Decimal
) -> CallOut:
    if cin.funding != "none":
        raise DeskRefused(
            'a legacy call records a view on a position already held; funding must be "none"'
        )
    acct = await ctx.store.latest_account()
    if acct is None or symbol not in {p.symbol for p in acct.positions}:
        raise DeskRefused(f"{symbol} is not a position in the real account")
    if await open_call_for(ctx.store, symbol, cin.direction, legacy=True) is not None:
        raise DeskRefused(f"{symbol} already has an open legacy call")
    q = await fresh_quotes(ctx, [symbol, "SPY", bench])
    last = q[symbol].last
    check_levels(cin.direction, last, target, inval, ctx.rules)
    call = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin="legacy", pitch_id=None, extends_call_id=None,
        symbol=symbol, direction=cin.direction, thesis=cin.thesis, target=target,
        invalidation=inval, horizon_days=cin.horizon_days, conviction=cin.conviction,
        benchmark=bench, ref_price=last, spy_ref=q["SPY"].last, bench_ref=q[bench].last,
        funding="none",
    ))
    return CallOut(call_id=call.id, origin="legacy", ref_price=str(last), proposal=None)


async def _held_by(ctx: PmContext, call_id: int) -> bool:
    for p in await open_positions(ctx.store):
        if await current_call_id(ctx.store, p.call_id) == call_id:
            return True
    return False


async def extend_call(
    ctx: PmContext, call_id: int, *, target: str, invalidation: str, horizon_days: int,
    thesis: str,
) -> CallOut:
    old = await get_call(ctx.store, call_id)
    if old is None or old.origin == "legacy":
        raise DeskRefused(f"there is no desk call {call_id}")
    if old.extends_call_id is not None or await is_extended(ctx.store, call_id):
        raise DeskRefused(f"call {call_id} has already used its one extension")
    res = await resolution_for(ctx.store, "call", call_id)
    if res is None or res.how != "horizon":
        raise DeskRefused("only a call that reached its horizon can be extended")
    if not await _held_by(ctx, call_id):
        raise DeskRefused(f"call {call_id} has no open paper position to carry")
    check_horizon(horizon_days, ctx.rules)
    t, i = parse_price(target, "target"), parse_price(invalidation, "invalidation")
    q = await fresh_quotes(ctx, [old.symbol, "SPY", old.benchmark])
    last = q[old.symbol].last
    check_levels(old.direction, last, t, i, ctx.rules)
    new = await insert_call(ctx.store, NewCall(
        made_at=ctx.now, session=ctx.today, origin="pm", pitch_id=None, extends_call_id=call_id,
        symbol=old.symbol, direction=old.direction, thesis=thesis, target=t, invalidation=i,
        horizon_days=horizon_days, conviction=old.conviction, benchmark=old.benchmark,
        ref_price=last, spy_ref=q["SPY"].last, bench_ref=q[old.benchmark].last,
        funding=old.funding,
    ))
    return CallOut(call_id=new.id, origin=new.origin, ref_price=str(last), proposal=None)


async def tighten_call(ctx: PmContext, call_id: int, invalidation: str) -> Decimal:
    call = await get_call(ctx.store, call_id)
    if call is None:
        raise DeskRefused(f"there is no call {call_id}")
    if call.origin == "legacy":
        raise DeskRefused("a legacy position's stop is moved by Chris at Schwab, not by the desk")
    if await resolution_for(ctx.store, "call", call_id) is not None:
        raise DeskRefused(f"call {call_id} has resolved; tighten its extension if it has one")
    if not await _held_by(ctx, call_id):
        raise DeskRefused(f"call {call_id} has no open paper position to protect")
    new = parse_price(invalidation, "invalidation")
    cur = await effective_invalidation(ctx.store, call)
    last = (await fresh_quotes(ctx, [call.symbol]))[call.symbol].last
    ok = cur < new < last if call.direction == "up" else last < new < cur
    if not ok:
        raise DeskRefused(
            f"a tightened invalidation must sit between the current level {cur} and the last"
            f" price {last}"
        )
    await tighten(ctx.store, call_id, new, ctx.now)
    return new


async def request_exit_for(ctx: PmContext, symbol: str, reason: str) -> Literal["paper", "legacy"]:
    sym = symbol.strip().upper()
    held = [p for p in await open_positions(ctx.store) if p.underlying == sym]
    for p in held:
        await request_exit(ctx.store, sym, p.proposal_id, reason, ctx.now)
    if held:
        return "paper"
    acct = await ctx.store.latest_account()
    if acct is not None and sym in {p.symbol for p in acct.positions}:
        await request_exit(ctx.store, sym, None, reason, ctx.now)
        return "legacy"
    raise DeskRefused(f"nothing is held in {sym}")


class PositionView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: int
    call_id: int
    symbol: str
    instrument: str
    quantity: int
    entry_price: str
    mark: str | None
    stop_trigger: str | None
    target: str
    invalidation: str
    horizon_days: int
    session: str


class LegacyView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    quantity: int
    market_value: str
    legacy_call_id: int | None


class PaperBookOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started: bool
    start_date: str | None
    equity: str | None
    cash: str | None
    open_premium: str | None
    positions: list[PositionView]
    pending: list[ProposalOut]
    legacy: list[LegacyView]


async def paper_book_view(ctx: PmContext) -> PaperBookOut:
    legacy: list[LegacyView] = []
    acct = await ctx.store.latest_account()
    for rp in acct.positions if acct is not None else []:
        lc = (await open_call_for(ctx.store, rp.symbol, "up", legacy=True)
              or await open_call_for(ctx.store, rp.symbol, "down", legacy=True))
        legacy.append(LegacyView(symbol=rp.symbol, quantity=rp.quantity,
                                 market_value=str(rp.market_value),
                                 legacy_call_id=None if lc is None else lc.id))
    row = await book_row(ctx.store)
    if row is None:
        return PaperBookOut(started=False, start_date=None, equity=None, cash=None,
                            open_premium=None, positions=[], pending=[], legacy=legacy)
    state = await book_state(ctx.store)
    mk = await marks(ctx.store)
    positions: list[PositionView] = []
    for p in await open_positions(ctx.store):
        call = await get_call(ctx.store, await current_call_id(ctx.store, p.call_id))
        assert call is not None
        inval = await effective_invalidation(ctx.store, call)
        mark = mk.get(p.symbol)
        positions.append(PositionView(
            proposal_id=p.proposal_id, call_id=call.id, symbol=p.symbol, instrument=p.instrument,
            quantity=p.quantity, entry_price=str(p.entry_price),
            mark=None if mark is None else str(mark),
            stop_trigger=None if p.stop_trigger is None else str(max(p.stop_trigger, inval)),
            target=str(call.target), invalidation=str(inval), horizon_days=call.horizon_days,
            session=call.session.isoformat(),
        ))
    return PaperBookOut(
        started=True, start_date=row[0].isoformat(), equity=str(state.equity.quantize(CENT)),
        cash=str(state.cash.quantize(CENT)), open_premium=str(state.open_premium.quantize(CENT)),
        positions=positions,
        pending=[_proposal_out(p) for p in await pending_proposals(ctx.store)], legacy=legacy,
    )


class PitchView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    analyst: str
    symbol: str
    direction: str
    session: str
    thesis: str
    target: str
    invalidation: str
    horizon_days: int
    conviction: int
    benchmark: str


class AnalystSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analyst: str
    resolved: int
    mean_excess_spy_pct: str | None
    mean_excess_bench_pct: str | None


class PitchesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pitches: list[PitchView]
    analysts: list[AnalystSummary]


async def pitches_view(ctx: PmContext) -> PitchesOut:
    pitches = [
        PitchView(id=p.id, analyst=p.analyst, symbol=p.symbol, direction=p.direction,
                  session=p.session.isoformat(), thesis=p.thesis, target=str(p.target),
                  invalidation=str(p.invalidation), horizon_days=p.horizon_days,
                  conviction=p.conviction, benchmark=p.benchmark)
        for p in await open_pitches(ctx.store)
    ]
    rows = await resolved_rows(ctx.store)
    summaries: list[AnalystSummary] = []
    for a in ANALYSTS:
        rs = [r.resolution for r in rows if r.analyst == a]
        ex, eb = mean([r.excess_spy for r in rs]), mean([r.excess_bench for r in rs])
        summaries.append(AnalystSummary(
            analyst=a, resolved=len(rs),
            mean_excess_spy_pct=None if ex is None else str(ex.quantize(CENT)),
            mean_excess_bench_pct=None if eb is None else str(eb.quantize(CENT)),
        ))
    return PitchesOut(pitches=pitches, analysts=summaries)
