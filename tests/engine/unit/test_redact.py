"""Direct coverage for the redacting recorder's core safety mechanism.

The contract test (tests/engine/contract/test_fixtures_redacted.py) only scans
the already-clean checked-in fixtures -- it proves nothing about the transform
itself. These tests exercise redact() and Recorder.record() directly against a
raw, unredacted payload.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tc.broker.client import BrokerError
from tc.broker.fake import Recorder, redact

# Built by concatenation so this file never contains a 32-hex literal (the
# contract test's regex doesn't scan tests/engine/unit, but keep the
# discipline anyway).
HEX32 = "ABCDEF0123456789" * 2


def test_redact_strips_identifiers_recursively() -> None:
    payload = {
        "securitiesAccount": {
            "type": "CASH",
            "accountNumber": "12345678",
            "hashValue": HEX32,
            "positions": [{"instrument": {"description": f"ref {HEX32} x"}}],
        },
        "list": [{"accountId": "9"}],
    }
    out = redact(payload)

    acct = out["securitiesAccount"]
    assert acct["accountNumber"] == "REDACTED"
    assert acct["hashValue"] == "HASH_REDACTED"
    assert out["list"][0]["accountId"] == "REDACTED"

    description = acct["positions"][0]["instrument"]["description"]
    assert "HASH_REDACTED" in description
    assert HEX32 not in description

    # Non-identifier keys/values pass through unchanged.
    assert acct["type"] == "CASH"


def _json_files(d: Path) -> list[Path]:
    """Filesystem reads live in sync helpers: ASYNC240 (rightly) objects to
    blocking pathlib calls inside an async test body."""
    return sorted(d.glob("*.json"))


def _read(p: Path) -> str:
    return p.read_text()


class _StubResp:
    """Enough of httpx.Response for _raise_for: status, text, json()."""

    def __init__(self, payload: Any, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return self._payload


class _Fields:
    POSITIONS = "positions"


class _Account:
    Fields = _Fields


class _Market:
    EQUITY = "equity"


class _MarketHours:
    Market = _Market


class _StubClient:
    Account = _Account
    MarketHours = _MarketHours

    def __init__(self, account_number: str, hash_value: str) -> None:
        self._account_number = account_number
        self._hash_value = hash_value

    async def get_account(self, account_hash: str, fields: Any = None) -> _StubResp:
        return _StubResp(
            {
                "securitiesAccount": {
                    "accountNumber": self._account_number,
                    "hashValue": self._hash_value,
                }
            }
        )

    async def get_orders_for_account(self, account_hash: str, **kw: Any) -> _StubResp:
        return _StubResp([{"orderId": 1, "hashValue": self._hash_value}])

    async def get_quotes(self, symbols: list[str]) -> _StubResp:
        return _StubResp(
            {s: {"quote": {"lastPrice": 1.0}, "accountId": self._account_number} for s in symbols}
        )

    async def get_market_hours(self, markets: Any, date: Any = None) -> _StubResp:
        return _StubResp({"equity": {"EQ": {"hashValue": self._hash_value}}})

    async def get_price_history_every_day(self, symbol: str, **kw: Any) -> _StubResp:
        return _StubResp({"candles": [], "hashValue": self._hash_value})


class _StubBroker:
    """Duck-typed stand-in for SchwabBroker: only what Recorder.record() uses."""

    def __init__(self) -> None:
        self._hash = HEX32
        self._client = _StubClient("12345678", self._hash)

    async def account_hashes(self) -> list[str]:
        return [self._hash]

    def _c(self) -> _StubClient:
        return self._client


async def test_recorder_writes_only_redacted_payloads(tmp_path: Path) -> None:
    stub: Any = _StubBroker()
    await Recorder(stub, tmp_path).record(["AMH"], date(2026, 9, 2))

    written = _json_files(tmp_path)
    assert len(written) == 5  # account, orders, quotes, hours, bars-AMH

    for p in written:
        text = _read(p)
        assert "12345678" not in text, f"{p} carries the raw account number"
        assert HEX32 not in text, f"{p} carries the raw hash"
        assert "REDACTED" in text, f"{p} was not redacted at all"


class _ErrorClient(_StubClient):
    async def get_account(self, account_hash: str, fields: Any = None) -> _StubResp:
        return _StubResp({"errors": ["bad request"]}, status_code=400, text="bad request")


class _ErrorBroker(_StubBroker):
    def __init__(self) -> None:
        super().__init__()
        self._client = _ErrorClient("12345678", self._hash)


async def test_recorder_refuses_error_body(tmp_path: Path) -> None:
    """A 400 body is valid JSON. Recording it produces a fixture that replays
    an error page as if it were reality — so the recorder must refuse it, and
    must leave nothing behind."""
    stub: Any = _ErrorBroker()
    with pytest.raises(BrokerError):
        await Recorder(stub, tmp_path).record(["AMH"], date(2026, 9, 2))
    assert _json_files(tmp_path) == []


async def test_recorder_refuses_wrong_shape(tmp_path: Path) -> None:
    class _ShapeClient(_StubClient):
        async def get_account(self, account_hash: str, fields: Any = None) -> _StubResp:
            return _StubResp({"unexpected": "shape"})

    class _ShapeBroker(_StubBroker):
        def __init__(self) -> None:
            super().__init__()
            self._client = _ShapeClient("12345678", self._hash)

    stub: Any = _ShapeBroker()
    with pytest.raises(BrokerError, match=re.escape("unexpected account.json payload")):
        await Recorder(stub, tmp_path).record(["AMH"], date(2026, 9, 2))
    assert _json_files(tmp_path) == []
