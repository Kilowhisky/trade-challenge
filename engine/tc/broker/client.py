"""Read-only broker access. No write method exists in this module by design
(spec §5: the order path is a separate, later component)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx
from schwab import auth as schwab_auth
from schwab.client import AsyncClient

from tc.broker.models import AccountSnapshot, DailyBar, MarketWindow, OrderRow, Quote
from tc.broker.token import TokenStore


class BrokerError(Exception):
    pass


class BrokerUnauthorized(BrokerError):  # noqa: N818 -- name fixed by the task-7 interface contract
    """401 / invalid_grant: the token is dead. Only a human re-auth fixes this."""


class Broker(Protocol):
    async def account_hashes(self) -> list[str]: ...
    async def account(self, account_hash: str) -> AccountSnapshot: ...
    async def orders(
        self, account_hash: str, from_dt: datetime, to_dt: datetime
    ) -> list[OrderRow]: ...
    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...
    async def market_window(self, d: date) -> MarketWindow: ...
    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]: ...
    def now(self) -> datetime: ...


def _raise_for(resp: httpx.Response) -> dict[str, Any] | list[Any]:
    if resp.status_code == 401:
        raise BrokerUnauthorized(resp.text[:200])
    if resp.status_code >= 400:
        raise BrokerError(f"{resp.status_code}: {resp.text[:200]}")
    data: dict[str, Any] | list[Any] = resp.json()
    return data


class SchwabBroker:
    def __init__(self, store: TokenStore, app_key: str, app_secret: str) -> None:
        self._store = store
        self._app_key = app_key
        self._app_secret = app_secret
        self._client: AsyncClient | None = None

    async def open(self) -> None:
        try:
            self._client = schwab_auth.client_from_access_functions(
                self._app_key,
                self._app_secret,
                token_read_func=self._store.read_func(),
                token_write_func=self._store.write_func(),
                asyncio=True,
            )
        except FileNotFoundError as e:
            raise BrokerUnauthorized(str(e)) from e
        self._client.set_timeout(30)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.session.aclose()
            self._client = None

    def _c(self) -> AsyncClient:
        if self._client is None:
            raise BrokerError("broker not opened")
        return self._client

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def account_hashes(self) -> list[str]:
        data = _raise_for(await self._c().get_account_numbers())
        assert isinstance(data, list)
        return [str(x["hashValue"]) for x in data]

    async def account(self, account_hash: str) -> AccountSnapshot:
        c = self._c()
        data = _raise_for(await c.get_account(account_hash, fields=[c.Account.Fields.POSITIONS]))
        assert isinstance(data, dict)
        return AccountSnapshot.from_payload(account_hash, data, self.now())

    async def orders(
        self, account_hash: str, from_dt: datetime, to_dt: datetime
    ) -> list[OrderRow]:
        data = _raise_for(
            await self._c().get_orders_for_account(
                account_hash, from_entered_datetime=from_dt, to_entered_datetime=to_dt
            )
        )
        assert isinstance(data, list)
        return [OrderRow.from_payload(o) for o in data]

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        data = _raise_for(await self._c().get_quotes(list(symbols)))
        assert isinstance(data, dict)
        return {s: Quote.from_payload(s, q) for s, q in data.items() if "quote" in q}

    async def market_window(self, d: date) -> MarketWindow:
        c = self._c()
        data = _raise_for(await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d))
        assert isinstance(data, dict)
        return MarketWindow.from_payload(d, data)

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
        end = self.now()
        start = end - timedelta(days=days * 2 + 7)  # weekends/holidays; trimmed below
        data = _raise_for(
            await self._c().get_price_history_every_day(
                symbol, start_datetime=start, end_datetime=end
            )
        )
        assert isinstance(data, dict)
        bars = [DailyBar.from_payload(c) for c in data.get("candles", [])]
        return bars[-days:]
