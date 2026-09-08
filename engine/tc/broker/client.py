"""Read-only broker access. No write method exists in this module by design
(spec §5: the order path is a separate, later component)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

import httpx
from schwab import auth as schwab_auth
from schwab.client import AsyncClient

from tc.broker.models import (
    AccountSnapshot,
    DailyBar,
    Expiration,
    Instrument,
    MarketWindow,
    Mover,
    OptionChainView,
    OrderRow,
    Quote,
    VerboseQuote,
)
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
    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]: ...
    async def option_chain(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        strike_count: int,
        contract_type: str,
    ) -> OptionChainView: ...
    async def expiration_chain(self, symbol: str) -> list[Expiration]: ...
    async def instruments(self, query: str, projection: str) -> list[Instrument]: ...
    async def movers(self, index: str, direction: str) -> list[Mover]: ...
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
        """An eager warm-up, not the only way in.

        `client_from_access_functions` reads the token file once and binds it
        for the client's life, so a client built before the first token exists
        can never authenticate. `open()` therefore only *tries*: it may raise
        `BrokerUnauthorized` on a cold box, and that must not poison the
        broker — `_c()` builds on demand, so the next call after a phone
        re-auth re-reads the file and succeeds without anyone calling `open()`
        again.
        """
        self._c()

    async def close(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            await c.session.aclose()

    def _c(self) -> AsyncClient:
        """The client, built on demand. `self._client is None` means "no token
        has been read yet, or the last read was rejected" — either way the
        token file is read again here, which is the whole re-open path."""
        if self._client is None:
            try:
                client = schwab_auth.client_from_access_functions(
                    self._app_key,
                    self._app_secret,
                    token_read_func=self._store.read_func(),
                    token_write_func=self._store.write_func(),
                    asyncio=True,
                )
            except FileNotFoundError as e:
                raise BrokerUnauthorized(str(e)) from e
            client.set_timeout(30)
            self._client = client
        return self._client

    async def _guard(self, resp: httpx.Response) -> dict[str, Any] | list[Any]:
        """`_raise_for`, plus: a 401 drops the bound client.

        The token behind a live client is dead for good — schwab-py refreshes
        from the file it read at construction, so the client cannot recover on
        its own. Dropping it means the next call re-reads the file, which is
        what turns a phone re-auth into a working engine without a restart.
        """
        try:
            return _raise_for(resp)
        except BrokerUnauthorized:
            await self.close()
            raise

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def account_hashes(self) -> list[str]:
        data = await self._guard(await self._c().get_account_numbers())
        assert isinstance(data, list)
        return [str(x["hashValue"]) for x in data]

    async def account(self, account_hash: str) -> AccountSnapshot:
        c = self._c()
        data = await self._guard(
            await c.get_account(account_hash, fields=[c.Account.Fields.POSITIONS])
        )
        assert isinstance(data, dict)
        return AccountSnapshot.from_payload(account_hash, data, self.now())

    async def orders(
        self, account_hash: str, from_dt: datetime, to_dt: datetime
    ) -> list[OrderRow]:
        data = await self._guard(
            await self._c().get_orders_for_account(
                account_hash, from_entered_datetime=from_dt, to_entered_datetime=to_dt
            )
        )
        assert isinstance(data, list)
        return [OrderRow.from_payload(o) for o in data]

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        data = await self._guard(await self._c().get_quotes(list(symbols)))
        assert isinstance(data, dict)
        return {s: Quote.from_payload(s, q) for s, q in data.items() if "quote" in q}

    async def quotes_verbose(self, symbols: Sequence[str]) -> dict[str, VerboseQuote]:
        """Every field the universe screen needs, in one call.

        v2 could not ask for these: the schwab-mcp wrapper ignored `fields=`,
        so the only way to get avg10DaysVolume / fundLeverageFactor / the
        `regular` block was a verbose response whose ~260KB body had to be
        written to a file and parsed by a script the model was forbidden to
        read. Here it is an ordinary typed read the model never sees at all.
        """
        if not symbols:
            return {}
        c = self._c()
        fields = [
            c.Quote.Fields.QUOTE,
            c.Quote.Fields.FUNDAMENTAL,
            c.Quote.Fields.REFERENCE,
            c.Quote.Fields.REGULAR,
        ]
        data = await self._guard(await c.get_quotes(list(symbols), fields=fields))
        assert isinstance(data, dict)
        return {s: VerboseQuote.from_payload(s, b) for s, b in data.items()}

    async def option_chain(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        strike_count: int,
        contract_type: str,
    ) -> OptionChainView:
        c = self._c()
        data = await self._guard(
            await c.get_option_chain(
                symbol,
                contract_type=c.Options.ContractType[contract_type],
                strike_count=strike_count,
                from_date=from_date,
                to_date=to_date,
            )
        )
        assert isinstance(data, dict)
        return OptionChainView.from_payload(symbol, data)

    async def expiration_chain(self, symbol: str) -> list[Expiration]:
        data = await self._guard(await self._c().get_option_expiration_chain(symbol))
        assert isinstance(data, dict)
        return [Expiration.from_payload(e) for e in data.get("expirationList", [])]

    async def instruments(self, query: str, projection: str) -> list[Instrument]:
        c = self._c()
        data = await self._guard(
            await c.get_instruments(query, c.Instrument.Projection(projection))
        )
        assert isinstance(data, dict)
        return [Instrument.from_payload(i) for i in data.get("instruments", [])]

    async def movers(self, index: str, direction: str) -> list[Mover]:
        c = self._c()
        order = (
            c.Movers.SortOrder.PERCENT_CHANGE_UP
            if direction == "up"
            else c.Movers.SortOrder.PERCENT_CHANGE_DOWN
        )
        data = await self._guard(await c.get_movers(c.Movers.Index(index), sort_order=order))
        assert isinstance(data, dict)
        return [Mover.from_payload(m) for m in data.get("screeners", [])]

    async def market_window(self, d: date) -> MarketWindow:
        c = self._c()
        data = await self._guard(await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d))
        assert isinstance(data, dict)
        return MarketWindow.from_payload(d, data)

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
        if days <= 0:
            raise ValueError("days must be positive")
        end = self.now()
        start = end - timedelta(days=days * 2 + 7)  # weekends/holidays; trimmed below
        data = await self._guard(
            await self._c().get_price_history_every_day(
                symbol, start_datetime=start, end_datetime=end
            )
        )
        assert isinstance(data, dict)
        bars = [DailyBar.from_payload(c) for c in data.get("candles", [])]
        return bars[-days:]


if TYPE_CHECKING:
    # Static conformance. Broker is a plain Protocol, so nothing at runtime
    # ties SchwabBroker to it — rename or re-signature a method and every
    # caller keeps type-checking against the protocol while the real object no
    # longer satisfies it. This assignment makes mypy --strict fail instead.
    _schwab_conforms: Broker = cast(SchwabBroker, None)
