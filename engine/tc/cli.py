"""`tc` — operator commands for the engine. Everything here is read-only at
the broker except auth-complete, which writes the token file."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml
from authlib.common.errors import AuthlibBaseError
from pydantic import ValidationError
from pydantic_settings import SettingsError

from tc.broker.client import BrokerError, SchwabBroker
from tc.broker.fake import Recorder
from tc.broker.token import NoAuthInProgress, TokenStore
from tc.config import Settings, load_settings
from tc.rules.consistency import run_checks


def _settings(ns: argparse.Namespace) -> Settings:
    # The default .env lives beside config.yml, not in whatever directory the
    # operator (or a cron line, or systemd) happened to start us from. An
    # explicit --env still wins.
    config = Path(ns.config)
    if ns.env:
        env: Path | None = Path(ns.env)
    else:
        beside = config.resolve().parent / ".env"
        env = beside if beside.exists() else None
    return load_settings(config, env)


def _token_store(s: Settings) -> TokenStore:
    return TokenStore(
        s.engine.data_dir / "token.json", s.token, s.schwab_app_key, s.schwab_app_secret
    )


def _token_line(store: TokenStore) -> tuple[str, int]:
    state = store.state()
    age = store.age_days()
    left = store.days_until_dead()
    action = {
        "fresh": "none",
        "reauth_due": "REAUTH NOW: tc auth-url, open on phone",
        "dead": "DEAD — account is blind until re-auth",
        "absent": "no token — run tc auth-url",
    }[state]
    fmt: Callable[[float | None], str] = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    line = f"state={state} age_days={fmt(age)} days_until_dead={fmt(left)} action={action}"
    rc = {"fresh": 0, "reauth_due": 2, "dead": 3, "absent": 3}[state]
    return line, rc


def cmd_check_consistency(ns: argparse.Namespace) -> int:
    rep = run_checks(Path(ns.repo))
    for f in rep.findings:
        loc = f"{f.path}:{f.line}" if f.line else (f.path or "-")
        print(f"FAIL  [{f.check}] {loc} {f.message}")
    for name, n in rep.checked.items():
        print(f"  {name}: {n} checked")
    if rep.ok:
        print("CONSISTENT")
        return 0
    print(
        f"{len(rep.findings)} inconsistency(ies). "
        "rules.yml is the source of truth; fix the other side."
    )
    return 1


def cmd_token_status(ns: argparse.Namespace) -> int:
    line, rc = _token_line(_token_store(_settings(ns)))
    print(line)
    return rc


def cmd_auth_url(ns: argparse.Namespace) -> int:
    print(_token_store(_settings(ns)).begin_auth())
    return 0


def cmd_auth_complete(ns: argparse.Namespace) -> int:
    store = _token_store(_settings(ns))
    store.complete_auth(ns.received_url)
    line, rc = _token_line(store)
    print(line)
    return rc


async def _record(s: Settings, out: Path, symbols: list[str]) -> None:
    broker = SchwabBroker(_token_store(s), s.schwab_app_key, s.schwab_app_secret)
    try:
        await broker.open()
        await Recorder(broker, out).record(symbols, datetime.now(UTC).date())
    finally:
        await broker.close()


def cmd_record_fixtures(ns: argparse.Namespace) -> int:
    asyncio.run(_record(_settings(ns), Path(ns.out), [x for x in ns.symbols.split(",") if x]))
    print(f"recorded (redacted) to {ns.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tc")
    p.add_argument("--config", default="config.yml")
    p.add_argument("--env", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check-consistency")
    c.add_argument("--repo", default=".")
    c.set_defaults(fn=cmd_check_consistency)

    sub.add_parser("token-status").set_defaults(fn=cmd_token_status)
    sub.add_parser("auth-url").set_defaults(fn=cmd_auth_url)

    a = sub.add_parser("auth-complete")
    a.add_argument("received_url")
    a.set_defaults(fn=cmd_auth_complete)

    r = sub.add_parser("record-fixtures")
    r.add_argument("--out", required=True)
    r.add_argument("--symbols", required=True)
    r.set_defaults(fn=cmd_record_fixtures)

    return p


def _validation_summary(exc: ValidationError) -> str:
    """Field names and error kinds only -- never the offending value."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc} ({err['type']})")
    return ", ".join(parts)


def _one_line(exc: Exception) -> str:
    """Collapse a multi-line exception (yaml points at the offending token on
    three lines) into the single `tc: ...` line the operator contract promises."""
    return " ".join(str(exc).split())


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        rc: int = ns.fn(ns)
        return rc
    # ValidationError first: it is a ValueError subclass, and it is the one
    # case that must NOT print the offending value.
    except ValidationError as e:
        print(f"tc: settings invalid: {_validation_summary(e)}", file=sys.stderr)
        return 4
    except (
        FileNotFoundError,
        NoAuthInProgress,
        BrokerError,
        AuthlibBaseError,
        yaml.YAMLError,
        SettingsError,
        ValueError,
    ) as e:
        print(f"tc: {_one_line(e)}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
