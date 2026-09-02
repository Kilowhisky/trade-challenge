import re
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ACCOUNT_NUMBER = re.compile(r'"accountNumber"\s*:\s*"(?!REDACTED")')
LONG_HEX = re.compile(r"\b[0-9A-F]{32,}\b")


def test_no_fixture_carries_an_identifier() -> None:
    for p in FIX.rglob("*.json"):
        text = p.read_text()
        assert not ACCOUNT_NUMBER.search(text), f"{p} carries an account number"
        assert not LONG_HEX.search(text), f"{p} carries a hash-like token"
        assert "refresh_token" not in text, f"{p} carries a token"
