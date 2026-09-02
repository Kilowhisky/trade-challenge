"""Fixture-backed Broker for tests and paper mode, plus the redacting recorder."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tc.broker.client import (
    Broker,
    BrokerError,
    BrokerUnauthorized,
    SchwabBroker,
    _raise_for,
)
from tc.broker.models import AccountSnapshot, DailyBar, MarketWindow, OrderRow, Quote

HASH_RE = re.compile(r"\b[0-9A-F]{32,}\b")


class FakeBroker:
    def __init__(self, fixture_dir: Path, frozen_now: datetime) -> None:
        self.dir = fixture_dir
        self._now = frozen_now

    def _load(self, name: str) -> Any:
        if (self.dir / "unauthorized").exists():
            raise BrokerUnauthorized("fixture: unauthorized")
        return json.loads((self.dir / name).read_text())

    def now(self) -> datetime:
        return self._now

    async def account_hashes(self) -> list[str]:
        self._load("account.json")
        return ["HASH_REDACTED"]

    async def account(self, account_hash: str) -> AccountSnapshot:
        return AccountSnapshot.from_payload(account_hash, self._load("account.json"), self._now)

    async def orders(
        self, account_hash: str, from_dt: datetime, to_dt: datetime
    ) -> list[OrderRow]:
        return [OrderRow.from_payload(o) for o in self._load("orders.json")]

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        data = self._load("quotes.json")
        # Same filter as the real client: Schwab returns an entry with an
        # "invalidSymbols"-style body and no "quote" block for an unknown or
        # halted symbol, and the fake must not raise where the real one skips.
        return {
            s: Quote.from_payload(s, data[s])
            for s in symbols
            if s in data and "quote" in data[s]
        }

    async def market_window(self, d: date) -> MarketWindow:
        return MarketWindow.from_payload(d, self._load(f"hours-{d.isoformat()}.json"))

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
        if days <= 0:
            raise ValueError("days must be positive")
        candles = self._load(f"bars-{symbol}.json").get("candles", [])
        return [DailyBar.from_payload(c) for c in candles][-days:]


def redact(obj: Any) -> Any:
    """Strip identifiers before a payload is written anywhere tracked."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in {"accountNumber", "accountId"}:
                out[k] = "REDACTED"
            elif k == "hashValue":
                out[k] = "HASH_REDACTED"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return HASH_RE.sub("HASH_REDACTED", obj)
    return obj


def _check_shape(name: str, payload: dict[str, Any] | list[Any]) -> None:
    """Reject anything that is not the payload the fixture name promises."""
    ok: bool
    if name == "account.json":
        ok = isinstance(payload, dict) and "securitiesAccount" in payload
    elif name == "orders.json":
        ok = isinstance(payload, list)
    elif name == "quotes.json":
        ok = isinstance(payload, dict)
    elif name.startswith("hours-"):
        ok = isinstance(payload, dict) and "equity" in payload
    else:  # bars-<symbol>.json
        ok = isinstance(payload, dict) and "candles" in payload
    if not ok:
        raise BrokerError(f"unexpected {name} payload")


class Recorder:
    """Capture real payloads once, redacted, so tests replay reality."""

    def __init__(self, broker: SchwabBroker, out_dir: Path) -> None:
        self.broker = broker
        self.out = out_dir

    async def record(self, symbols: Sequence[str], d: date) -> None:
        c = self.broker._c()
        h = (await self.broker.account_hashes())[0]
        # Every response goes through _raise_for, not .json(): a 401 or a 500
        # body parses as perfectly good JSON, and writing it produces a fixture
        # that "replays reality" as an error page. Shapes are checked before
        # anything is written, and nothing is written unless all of them pass —
        # a half-recorded fixture directory is worse than none.
        raw = {
            "account.json": _raise_for(
                await c.get_account(h, fields=[c.Account.Fields.POSITIONS])
            ),
            "orders.json": _raise_for(await c.get_orders_for_account(h)),
            "quotes.json": _raise_for(await c.get_quotes(list(symbols))),
            f"hours-{d.isoformat()}.json": _raise_for(
                await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d)
            ),
        }
        for s in symbols:
            raw[f"bars-{s}.json"] = _raise_for(await c.get_price_history_every_day(s))
        for name, payload in raw.items():
            _check_shape(name, payload)
        self.out.mkdir(parents=True, exist_ok=True)
        for name, payload in raw.items():
            (self.out / name).write_text(json.dumps(redact(payload), indent=1))


if TYPE_CHECKING:
    # Same static conformance guard as client.py: the fake is only useful as a
    # stand-in if it still satisfies the protocol the real broker does.
    _fake_conforms: Broker = cast(FakeBroker, None)
