"""The Schwab token: one file, one owner, and the phone re-auth exchange.

Facts this file is built on (verified 2026-09-02, spec §8): the refresh token
hard-expires `hard_expiry_days` after the ORIGINAL login and cannot be renewed
programmatically; the callback may be any HTTPS URL Schwab has on file; the
5-day forced re-auth and the 127.0.0.1-only callback were the old wrapper's
choices, not Schwab's. schwab-py's own on-disk shape is
{"creation_timestamp": int, "token": {...}} — we keep it verbatim so
client_from_access_functions can consume it unchanged.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from schwab import auth as schwab_auth

from tc.config import TokenConfig

TokenState = Literal["absent", "fresh", "reauth_due", "dead"]
DAY = 86400.0


class NoAuthInProgress(Exception):  # noqa: N818 -- name fixed by the task-6 interface contract
    """complete_auth was called with no persisted auth context."""


class TokenStore:
    def __init__(
        self,
        path: Path,
        cfg: TokenConfig,
        app_key: str,
        app_secret: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.cfg = cfg
        self._app_key = app_key
        self._app_secret = app_secret
        self._clock = clock
        self._ctx_path = path.with_name("auth-context.json")

    # --- file ------------------------------------------------------------
    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            raw: Any = json.loads(self.path.read_text())
            has_meta = isinstance(raw, dict) and "creation_timestamp" in raw and "token" in raw
            if not has_meta:
                raise ValueError("token file missing schwab-py metadata")
            data: dict[str, Any] = raw
            return data
        except (ValueError, OSError):
            # A corrupt token is worse than no token: it would crash every read.
            # Quarantine it (never delete evidence) and report absent.
            self.path.replace(self.path.with_name(self.path.name + ".corrupt"))
            return None

    def write(self, wrapped: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(wrapped, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def read_func(self) -> Callable[[], dict[str, Any]]:
        def _read() -> dict[str, Any]:
            data = self.read()
            if data is None:
                raise FileNotFoundError(f"no token at {self.path}")
            return data
        return _read

    def write_func(self) -> Callable[..., None]:
        # schwab-py calls token_write_func(wrapped_token, *args, **kwargs)
        def _write(wrapped: dict[str, Any], *_: Any, **__: Any) -> None:
            self.write(wrapped)
        return _write

    # --- age / state -------------------------------------------------------
    def age_days(self) -> float | None:
        data = self.read()
        if data is None:
            return None
        return (self._clock() - float(data["creation_timestamp"])) / DAY

    def days_until_dead(self) -> float | None:
        age = self.age_days()
        return None if age is None else self.cfg.hard_expiry_days - age

    def state(self) -> TokenState:
        age = self.age_days()
        if age is None:
            return "absent"
        if age >= self.cfg.hard_expiry_days:
            return "dead"
        if age >= self.cfg.reauth_after_days:
            return "reauth_due"
        return "fresh"

    # --- re-auth ----------------------------------------------------------
    def begin_auth(self) -> str:
        ctx = schwab_auth.get_auth_context(self._app_key, str(self.cfg.callback_url))
        self._ctx_path.parent.mkdir(parents=True, exist_ok=True)
        self._ctx_path.write_text(
            json.dumps({"callback_url": ctx.callback_url, "state": ctx.state})
        )
        return str(ctx.authorization_url)

    def complete_auth(self, received_url: str) -> None:
        if not self._ctx_path.exists():
            raise NoAuthInProgress("no auth-context.json; call begin_auth first")
        saved = json.loads(self._ctx_path.read_text())
        ctx = schwab_auth.AuthContext(saved["callback_url"], "", saved["state"])
        # token_write_func receives the schwab-py WRAPPED token (metadata added
        # by TokenMetadata.wrapped_token_write_func) — exactly our file shape.
        schwab_auth.client_from_received_url(
            self._app_key, self._app_secret, ctx, received_url,
            token_write_func=self.write_func(), asyncio=False,
        )
        self._ctx_path.unlink(missing_ok=True)
