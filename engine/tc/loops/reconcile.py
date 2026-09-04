"""CLAUDE.md §4.5: never begin from an assumed state. One account read, one
orders read, both recorded, one typed view for every loop that follows."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from tc.broker.client import Broker
from tc.broker.models import AccountSnapshot, OrderRow
from tc.store.db import Store


class BookView(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    account: AccountSnapshot
    orders: list[OrderRow]
    resting_stops: dict[str, OrderRow]
    naked: list[str]
    partial: list[tuple[str, int, int]]
    orphaned_stops: list[OrderRow]
    open_entries: list[OrderRow]
    restricted: bool
    read_at: datetime


def _is_sell_stop(o: OrderRow) -> bool:
    return o.is_resting_stop and len(o.legs) == 1 and o.legs[0].instruction.startswith("SELL")


def build_view(account: AccountSnapshot, orders: list[OrderRow], read_at: datetime) -> BookView:
    held = {p.symbol: p.quantity for p in account.positions if p.quantity > 0}
    stops = {o.symbol: o for o in orders if _is_sell_stop(o)}
    naked = [s for s in held if s not in stops]
    partial = [
        (s, q, stops[s].quantity) for s, q in held.items() if s in stops and stops[s].quantity != q
    ]
    orphaned = [o for s, o in stops.items() if s not in held]
    entries = [
        o
        for o in orders
        if o.status in {"WORKING", "QUEUED", "ACCEPTED"}
        and o.order_type == "LIMIT"
        and o.legs
        and o.legs[0].instruction.startswith("BUY")
    ]
    return BookView(
        account=account,
        orders=orders,
        resting_stops=stops,
        naked=sorted(naked),
        partial=partial,
        orphaned_stops=orphaned,
        open_entries=entries,
        restricted=account.is_closing_only_restricted or account.cash_call != 0,
        read_at=read_at,
    )


async def reconcile(
    broker: Broker, store: Store, account_hash: str, now: datetime, orders_from: date
) -> BookView:
    account = await broker.account(account_hash)
    frm = datetime.combine(orders_from, datetime.min.time(), tzinfo=now.tzinfo)
    orders = await broker.orders(account_hash, frm, now + timedelta(days=1))
    await store.record_account(account)
    await store.record_orders(account_hash, orders, now)
    return build_view(account, orders, now)
