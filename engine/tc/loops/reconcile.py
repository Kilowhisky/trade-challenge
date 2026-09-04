"""CLAUDE.md §4.5: never begin from an assumed state. One account read, one
orders read, both recorded, one typed view for every loop that follows."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from tc.broker.client import Broker
from tc.broker.models import RESTING, AccountSnapshot, OrderRow
from tc.store.db import Store


class BookView(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    account: AccountSnapshot
    orders: list[OrderRow]
    resting_stops: dict[str, OrderRow]
    # symbol -> every resting sell stop on it, whenever there is more than
    # one. `resting_stops` keeps one per symbol (the earliest) so watches 4
    # and 5 stay a straight symbol lookup; the extras are §1.5's accidental
    # short waiting to trigger and are named here rather than dropped.
    duplicate_stops: dict[str, list[OrderRow]] = Field(default_factory=dict)
    naked: list[str]
    partial: list[tuple[str, int, int]]
    orphaned_stops: list[OrderRow]
    open_entries: list[OrderRow]
    restricted: bool
    read_at: datetime

    @property
    def resting_stop_count(self) -> int:
        """EVERY resting sell stop, not one per symbol. The tick row's `stops`
        column is compared against the position count by a human reading the
        ledger, and a symbol carrying two stops that shows as one is the
        single case where that comparison must not look tidy."""
        return len(self.resting_stops) + sum(len(v) - 1 for v in self.duplicate_stops.values())


def _is_sell_stop(o: OrderRow) -> bool:
    return o.is_resting_stop and len(o.legs) == 1 and o.legs[0].instruction.startswith("SELL")


def build_view(account: AccountSnapshot, orders: list[OrderRow], read_at: datetime) -> BookView:
    held = {p.symbol: p.quantity for p in account.positions if p.quantity > 0}
    by_symbol: dict[str, list[OrderRow]] = {}
    for o in orders:
        if _is_sell_stop(o):
            by_symbol.setdefault(o.symbol, []).append(o)
    for rows in by_symbol.values():
        rows.sort(key=lambda o: (o.entered_at, o.order_id))
    # The earliest stop is the one watches 4 and 5 judge the position against:
    # last-in-wins silently made a stray second stop the "real" one, and its
    # quantity the one watch 5 compared against the fill.
    stops = {s: rows[0] for s, rows in by_symbol.items()}
    duplicates = {s: rows for s, rows in by_symbol.items() if len(rows) > 1}
    naked = [s for s in held if s not in stops]
    partial = [
        (s, q, stops[s].quantity) for s, q in held.items() if s in stops and stops[s].quantity != q
    ]
    orphaned = [o for s, o in stops.items() if s not in held]
    entries = [
        o
        for o in orders
        if o.status in RESTING
        and o.order_type == "LIMIT"
        and o.legs
        and o.legs[0].instruction.startswith("BUY")
    ]
    return BookView(
        account=account,
        orders=orders,
        resting_stops=stops,
        duplicate_stops=duplicates,
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
