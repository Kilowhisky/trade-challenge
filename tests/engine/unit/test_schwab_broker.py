"""The real SchwabBroker against a dead/absent token — the startup path, and
the re-open path that follows a phone re-auth.

Nothing here touches the network: with no token file, schwab-py's
client_from_access_functions calls our read_func, which raises
FileNotFoundError before any HTTP happens; with one, it builds a client from
the file alone. That mapping to BrokerUnauthorized is the whole contract the
scheduler relies on to enter blind mode instead of crashing — and the lazy
rebuild is what gets it back OUT of blind mode, because
client_from_access_functions binds the token it read for the client's life
and there is no other path that re-reads the file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl

from tc.broker.client import BrokerError, BrokerUnauthorized, SchwabBroker
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


def _write_token(store: TokenStore) -> None:
    """A schwab-py-shaped wrapped token, freshly created. Enough for
    client_from_access_functions to build a client offline — it constructs an
    OAuth session from these fields and makes no request."""
    now = int(time.time())
    token: dict[str, Any] = {
        "access_token": "a",
        "refresh_token": "r",
        "token_type": "Bearer",
        "scope": "api",
        "expires_in": 1800,
        "expires_at": now + 1800,
        "id_token": "i",
    }
    store.write({"creation_timestamp": now, "token": token})


async def test_c_builds_the_client_when_the_token_finally_appears(tmp_path: Path) -> None:
    """The cold-start case: the engine came up before the first token ever
    existed. `open()` failed then, and nothing calls it again — so the next
    call after the phone re-auth has to be the thing that re-reads the file.
    """
    store = TokenStore(tmp_path / "token.json", CFG, "k", "s")
    broker = SchwabBroker(store, "k", "s")
    with pytest.raises(BrokerUnauthorized):
        broker._c()

    _write_token(store)  # the phone lands on /oauth/callback

    client = broker._c()  # no second open() anywhere
    assert client is not None
    assert broker._c() is client  # and it is built once, not per call
    await broker.close()


async def test_a_401_drops_the_client_so_the_next_call_re_reads_the_token(
    tmp_path: Path,
) -> None:
    """schwab-py refreshes from the token it read at construction, so a client
    whose refresh token has died can never recover on its own. Dropping it on
    the 401 is what makes the NEXT call read the new file."""
    store = TokenStore(tmp_path / "token.json", CFG, "k", "s")
    broker = SchwabBroker(store, "k", "s")
    _write_token(store)
    first = broker._c()

    with pytest.raises(BrokerUnauthorized):
        await broker._guard(httpx.Response(401, text="invalid_grant"))

    assert broker._client is None
    _write_token(store)  # a re-auth writes a new token
    assert broker._c() is not first
    await broker.close()


async def test_a_500_keeps_the_client(tmp_path: Path) -> None:
    """Only 401 means "this token is finished". A 500 is Schwab having a bad
    minute, and rebuilding the client on every one of those would turn an
    upstream blip into a token-file read storm."""
    store = TokenStore(tmp_path / "token.json", CFG, "k", "s")
    broker = SchwabBroker(store, "k", "s")
    _write_token(store)
    first = broker._c()

    with pytest.raises(BrokerError):
        await broker._guard(httpx.Response(500, text="upstream"))

    assert broker._client is first
    await broker.close()
