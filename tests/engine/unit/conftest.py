"""Fixtures shared by the desk tests. The only conftest in tests/engine:
every older test file builds its own store, and nothing here shadows a name
they use."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from tc.store.db import Store


@pytest_asyncio.fixture
async def desk_store() -> AsyncIterator[Store]:
    # ":memory:" -- nothing in the desk tests asserts durability, and an
    # in-memory database leaves no WAL behind for the next test.
    s = Store(Path(":memory:"))
    await s.open()
    yield s
    await s.close()
