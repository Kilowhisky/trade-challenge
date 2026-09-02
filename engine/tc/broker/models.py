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
from typing import Any

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
