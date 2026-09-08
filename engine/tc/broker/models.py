"""Typed views over Schwab Trader API payloads.

Field traps carried over from tick.md §B2/§B5 and the schwab-mcp-notes skill:
read currentBalances never initialBalances; currentDayProfitLoss is the DAY
move (schwab-mcp exposed it as unrealizedPL), lifetime P/L is computed; the
market-hours payload nests under "EQ" on a trading day and "equity" on a
closed one.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tc.money import D, cents

RESTING = {"WORKING", "QUEUED", "ACCEPTED", "PENDING_ACTIVATION", "AWAITING_STOP_CONDITION"}
STOP_TYPES = {"STOP", "STOP_LIMIT"}


def _dec(v: Any, default: str = "0") -> Decimal:
    return D(default) if v is None else cents(D(str(v)))


def _dec_raw_opt(v: Any) -> Decimal | None:
    """Like _dec but keeps full precision (no cents rounding), and preserves
    the difference between "no value" and zero."""
    return None if v is None else D(str(v))


def _int(v: Any) -> int:
    return int(D(str(v or 0)))


def _dt(s: str) -> datetime:
    # Schwab emits "2026-08-24T14:32:11+0000"; fromisoformat needs "+00:00"
    if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return datetime.fromisoformat(s)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    asset_type: str
    quantity: int
    average_price: Decimal | None
    market_value: Decimal
    day_pl: Decimal
    settled_quantity: int

    @property
    def lifetime_pl(self) -> Decimal | None:
        """None when Schwab reports no cost basis.

        A null basis must not be read as zero — that would report the entire
        market value as lifetime gain. Callers render None as "n/a";
        tc.rules.arith.lifetime_pl takes a real Decimal and is not called at
        all for a position whose basis is unknown."""
        if self.average_price is None:
            return None
        return cents(self.market_value - self.average_price * abs(self.quantity))


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_hash: str
    read_at: datetime
    liquidation_value: Decimal
    cash_available_for_trading: Decimal
    unsettled_cash: Decimal
    cash_balance: Decimal
    cash_call: Decimal
    is_closing_only_restricted: bool
    positions: list[Position]

    @property
    def reserve_cash(self) -> Decimal:
        return min(self.cash_balance, self.cash_available_for_trading + self.unsettled_cash)

    @classmethod
    def from_payload(
        cls, account_hash: str, payload: dict[str, Any], read_at: datetime
    ) -> AccountSnapshot:
        acct = payload["securitiesAccount"]
        bal = acct["currentBalances"]
        positions = [
            Position(
                symbol=p["instrument"]["symbol"],
                asset_type=p["instrument"].get("assetType", ""),
                quantity=_int(p.get("longQuantity")) - _int(p.get("shortQuantity")),
                average_price=_dec_raw_opt(p.get("averagePrice")),
                market_value=_dec(p.get("marketValue")),
                day_pl=_dec(p.get("currentDayProfitLoss")),
                settled_quantity=_int(p.get("settledLongQuantity")),
            )
            for p in acct.get("positions", [])
        ]
        return cls(
            account_hash=account_hash,
            read_at=read_at,
            liquidation_value=_dec(bal.get("liquidationValue")),
            cash_available_for_trading=_dec(bal.get("cashAvailableForTrading")),
            unsettled_cash=_dec(bal.get("unsettledCash")),
            cash_balance=_dec(bal.get("cashBalance")),
            cash_call=_dec(bal.get("cashCall")),
            is_closing_only_restricted=bool(acct.get("isClosingOnlyRestricted", False)),
            positions=positions,
        )


class OrderLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str
    quantity: int
    symbol: str
    asset_type: str


class OrderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    status: str
    order_type: str
    duration: str
    entered_at: datetime
    quantity: int
    filled_quantity: int
    price: Decimal | None
    stop_price: Decimal | None
    legs: list[OrderLeg]

    @property
    def symbol(self) -> str:
        return self.legs[0].symbol if self.legs else ""

    @property
    def is_resting_stop(self) -> bool:
        return self.status in RESTING and self.order_type in STOP_TYPES

    @classmethod
    def from_payload(cls, o: dict[str, Any]) -> OrderRow:
        return cls(
            order_id=int(o["orderId"]),
            status=str(o.get("status", "")),
            order_type=str(o.get("orderType", "")),
            duration=str(o.get("duration", "")),
            entered_at=_dt(o["enteredTime"]),
            quantity=_int(o.get("quantity")),
            filled_quantity=_int(o.get("filledQuantity")),
            price=None if o.get("price") is None else _dec(o["price"]),
            stop_price=None if o.get("stopPrice") is None else _dec(o["stopPrice"]),
            legs=[
                OrderLeg(
                    instruction=str(leg.get("instruction", "")),
                    quantity=_int(leg.get("quantity")),
                    symbol=leg["instrument"]["symbol"],
                    asset_type=leg["instrument"].get("assetType", ""),
                )
                for leg in o.get("orderLegCollection", [])
            ],
        )


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    quote_time: datetime
    description: str

    @classmethod
    def from_payload(cls, symbol: str, q: dict[str, Any]) -> Quote:
        try:
            quote = q["quote"]
        except KeyError:
            raise ValueError(f"no quote payload for {symbol}") from None
        return cls(
            symbol=symbol,
            last=_dec(quote.get("lastPrice")),
            bid=_dec(quote.get("bidPrice")),
            ask=_dec(quote.get("askPrice")),
            quote_time=datetime.fromtimestamp(int(quote.get("quoteTime", 0)) / 1000, tz=UTC),
            description=str(q.get("reference", {}).get("description", "")),
        )


class MarketWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    is_trading_day: bool
    rth_start: datetime | None
    rth_end: datetime | None

    @classmethod
    def from_payload(cls, d: dt.date, payload: dict[str, Any]) -> MarketWindow:
        equity = payload.get("equity") or {}
        body: dict[str, Any] = next(iter(equity.values()), {}) if equity else {}
        rth = (body.get("sessionHours") or {}).get("regularMarket") or []
        if body.get("isOpen") and rth:
            return cls(date=d, is_trading_day=True,
                       rth_start=_dt(rth[0]["start"]), rth_end=_dt(rth[0]["end"]))
        return cls(date=d, is_trading_day=False, rth_start=None, rth_end=None)


class DailyBar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_payload(cls, c: dict[str, Any]) -> DailyBar:
        d = datetime.fromtimestamp(int(c["datetime"]) / 1000, tz=UTC).date()
        return cls(
            date=d,
            open=_dec(c["open"]),
            high=_dec(c["high"]),
            low=_dec(c["low"]),
            close=_dec(c["close"]),
        )


def _num(v: Any, default: str = "0") -> Decimal:
    """Full-precision Decimal, with "" treated as absent.

    Distinct from `_dec` above, which quantizes to the cent because it reads
    money. These fields are not all money: a delta of 0.6123, an implied
    volatility of 20.85, a share count of 2,349,452 — rounding any of them to
    the cent would either destroy the value or invent two decimal places the
    payload never had.
    """
    if v is None or v == "":
        return D(default)
    return D(str(v))


# Schwab's "we cannot price this" sentinel. It arrives as a number, which is
# exactly the problem: a delta FLOOR test passes it (it IS a number) and a
# delta BAND test rejects it as too low (the wrong number), and the manual's
# delta rule is a band. None is the only honest record of "unknown".
_UNPRICED = (None, "", "NaN", -999.0, "-999.0", -999)


class VerboseQuote(BaseModel):
    """The universe sweep's row, straight off the JSON payload.

    v2 read this out of two-space-indented verbose *text* and had to split it
    into named sub-blocks by regex, because `lastPrice` exists in BOTH the
    `extended` and `quote` blocks and `extended` came first -- so a whole-body
    match returned the after-hours print. Here the sub-objects are already
    named, and the precedence is stated once: regular, then quote, never
    extended.
    """

    model_config = ConfigDict(extra="forbid")
    symbol: str
    price: Decimal | None  # None = unquotable; recorded, never treated as 0
    avg10_days_volume: Decimal
    fund_leverage_factor: Decimal  # PERCENT: 0 stock, 100 = 1x fund, 200/300 leveraged
    high: Decimal | None
    low: Decimal | None
    week52_high: Decimal
    net_percent_change: Decimal
    optionable: bool
    description: str
    last_earnings: str  # "" when absent; a column of the universe table
    is_etf: bool

    @classmethod
    def from_payload(cls, symbol: str, body: dict[str, Any]) -> VerboseQuote:
        q = body.get("quote") or {}
        f = body.get("fundamental") or {}
        r = body.get("reference") or {}
        reg = body.get("regular") or {}
        # Fall through on an EMPTY regular price, not merely a missing key: a
        # `regular` block that exists with a null price is what a pre-market
        # read looks like, and `.get(key, fallback)` would hand back that null
        # as the answer.
        raw_price = reg.get("regularMarketLastPrice")
        if raw_price in (None, ""):
            raw_price = q.get("lastPrice")
        return cls(
            symbol=symbol,
            price=None if raw_price in (None, "") else _num(raw_price),
            avg10_days_volume=_num(f.get("avg10DaysVolume")),
            fund_leverage_factor=_num(f.get("fundLeverageFactor")),
            high=None if q.get("highPrice") is None else _num(q["highPrice"]),
            low=None if q.get("lowPrice") is None else _num(q["lowPrice"]),
            week52_high=_num(q.get("52WeekHigh")),
            net_percent_change=_num(q.get("netPercentChange")),
            optionable=bool(r.get("optionable", False)),
            description=str(r.get("description", "")).strip(),
            last_earnings=str(f.get("lastEarningsDate") or "")[:10],
            is_etf=body.get("assetSubType") == "ETF",
        )


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    osi: str
    expiry: date
    strike: Decimal
    kind: Literal["CALL", "PUT"]
    bid: Decimal
    ask: Decimal
    last: Decimal
    delta: Decimal | None
    open_interest: int
    volume: int
    implied_volatility: Decimal | None
    days_to_expiration: int

    @classmethod
    def from_payload(cls, c: dict[str, Any]) -> OptionContract:
        raw_delta = c.get("delta")
        raw_vol = c.get("volatility")
        return cls(
            osi=str(c["symbol"]),
            expiry=_dt(str(c["expirationDate"]).replace("Z", "+00:00")).date(),
            strike=_num(c["strikePrice"]),
            kind="CALL" if str(c["putCall"]).upper() == "CALL" else "PUT",
            bid=_num(c.get("bid")),
            ask=_num(c.get("ask")),
            last=_num(c.get("last")),
            delta=None if raw_delta in _UNPRICED else _num(raw_delta),
            open_interest=_int(c.get("openInterest")),
            volume=_int(c.get("totalVolume")),
            implied_volatility=None if raw_vol in _UNPRICED else _num(raw_vol),
            days_to_expiration=_int(c.get("daysToExpiration")),
        )


class OptionChainView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    underlying_price: Decimal
    contracts: list[OptionContract]

    @classmethod
    def from_payload(cls, symbol: str, body: dict[str, Any]) -> OptionChainView:
        """Both maps flattened into one sorted list.

        Schwab nests the chain expiry -> strike -> [contract], with calls and
        puts in separate top-level maps. Every caller wants a list; leaving
        the nesting in place would mean every caller re-implements this walk,
        and the sort is what makes the flattened order deterministic rather
        than dict-insertion order.
        """
        out: list[OptionContract] = []
        for key in ("callExpDateMap", "putExpDateMap"):
            for strikes in (body.get(key) or {}).values():
                for rows in strikes.values():
                    out.extend(OptionContract.from_payload(c) for c in rows)
        out.sort(key=lambda c: (c.expiry, c.kind, c.strike))
        return cls(
            symbol=symbol,
            underlying_price=_num(body.get("underlyingPrice")),
            contracts=out,
        )


class Expiration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiry: date
    days_to_expiration: int
    standard: bool

    @classmethod
    def from_payload(cls, e: dict[str, Any]) -> Expiration:
        return cls(
            expiry=date.fromisoformat(str(e["expirationDate"])[:10]),
            days_to_expiration=_int(e.get("daysToExpiration")),
            standard=bool(e.get("standard", True)),
        )


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    description: str
    asset_type: str
    exchange: str
    cusip: str | None

    @classmethod
    def from_payload(cls, i: dict[str, Any]) -> Instrument:
        cusip = i.get("cusip")
        return cls(
            symbol=str(i.get("symbol", "")),
            description=str(i.get("description", "")),
            asset_type=str(i.get("assetType", "")),
            exchange=str(i.get("exchange", "")),
            cusip=None if cusip is None else str(cusip),
        )


class Mover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    description: str
    last: Decimal
    net_change: Decimal
    net_percent_change: Decimal
    volume: int

    @classmethod
    def from_payload(cls, m: dict[str, Any]) -> Mover:
        return cls(
            symbol=str(m.get("symbol", "")),
            description=str(m.get("description", "")),
            last=_num(m.get("lastPrice")),
            net_change=_num(m.get("netChange")),
            net_percent_change=_num(m.get("netPercentChange")),
            volume=_int(m.get("volume")),
        )
