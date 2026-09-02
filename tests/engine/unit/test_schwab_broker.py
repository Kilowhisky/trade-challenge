"""The real SchwabBroker against a dead/absent token — the startup path.

Nothing here touches the network: with no token file, schwab-py's
client_from_access_functions calls our read_func, which raises
FileNotFoundError before any HTTP happens. That mapping to BrokerUnauthorized
is the whole contract the scheduler relies on to enter blind mode instead of
crashing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import AnyHttpUrl

from tc.broker.client import BrokerUnauthorized, SchwabBroker
from tc.broker.token import TokenStore
from tc.config import TokenConfig

CFG = TokenConfig(
    reauth_after_days=5,
    hard_expiry_days=7,
    callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"),
)


async def test_open_without_token_is_unauthorized_and_close_is_safe(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "token.json", CFG, "k", "s")
    broker = SchwabBroker(store, "k", "s")
    with pytest.raises(BrokerUnauthorized):
        await broker.open()
    # close() after a failed open must not raise: the finally-block in every
    # caller runs it unconditionally.
    await broker.close()
