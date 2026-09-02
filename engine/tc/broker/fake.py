"""Fixture-backed Broker for tests and paper mode, plus the redacting recorder."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tc.broker.client import BrokerUnauthorized, SchwabBroker
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
        return {s: Quote.from_payload(s, data[s]) for s in symbols if s in data}

    async def market_window(self, d: date) -> MarketWindow:
        return MarketWindow.from_payload(d, self._load(f"hours-{d.isoformat()}.json"))

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
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


class Recorder:
    """Capture real payloads once, redacted, so tests replay reality."""

    def __init__(self, broker: SchwabBroker, out_dir: Path) -> None:
        self.broker = broker
        self.out = out_dir

    async def record(self, symbols: Sequence[str], d: date) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        c = self.broker._c()
        h = (await self.broker.account_hashes())[0]
        raw = {
            "account.json": (
                await c.get_account(h, fields=[c.Account.Fields.POSITIONS])
            ).json(),
            "orders.json": (await c.get_orders_for_account(h)).json(),
            "quotes.json": (await c.get_quotes(list(symbols))).json(),
            f"hours-{d.isoformat()}.json": (
                await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d)
            ).json(),
        }
        for s in symbols:
            raw[f"bars-{s}.json"] = (await c.get_price_history_every_day(s)).json()
        for name, payload in raw.items():
            (self.out / name).write_text(json.dumps(redact(payload), indent=1))
