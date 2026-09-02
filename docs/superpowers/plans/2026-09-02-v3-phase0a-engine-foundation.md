# v3 Phase 0a — Engine Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed, tested foundation of the v3 engine — config, rules, arithmetic, consistency checker, token store with the phone re-auth exchange, broker client + fake, SQLite store, and the ET clock — so that Phase 0b can wire the loops and service on top of it.

**Architecture:** One Python package `tc` under `engine/`, asyncio, pydantic-strict models, `Decimal` everywhere money appears, one SQLite writer. Nothing in this plan touches the broker with a write call or runs on the server; every task is unit/property tested against a fake or fixtures and passes `mypy --strict` and `ruff`.

**Tech Stack:** Python 3.12, `schwab-py` 1.5.1, `pydantic` 2 + `pydantic-settings`, `PyYAML`, `aiosqlite`, `httpx`, `hypothesis`, `pytest` + `pytest-asyncio`, `mypy`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` (§3, §5.2, §6, §8, §9, §10). Phase 0b (loops, scheduler, HTTP, Discord webhook, healthchecks, shadow diff, docker, host probe) is a separate plan that consumes the interfaces produced here.

## Global Constraints

- Python **3.12**; `mypy --strict` and `ruff check` clean after every task, over `tc` **and** `../tests/engine`; `pytest -q` green before every commit. The gate is `cd engine && pytest -q && mypy && ruff check . ../tests/engine`.
- All money is `decimal.Decimal`, quantized to cents with `ROUND_FLOOR` where a cap is computed (spec §5.2: "caps in `Decimal`, floored"). Never `float` for money.
- Every model is `pydantic.BaseModel` with `model_config = ConfigDict(extra="forbid")` unless a task says otherwise. (Not `strict=True`: the models coerce Schwab's JSON numbers into `Decimal` deliberately, which strict mode forbids.)
- `rules.yml` stays the single source of truth; **no rule number is hard-coded** in `engine/` (the ported consistency check enforces this on `engine/**/*.py` too).
- The public repo must never contain an account number, account hash, token, or secret (`CLAUDE.md §7.4`). Fixtures are redacted; a test asserts it.
- No file under `engine/` shells out to bash. No `subprocess` in this plan.
- Work on branch `feat/v3-engine`. Stage explicit paths only; never `git add -A` (a laptop `-A` once deleted server files — memory `store-commits-never-add-all`).
- Commit message trailer on every commit: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File Structure

```
engine/
  pyproject.toml                 package `tc`, deps, tool config (ruff, mypy, pytest)
  tc/__init__.py
  tc/config.py                   Settings (pydantic-settings): config.yml + .env, fail-fast
  tc/money.py                    Decimal helpers: D(), cents(), floor_cents()
  tc/rules/__init__.py
  tc/rules/model.py              Rules: typed view over rules.yml (manual + strategy)
  tc/rules/arith.py              pure arithmetic: caps, stop geometry, HWM, drawdown, reserve
  tc/rules/consistency.py        port of scripts/check-consistency.sh + engine checks
  tc/broker/__init__.py
  tc/broker/models.py            AccountSnapshot, Position, OrderRow, Quote, MarketWindow, DailyBar
  tc/broker/token.py             TokenStore: file, age, state, auth URL, callback exchange
  tc/broker/client.py            Broker protocol + SchwabBroker on schwab-py AsyncClient
  tc/broker/fake.py              FakeBroker (fixture-backed) + redacting recorder
  tc/store/__init__.py
  tc/store/db.py                 Store: aiosqlite single writer, migrations, append-only triggers
  tc/store/schema.sql            DDL for Phase 0 tables
  tc/clock.py                    ET now(), SessionPhase (PRE/RTH/POST), trading-day gate
  tc/cli.py                      `tc` entry: check-consistency, token-status, auth-url, record-fixtures
config.yml                       checked-in non-secret config (engine section)
tests/engine/                    pytest tree mirroring tc/ (unit/, property/, contract/)
tests/engine/fixtures/           redacted JSON fixtures for FakeBroker
```

Every task below creates its files under `engine/` and its tests under `tests/engine/`. Run all commands from the repo root with `cd engine && …` where shown.

---

### Task 1: Package scaffold, tooling, and `Settings`

**Files:**
- Create: `engine/pyproject.toml`, `engine/tc/__init__.py`, `engine/tc/config.py`, `config.yml`
- Test: `tests/engine/unit/test_config.py`

**Interfaces:**
- Produces: `tc.config.Settings` with fields listed below; `tc.config.load_settings(config_path: Path, env_file: Path | None) -> Settings`.

- [ ] **Step 1: Create `engine/pyproject.toml`**

```toml
[project]
name = "tc"
version = "0.1.0"
description = "trade-challenge v3 engine"
requires-python = ">=3.12"
dependencies = [
  "schwab-py==1.5.1",
  "pydantic>=2.7,<3",
  "pydantic-settings>=2.3,<3",
  "PyYAML>=6.0",
  "aiosqlite>=0.20",
  "httpx>=0.28",
]

[project.optional-dependencies]
dev = [
  "pytest>=8", "pytest-asyncio>=0.23", "hypothesis>=6.100",
  "mypy>=1.10", "ruff>=0.5", "types-PyYAML",
]

[project.scripts]
tc = "tc.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["tc*"]

[tool.setuptools.package-data]
tc = ["store/schema.sql"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "ASYNC", "S", "RUF"]
ignore = ["S101"]  # asserts in tests

[tool.ruff.lint.per-file-ignores]
"../tests/engine/**/*.py" = ["E501"]  # wide fixture literals; every other rule still applies

[tool.mypy]
strict = true
files = ["tc", "../tests/engine"]   # the tests are type-checked too
plugins = ["pydantic.mypy"]         # BaseSettings fields come from the env, not __init__
python_version = "3.12"
ignore_missing_imports = true  # schwab-py ships no stubs

[tool.pytest.ini_options]
testpaths = ["../tests/engine"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `engine/tc/__init__.py`**

```python
"""trade-challenge v3 engine."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Install in a venv and confirm the toolchain runs**

Run:
```bash
cd engine && python3.12 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && ruff --version && mypy --version && pytest --version
```
Expected: three version lines, no errors. Add `engine/.venv/` to `.gitignore` (append the line `engine/.venv/`).

- [ ] **Step 4: Write the failing config test**

`tests/engine/unit/test_config.py`:
```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from tc.config import Settings, load_settings

CONFIG = """
engine:
  timezone: America/New_York
  data_dir: /tmp/tc-test-data
  http_bind: 127.0.0.1:8080
  reserve_usd: "900.00"
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
"""

ENV = """
TC_SCHWAB_APP_KEY=k
TC_SCHWAB_APP_SECRET=s
TC_DISCORD_WEBHOOK_URL=https://discord.example/hook
"""


def _write(tmp_path: Path, cfg: str = CONFIG, env: str = ENV) -> tuple[Path, Path]:
    c = tmp_path / "config.yml"
    e = tmp_path / ".env"
    c.write_text(cfg)
    e.write_text(env)
    return c, e


def test_loads_yaml_and_env(tmp_path: Path) -> None:
    c, e = _write(tmp_path)
    s = load_settings(c, e)
    assert isinstance(s, Settings)
    assert s.engine.data_dir == Path("/tmp/tc-test-data")
    assert s.token.reauth_after_days == 5
    assert s.schwab_app_key == "k"
    assert str(s.discord_webhook_url).startswith("https://discord.example")


def test_missing_secret_fails_fast(tmp_path: Path) -> None:
    c, e = _write(tmp_path, env="TC_SCHWAB_APP_KEY=k\n")
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_unknown_yaml_key_rejected(tmp_path: Path) -> None:
    c, e = _write(tmp_path, cfg=CONFIG + "  bogus: 1\n")
    with pytest.raises(ValidationError):
        load_settings(c, e)


def test_reauth_must_precede_hard_expiry(tmp_path: Path) -> None:
    c, e = _write(tmp_path, cfg=CONFIG.replace("reauth_after_days: 5", "reauth_after_days: 8"))
    with pytest.raises(ValidationError):
        load_settings(c, e)
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'tc.config'`

- [ ] **Step 6: Implement `engine/tc/config.py`**

```python
"""Engine settings: non-secret config.yml + secrets from .env / environment.

Fail-fast by design (spec §9): a missing key raises at startup, never at the
moment an order or a re-auth needs it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/New_York"
    data_dir: Path
    http_bind: str = "127.0.0.1:8080"
    reserve_usd: Decimal = Decimal("900.00")


class TokenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reauth_after_days: int = Field(ge=1)
    hard_expiry_days: int = Field(ge=1)
    callback_url: AnyHttpUrl

    @model_validator(mode="after")
    def _ordered(self) -> TokenConfig:
        if self.reauth_after_days >= self.hard_expiry_days:
            raise ValueError("token.reauth_after_days must be < token.hard_expiry_days")
        return self


class FileConfig(BaseModel):
    """The whole of config.yml. Unknown keys are errors, not warnings."""

    model_config = ConfigDict(extra="forbid")
    engine: EngineConfig
    token: TokenConfig


class Settings(BaseSettings):
    """Secrets come only from the environment / .env, prefixed TC_."""

    model_config = SettingsConfigDict(env_prefix="TC_", extra="ignore")

    schwab_app_key: str
    schwab_app_secret: str
    discord_webhook_url: AnyHttpUrl
    healthchecks_base_url: AnyHttpUrl | None = None

    # populated from config.yml, not env
    engine: EngineConfig
    token: TokenConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_settings(config_path: Path, env_file: Path | None = None) -> Settings:
    file_cfg = FileConfig.model_validate(_read_yaml(config_path))
    kwargs: dict[str, Any] = {"engine": file_cfg.engine, "token": file_cfg.token}
    if env_file is not None:
        return Settings(_env_file=str(env_file), **kwargs)  # type: ignore[call-arg]
    return Settings(**kwargs)
```

- [ ] **Step 7: Run tests and type checks**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_config.py && mypy && ruff check . ../tests/engine`
Expected: `4 passed`, mypy `Success`, ruff clean.

- [ ] **Step 8: Create the checked-in `config.yml` at the repo root**

```yaml
# config.yml — non-secret engine configuration (spec §9). Secrets live in .env
# as TC_* variables and are never written here.
engine:
  timezone: America/New_York
  data_dir: /data
  http_bind: 127.0.0.1:8080
  reserve_usd: "900.00"        # CLAUDE.md header: the one fixed dollar quantity
token:
  reauth_after_days: 5         # engine posts the auth URL to Discord at this age
  hard_expiry_days: 7          # Schwab's absolute refresh-token lifetime (verified 2026-09-02)
  callback_url: https://REPLACE-ME.ts.net/oauth/callback   # set after Tailscale is up
```

- [ ] **Step 9: Commit**

```bash
git add engine/pyproject.toml engine/tc/__init__.py engine/tc/config.py config.yml tests/engine/unit/test_config.py .gitignore
git commit -m "engine: package scaffold and fail-fast Settings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `Rules` — typed view over `rules.yml`

**Files:**
- Create: `engine/tc/rules/__init__.py`, `engine/tc/rules/model.py`, `engine/tc/money.py`
- Test: `tests/engine/unit/test_rules_model.py`

**Interfaces:**
- Produces: `tc.rules.model.Rules` with `.manual` and `.strategy` (both `Mapping[str, Decimal]`), typed properties used by later tasks: `single_position_pct`, `option_single_position_pct`, `option_open_premium_pct`, `leveraged_aggregate_pct`, `stop_atr_multiple`, `stop_trigger_min_pct`, `stop_trigger_max_pct`, `stop_limit_pct_below_trigger`, `halt_multiple_of_hwm`, `option_close_at_dte`, `option_min_dte`, `leveraged_max_hold_sessions`, `settlement_reserve_usd`, `min_share_price_usd`, `max_orders_per_symbol_per_session`, `max_replaces_per_stop_per_day`; `Rules.load(path: Path) -> Rules`; `Rules.get(section: str, key: str) -> Decimal` (raises `KeyError`).
- Produces: `tc.money.D(x) -> Decimal`, `tc.money.floor_cents(d) -> Decimal`, `tc.money.cents(d) -> Decimal` (ROUND_HALF_EVEN to 0.01).

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_rules_model.py`:
```python
from decimal import Decimal
from pathlib import Path

import pytest

from tc.money import D, cents, floor_cents
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]


def test_loads_real_rules_yml() -> None:
    r = Rules.load(REPO / "rules.yml")
    assert r.single_position_pct == Decimal("35")
    assert r.halt_multiple_of_hwm == Decimal("0.80")
    assert r.settlement_reserve_usd == Decimal("900.00")
    assert r.get("strategy", "option_min_delta") == Decimal("0.50")


def test_unknown_key_raises() -> None:
    r = Rules.load(REPO / "rules.yml")
    with pytest.raises(KeyError):
        r.get("manual", "does_not_exist")


def test_rejects_nesting_deeper_than_two(tmp_path: Path) -> None:
    p = tmp_path / "rules.yml"
    p.write_text("manual:\n  a:\n    b: 1\nstrategy: {}\n")
    with pytest.raises(ValueError):
        Rules.load(p)


def test_rejects_non_numeric_value(tmp_path: Path) -> None:
    p = tmp_path / "rules.yml"
    p.write_text("manual:\n  a: yes\nstrategy: {}\n")
    with pytest.raises(ValueError):
        Rules.load(p)


def test_money_helpers() -> None:
    assert D("1.005") == Decimal("1.005")
    assert floor_cents(Decimal("1.009")) == Decimal("1.00")
    assert cents(Decimal("1.005")) == Decimal("1.00")  # banker's rounding
    assert cents(Decimal("1.015")) == Decimal("1.02")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_rules_model.py`
Expected: FAIL, `No module named 'tc.money'`.

- [ ] **Step 3: Implement `engine/tc/money.py`**

```python
"""Decimal helpers. Money is never a float anywhere in the engine."""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


def D(x: str | int | Decimal) -> Decimal:  # noqa: N802 — deliberate short name
    return x if isinstance(x, Decimal) else Decimal(str(x))


def floor_cents(d: Decimal) -> Decimal:
    """Round DOWN to the cent. Used for every cap so a cap never rounds up."""
    return d.quantize(CENT, rounding=ROUND_FLOOR)


def cents(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_EVEN)
```

- [ ] **Step 4: Implement `engine/tc/rules/model.py`** (and an empty `engine/tc/rules/__init__.py`)

```python
"""Typed view over rules.yml — the single source of truth for every parameter.

The file is deliberately two levels deep and flat within a section (rules.yml
header). This loader enforces that shape: anything deeper or non-numeric is a
load error, because a rule that cannot be read must fail loudly, never
degrade into "no cap applied" (scripts/lib-rules.sh, rule_get).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

SECTIONS = ("manual", "strategy")


def _to_decimal(section: str, key: str, value: Any) -> Decimal:
    # YAML turns 0.80 into a float; go through str() so the text is preserved.
    if isinstance(value, bool) or value is None or isinstance(value, dict | list):
        raise ValueError(f"rules.yml {section}.{key}: value must be numeric, got {value!r}")
    try:
        return Decimal(str(value))
    except InvalidOperation as e:
        raise ValueError(f"rules.yml {section}.{key}: not a number: {value!r}") from e


@dataclass(frozen=True)
class Rules:
    manual: Mapping[str, Decimal]
    strategy: Mapping[str, Decimal]
    source: Path

    @classmethod
    def load(cls, path: Path) -> Rules:
        with path.open() as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict) or set(raw) != set(SECTIONS):
            raise ValueError(f"{path}: expected exactly the sections {SECTIONS}")
        parsed: dict[str, dict[str, Decimal]] = {}
        for section in SECTIONS:
            body = raw[section] or {}
            if not isinstance(body, dict):
                raise ValueError(f"{path}: section {section} must be a mapping")
            parsed[section] = {k: _to_decimal(section, k, v) for k, v in body.items()}
        return cls(
            manual=MappingProxyType(parsed["manual"]),
            strategy=MappingProxyType(parsed["strategy"]),
            source=path,
        )

    def get(self, section: str, key: str) -> Decimal:
        table = self.manual if section == "manual" else self.strategy
        if key not in table:
            raise KeyError(f"no such rule: {section}.{key} (in {self.source})")
        return table[key]

    # --- typed accessors for the manual rules the engine executes ----------
    @property
    def settlement_reserve_usd(self) -> Decimal: return self.get("manual", "settlement_reserve_usd")
    @property
    def min_share_price_usd(self) -> Decimal: return self.get("manual", "min_share_price_usd")
    @property
    def single_position_pct(self) -> Decimal: return self.get("manual", "single_position_pct")
    @property
    def option_single_position_pct(self) -> Decimal: return self.get("manual", "option_single_position_pct")
    @property
    def option_open_premium_pct(self) -> Decimal: return self.get("manual", "option_open_premium_pct")
    @property
    def option_min_dte(self) -> int: return int(self.get("manual", "option_min_dte"))
    @property
    def option_close_at_dte(self) -> int: return int(self.get("manual", "option_close_at_dte"))
    @property
    def stop_atr_multiple(self) -> Decimal: return self.get("manual", "stop_atr_multiple")
    @property
    def stop_trigger_min_pct(self) -> Decimal: return self.get("manual", "stop_trigger_min_pct")
    @property
    def stop_trigger_max_pct(self) -> Decimal: return self.get("manual", "stop_trigger_max_pct")
    @property
    def stop_limit_pct_below_trigger(self) -> Decimal: return self.get("manual", "stop_limit_pct_below_trigger")
    @property
    def leveraged_aggregate_pct(self) -> Decimal: return self.get("manual", "leveraged_aggregate_pct")
    @property
    def leveraged_max_hold_sessions(self) -> int: return int(self.get("manual", "leveraged_max_hold_sessions"))
    @property
    def halt_multiple_of_hwm(self) -> Decimal: return self.get("manual", "halt_multiple_of_hwm")
    @property
    def max_orders_per_symbol_per_session(self) -> int: return int(self.get("manual", "max_orders_per_symbol_per_session"))
    @property
    def max_replaces_per_stop_per_day(self) -> int: return int(self.get("manual", "max_replaces_per_stop_per_day"))
```

- [ ] **Step 5: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_rules_model.py && mypy && ruff check . ../tests/engine`
Expected: `5 passed`; clean. (If ruff flags the one-line properties under `E701`, add `E701` to the ignore list in `pyproject.toml` — the compact form is deliberate for a 17-accessor table.)

- [ ] **Step 6: Commit**

```bash
git add engine/tc/money.py engine/tc/rules/__init__.py engine/tc/rules/model.py tests/engine/unit/test_rules_model.py engine/pyproject.toml
git commit -m "engine: Rules loader over rules.yml and Decimal money helpers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 3: `arith.py` — pure rule arithmetic, property-tested

**Files:**
- Create: `engine/tc/rules/arith.py`
- Test: `tests/engine/unit/test_arith.py`, `tests/engine/property/test_arith_props.py`

**Interfaces:**
- Consumes: `Rules` (Task 2), `floor_cents` (Task 2).
- Produces (all pure, all `Decimal` in/out):
  - `cap_dollars(pct: Decimal, account_value: Decimal) -> Decimal` — `floor_cents(account_value * pct / 100)`
  - `StopGeometry(trigger: Decimal, limit: Decimal, trigger_pct: Decimal)`; `stop_geometry(entry: Decimal, daily_atr_pct: Decimal, rules: Rules) -> StopGeometry` — `CLAUDE.md §3.4`
  - `halt_threshold(hwm: Decimal, rules: Rules) -> Decimal`
  - `drawdown_pct(account_value: Decimal, hwm: Decimal) -> Decimal` — percent, 2 dp
  - `is_halted(account_value: Decimal, hwm: Decimal, rules: Rules) -> bool` — `account_value <= halt`
  - `ratchet_hwm(prior: Decimal, close: Decimal) -> Decimal` — `max`
  - `legacy_hwm_to_account_basis(recorded: Decimal, recorded_on: date, reserve: Decimal) -> Decimal` — adds the reserve iff `recorded_on < date(2026, 8, 31)`
  - `reserve_cash(cash_balance, cash_available_for_trading, unsettled) -> Decimal` — `min(cash_balance, cafT + unsettled)` (tick.md §C watch 2)
  - `lifetime_pl(market_value, average_price, quantity) -> Decimal` (tick.md §B5)
  - `notional(quantity: int, price: Decimal, multiplier: int) -> Decimal`
  - `notional_matches(a, b, tolerance=Decimal("0.01")) -> bool`
  - `dte_floor_identity_holds(rules: Rules) -> bool` — `option_min_dte == close_at_dte + max_blind_days + execution_margin`

- [ ] **Step 1: Write the failing unit tests**

`tests/engine/unit/test_arith.py`:
```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from tc.rules import arith
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
R = Rules.load(REPO / "rules.yml")


def test_cap_floors_to_cent() -> None:
    # 35% of 3,781.06 = 1,323.371 -> floored, never rounded up
    assert arith.cap_dollars(Decimal("35"), Decimal("3781.06")) == Decimal("1323.37")


def test_stop_geometry_clamps_and_offsets() -> None:
    # ATR 2% -> 2.5x = 5% -> below the 8% floor -> trigger 8% below entry, limit 5% below trigger
    g = arith.stop_geometry(Decimal("100.00"), Decimal("2"), R)
    assert g.trigger_pct == Decimal("8")
    assert g.trigger == Decimal("92.00")
    assert g.limit == Decimal("87.40")
    # ATR 8% -> 20% -> capped at 15%
    g = arith.stop_geometry(Decimal("100.00"), Decimal("8"), R)
    assert g.trigger_pct == Decimal("15")
    assert g.trigger == Decimal("85.00")
    # ATR 4% -> 10%, inside the band
    g = arith.stop_geometry(Decimal("50.00"), Decimal("4"), R)
    assert g.trigger == Decimal("45.00") and g.limit == Decimal("42.75")


def test_halt_and_drawdown_use_account_value() -> None:
    hwm = Decimal("3800.00")
    assert arith.halt_threshold(hwm, R) == Decimal("3040.00")
    assert arith.drawdown_pct(Decimal("3758.00"), hwm) == Decimal("-1.11")
    assert arith.is_halted(Decimal("3040.00"), hwm, R) is True
    assert arith.is_halted(Decimal("3040.01"), hwm, R) is False


def test_legacy_hwm_conversion_is_dated() -> None:
    # every High-water mark written before 2026-08-31 is competition capital ($900 low)
    assert arith.legacy_hwm_to_account_basis(
        Decimal("2900.00"), date(2026, 8, 26), Decimal("900.00")
    ) == Decimal("3800.00")
    assert arith.legacy_hwm_to_account_basis(
        Decimal("3800.00"), date(2026, 8, 31), Decimal("900.00")
    ) == Decimal("3800.00")


def test_ratchet_never_lowers() -> None:
    assert arith.ratchet_hwm(Decimal("2900"), Decimal("2881.06")) == Decimal("2900")
    assert arith.ratchet_hwm(Decimal("2900"), Decimal("2950")) == Decimal("2950")


def test_reserve_and_pl() -> None:
    assert arith.reserve_cash(Decimal("2393.57"), Decimal("2393.57"), Decimal("0")) == Decimal("2393.57")
    assert arith.reserve_cash(Decimal("500"), Decimal("300"), Decimal("100")) == Decimal("400")
    assert arith.lifetime_pl(Decimal("990.64"), Decimal("34.79"), 29) == Decimal("-18.27")


def test_notional_tolerance() -> None:
    assert arith.notional(4, Decimal("50.375"), 1) == Decimal("201.50")
    assert arith.notional(2, Decimal("1.23"), 100) == Decimal("246.00")
    assert arith.notional_matches(Decimal("201.50"), Decimal("201.51"))
    assert not arith.notional_matches(Decimal("201.50"), Decimal("201.52"))


def test_dte_identity_holds_for_real_rules() -> None:
    assert arith.dte_floor_identity_holds(R)
```

- [ ] **Step 2: Write the failing property tests**

`tests/engine/property/test_arith_props.py`:
```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from tc.rules import arith
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
R = Rules.load(REPO / "rules.yml")

money = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10000000"), places=2, allow_nan=False)
pct = st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=2, allow_nan=False)
atr = st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=3, allow_nan=False)


@given(pct, money)
def test_cap_never_exceeds_exact(p: Decimal, av: Decimal) -> None:
    assert arith.cap_dollars(p, av) <= av * p / 100
    assert arith.cap_dollars(p, av) >= av * p / 100 - Decimal("0.01")


@given(money, atr)
@settings(max_examples=300)
def test_stop_trigger_pct_inside_band_and_monotone(entry: Decimal, a: Decimal) -> None:
    g = arith.stop_geometry(entry, a, R)
    assert R.stop_trigger_min_pct <= g.trigger_pct <= R.stop_trigger_max_pct
    assert g.limit < g.trigger < entry
    g2 = arith.stop_geometry(entry, a + Decimal("0.5"), R)
    assert g2.trigger_pct >= g.trigger_pct  # more volatile -> wider or equal


@given(money, money)
def test_hwm_ratchet_monotone(prior: Decimal, close: Decimal) -> None:
    new = arith.ratchet_hwm(prior, close)
    assert new >= prior and new >= close


@given(money, st.dates(min_value=date(2026, 8, 1), max_value=date(2026, 12, 31)))
def test_basis_conversion_idempotent(hwm: Decimal, d: Decimal) -> None:
    once = arith.legacy_hwm_to_account_basis(hwm, d, Decimal("900.00"))
    # applying the conversion to an already-converted (post-amendment) mark changes nothing
    assert arith.legacy_hwm_to_account_basis(once, date(2026, 8, 31), Decimal("900.00")) == once


@given(money, money)
def test_halt_is_exactly_at_or_below_threshold(av: Decimal, hwm: Decimal) -> None:
    assert arith.is_halted(av, hwm, R) == (av <= arith.halt_threshold(hwm, R))
```

- [ ] **Step 3: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_arith.py ../tests/engine/property`
Expected: FAIL, `cannot import name 'arith'`.

- [ ] **Step 4: Implement `engine/tc/rules/arith.py`**

```python
"""Pure arithmetic for every cap and threshold the engine enforces.

Ported from scripts/pre-order-check.sh (caps, notional, price floor),
.claude/commands/tick.md §B5/§C (drawdown, reserve, lifetime P/L) and
.claude/agents/session-close.md §3 (ratchet, basis conversion). No I/O, no
floats, no rule numbers: every parameter comes in through `Rules`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tc.money import cents, floor_cents
from tc.rules.model import Rules

# The §3.6 re-anchor date: every High-water mark recorded before it is a
# competition-capital figure (account value − reserve) and must be CONVERTED,
# never merely compared (CLAUDE.md §3.6 migration note).
BASIS_AMENDMENT_DATE = date(2026, 8, 31)

HUNDRED = Decimal("100")


def cap_dollars(pct: Decimal, account_value: Decimal) -> Decimal:
    return floor_cents(account_value * pct / HUNDRED)


@dataclass(frozen=True)
class StopGeometry:
    trigger: Decimal
    limit: Decimal
    trigger_pct: Decimal  # percent below entry, after the clamp


def stop_geometry(entry: Decimal, daily_atr_pct: Decimal, rules: Rules) -> StopGeometry:
    """CLAUDE.md §3.4: trigger_pct = clamp(multiple × ATR%, min, max); limit sits
    limit_pct below the trigger."""
    raw = rules.stop_atr_multiple * daily_atr_pct
    trigger_pct = min(max(raw, rules.stop_trigger_min_pct), rules.stop_trigger_max_pct)
    trigger = cents(entry * (HUNDRED - trigger_pct) / HUNDRED)
    limit = cents(trigger * (HUNDRED - rules.stop_limit_pct_below_trigger) / HUNDRED)
    return StopGeometry(trigger=trigger, limit=limit, trigger_pct=trigger_pct)


def halt_threshold(hwm: Decimal, rules: Rules) -> Decimal:
    return cents(hwm * rules.halt_multiple_of_hwm)


def drawdown_pct(account_value: Decimal, hwm: Decimal) -> Decimal:
    if hwm <= 0:
        raise ValueError("hwm must be positive")
    return cents((account_value - hwm) / hwm * HUNDRED)


def is_halted(account_value: Decimal, hwm: Decimal, rules: Rules) -> bool:
    return account_value <= halt_threshold(hwm, rules)


def ratchet_hwm(prior: Decimal, close: Decimal) -> Decimal:
    return max(prior, close)


def legacy_hwm_to_account_basis(recorded: Decimal, recorded_on: date, reserve: Decimal) -> Decimal:
    return recorded + reserve if recorded_on < BASIS_AMENDMENT_DATE else recorded


def reserve_cash(
    cash_balance: Decimal, cash_available_for_trading: Decimal, unsettled: Decimal
) -> Decimal:
    return min(cash_balance, cash_available_for_trading + unsettled)


def lifetime_pl(market_value: Decimal, average_price: Decimal, quantity: int) -> Decimal:
    return cents(market_value - average_price * abs(quantity))


def notional(quantity: int, price: Decimal, multiplier: int) -> Decimal:
    return cents(Decimal(quantity) * price * multiplier)


def notional_matches(a: Decimal, b: Decimal, tolerance: Decimal = Decimal("0.01")) -> bool:
    return abs(a - b) <= tolerance


def dte_floor_identity_holds(rules: Rules) -> bool:
    expected = (
        rules.option_close_at_dte
        + int(rules.get("manual", "option_max_blind_days"))
        + int(rules.get("manual", "option_dte_execution_margin_days"))
    )
    return rules.option_min_dte == expected
```

- [ ] **Step 5: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_arith.py ../tests/engine/property && mypy && ruff check . ../tests/engine`
Expected: all pass. If `test_stop_trigger_pct_inside_band_and_monotone` fails on `g.limit < g.trigger < entry` for tiny entries (e.g. `0.01`), raise `money`'s `min_value` in the property file to `Decimal("5.00")` — the `CLAUDE.md §1.4` floor makes sub-$5 entries impossible anyway, and say so in a comment.

- [ ] **Step 6: Commit**

```bash
git add engine/tc/rules/arith.py tests/engine/unit/test_arith.py tests/engine/property/test_arith_props.py
git commit -m "engine: rule arithmetic with hypothesis properties

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 4: `consistency.py` — port of `check-consistency.sh`, reaching `.claude/` and `engine/`

**Files:**
- Create: `engine/tc/rules/consistency.py`
- Test: `tests/engine/unit/test_consistency.py`, `tests/engine/fixtures/consistency/` (small mutated repo copies built by the test itself)

**Interfaces:**
- Consumes: `Rules` (Task 2), `dte_floor_identity_holds` (Task 3).
- Produces: `Finding(check: str, path: str | None, line: int | None, message: str)`; `Report(findings: list[Finding], checked: dict[str, int])` with `.ok -> bool`; `run_checks(repo_root: Path, rules: Rules | None = None) -> Report`.

The checks, each a function `check_<name>(root, rules) -> tuple[list[Finding], int]` (findings, count-checked), ported 1:1 from `scripts/check-consistency.sh` and extended per spec §9:

| # | Check | Ported from | Extension |
|---|---|---|---|
| 1 | annotations: every `**N**<!--rule:key-->` in `**/*.md` matches `rules.yml` | sh §1 | scans `.claude/**` and `engine/**` too; skips `docs/archive/`, `.venv/`, `node_modules/` |
| 2 | tightness pairs (`strategy` vs `manual`, ge/le) | sh §2 | same four pairs |
| 3 | derived: DTE identity; delta band ordered; strategy delta inside band | sh §2b | uses `arith.dte_floor_identity_holds` |
| 4 | dead keys: none of the seven §8 calendar keys in `rules.yml` | sh §2b | same list |
| 5 | hard-coded rule percentages in `scripts/*.sh` **and** `engine/**/*.py` (`* 35 / 100` style, non-comment lines) | sh §3 | Python too |
| 6 | `--jesus-take-the-wheel` outside a comment anywhere in `docker/ scripts/ .claude/ engine/` | sh §4 | `engine/` too |
| 7 | cross-basis: `comp_capital` compared against `halt`/`hwm` in `.claude/**/*.md` | sh §N | unchanged |
| 8 | endgame dates: `(lockout|flat by|END_DATE)` with a September date in `.claude/**`, `scripts/scheduled-run.sh` | sh §N+1 | unchanged |

Checks 5–8 of the shell script (schedule-vs-docs, sidecar gitignore, compose flags, deploy.sh in crontab) are **not** ported: they describe the old runtime and are retired with it in Phase 3. The bash checker keeps running beside this one until then (spec §11 Phase 0).

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_consistency.py`:
```python
import shutil
from pathlib import Path

from tc.rules.consistency import run_checks

REPO = Path(__file__).resolve().parents[3]
# Built by concatenation so this test file (and the plan that quotes it) never
# contains a literal annotation for the checkers to parse.
MARK = "<!--" + "rule:manual_single_position_pct-->"


def _mini_repo(tmp_path: Path) -> Path:
    """A minimal copy: rules.yml, CLAUDE.md, strategy.md, .claude/, scripts/ (one file)."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(REPO / "rules.yml", root / "rules.yml")
    shutil.copy(REPO / "CLAUDE.md", root / "CLAUDE.md")
    shutil.copy(REPO / "strategy.md", root / "strategy.md")
    shutil.copytree(REPO / ".claude", root / ".claude")
    (root / "scripts").mkdir()
    (root / "scripts" / "x.sh").write_text("#!/bin/bash\n# * 35 / 100 in a comment is fine\necho ok\n")
    (root / "engine" / "tc").mkdir(parents=True)
    (root / "engine" / "tc" / "y.py").write_text("x = 1\n")
    return root


def test_real_repo_is_consistent() -> None:
    rep = run_checks(REPO)
    assert rep.ok, [f"{f.check}: {f.path}:{f.line} {f.message}" for f in rep.findings]
    assert rep.checked["annotations"] > 20


def test_annotation_mismatch_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    p = root / "CLAUDE.md"
    p.write_text(p.read_text().replace("**35%**" + MARK, "**40%**" + MARK))
    rep = run_checks(root)
    assert not rep.ok
    assert any(f.check == "annotations" and "manual_single_position_pct" in f.message for f in rep.findings)


def test_annotation_in_dot_claude_is_seen(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / ".claude" / "commands" / "z.md").write_text("cap is **99%**" + MARK + "\n")
    rep = run_checks(root)
    assert any(f.path and f.path.endswith(".claude/commands/z.md") for f in rep.findings)


def test_hardcoded_pct_in_python_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / "engine" / "tc" / "y.py").write_text("cap = av * 35 / 100\n")
    rep = run_checks(root)
    assert any(f.check == "hardcoded" and f.path and f.path.endswith("y.py") for f in rep.findings)


def test_ungated_flag_in_engine_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / "engine" / "tc" / "y.py").write_text('flag = "--jesus-take-the-wheel"\n')
    rep = run_checks(root)
    assert any(f.check == "ungated_broker" for f in rep.findings)


def test_dte_identity_break_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    p = root / "rules.yml"
    p.write_text(p.read_text().replace("option_min_dte: 18", "option_min_dte: 17"))
    rep = run_checks(root)
    assert any(f.check == "derived" and "option_min_dte" in f.message for f in rep.findings)


def test_dead_key_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    p = root / "rules.yml"
    p.write_text(p.read_text() + "\n  window_end: 2026-09-14\n")
    rep = run_checks(root)
    assert any(f.check == "dead_keys" for f in rep.findings)


def test_cross_basis_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / ".claude" / "commands" / "bad.md").write_text("halt if comp_capital <= halt\n")
    rep = run_checks(root)
    assert any(f.check == "cross_basis" for f in rep.findings)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_consistency.py`
Expected: FAIL, `No module named 'tc.rules.consistency'`.

- [ ] **Step 3: Implement `engine/tc/rules/consistency.py`**

```python
"""Verify that no document or script contradicts rules.yml.

Python port of scripts/check-consistency.sh, extended to the two places the
shell version could not reach and which produced real defects: .claude/ (the
files that execute the rules; the 2026-08-31 cross-basis halt shipped through a
CONSISTENT report) and engine/ (this code). Exit semantics match the script:
ok == no findings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from tc.rules import arith
from tc.rules.model import Rules

SKIP_DIRS = {".git", ".venv", "node_modules", "archive", ".playwright-mcp", "__pycache__"}

ANNOTATION = re.compile(r"(?P<num>[0-9][0-9.]*)%?\*\*<!--rule:(?P<key>[a-zA-Z0-9_]+)-->")
HARDCODE = re.compile(r"\*\s*(35|30|20|15|10|50)\s*/\s*100")
UNGATED = "--jesus-take-the-wheel"
CROSS_BASIS = re.compile(
    r"comp_capital[^|]*(<=|≤|>=|≥)[^|]*(halt|hwm)|(halt|hwm)[^|]*(<=|≤|>=|≥)[^|]*comp_capital"
)
ENDGAME = re.compile(r"(lockout|flat by|END_DATE)[^|]*(9/|2026-09)")
ENDGAME_OK = re.compile(r"removed|deleted|no longer|was ", re.IGNORECASE)
DEAD_KEYS = (
    "window_start", "window_end", "final_session", "lockout_start",
    "lockout_final_sessions", "all_options_flat_by", "last_leveraged_entry",
)
TIGHTNESS = (  # strategy_key, manual_key, direction, label
    ("option_min_delta", "option_min_delta", "ge", "delta-floor"),
    ("leveraged_exit_session", "leveraged_max_hold_sessions", "le", "leveraged-hold"),
    ("sleeve_options_open_pct", "option_open_premium_pct", "le", "options-open"),
    ("sleeve_leveraged_pct", "leveraged_aggregate_pct", "le", "leveraged-aggregate"),
)


@dataclass(frozen=True)
class Finding:
    check: str
    path: str | None
    line: int | None
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.findings


def _walk(root: Path, suffixes: tuple[str, ...], under: Iterable[str] | None = None) -> Iterable[Path]:
    bases = [root / u for u in under] if under else [root]
    for base in bases:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_dir() or p.suffix not in suffixes:
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
                continue
            yield p


def _rel(root: Path, p: Path) -> str:
    return str(p.relative_to(root))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def check_annotations(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    table = {f"manual_{k}": v for k, v in rules.manual.items()}
    table |= {f"strategy_{k}": v for k, v in rules.strategy.items()}
    for p in _walk(root, (".md",)):
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            for m in ANNOTATION.finditer(line):
                n += 1
                key, stated = m["key"], Decimal(m["num"])
                if key not in table:
                    out.append(Finding("annotations", _rel(root, p), i, f"unknown rule '{key}'"))
                elif stated != table[key]:
                    out.append(Finding("annotations", _rel(root, p), i,
                                       f"states {stated} but rules.yml has {key} = {table[key]}"))
    return out, n


def check_tightness(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    for skey, mkey, d, label in TIGHTNESS:
        s, m = rules.get("strategy", skey), rules.get("manual", mkey)
        ok = s >= m if d == "ge" else s <= m
        if not ok:
            out.append(Finding("tightness", "rules.yml", None, f"{label}: strategy {s} is LOOSER than manual {m}"))
    return out, len(TIGHTNESS)


def check_derived(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    if not arith.dte_floor_identity_holds(rules):
        out.append(Finding("derived", "rules.yml", None,
                           "option_min_dte != close_at_dte + max_blind_days + execution_margin"))
    mn, mx = rules.get("manual", "option_min_delta"), rules.get("manual", "option_max_delta")
    sd = rules.get("strategy", "option_min_delta")
    if not mn < mx:
        out.append(Finding("derived", "rules.yml", None, f"option delta band inverted: {mn} >= {mx}"))
    if not mn <= sd <= mx:
        out.append(Finding("derived", "rules.yml", None, f"strategy option_min_delta {sd} outside band {mn}-{mx}"))
    return out, 3


def check_dead_keys(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    text = (root / "rules.yml").read_text()
    for k in DEAD_KEYS:
        if re.search(rf"^\s+{k}:", text, re.MULTILINE):
            out.append(Finding("dead_keys", "rules.yml", None,
                               f"carries '{k}' — §8 and the endgame calendar were deleted 2026-08-31"))
    return out, len(DEAD_KEYS)


def check_hardcoded(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    files = list(_walk(root, (".sh",), ["scripts"])) + list(_walk(root, (".py",), ["engine"]))
    for p in files:
        if p.name in {"lib-rules.sh", "check-consistency.sh", "consistency.py"}:
            continue
        n += 1
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if not _is_comment(line) and HARDCODE.search(line):
                out.append(Finding("hardcoded", _rel(root, p), i, "hard-codes a rule percentage — read it from rules.yml"))
    return out, n


def check_ungated(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    for p in _walk(root, (".sh", ".py", ".yml", ".yaml", ".md", ".json"), ["docker", "scripts", ".claude", "engine"]):
        if p.name in {"check-consistency.sh", "consistency.py"}:
            continue
        n += 1
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if UNGATED in line and not _is_comment(line):
                out.append(Finding("ungated_broker", _rel(root, p), i, "bypasses the Discord approval gate"))
    return out, n


def check_cross_basis(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    for p in _walk(root, (".md",), [".claude"]):
        n += 1
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if CROSS_BASIS.search(line):
                out.append(Finding("cross_basis", _rel(root, p), i, "compares comp_capital against halt/HWM — false halt"))
    return out, n


def check_endgame(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    files = list(_walk(root, (".md",), [".claude"]))
    sr = root / "scripts" / "scheduled-run.sh"
    if sr.exists():
        files.append(sr)
    for p in files:
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if ENDGAME.search(line) and not ENDGAME_OK.search(line):
                out.append(Finding("endgame", _rel(root, p), i, "live endgame date from the deleted §8"))
    return out, len(files)


CHECKS: tuple[tuple[str, Callable[[Path, Rules], tuple[list[Finding], int]]], ...] = (
    ("annotations", check_annotations),
    ("tightness", check_tightness),
    ("derived", check_derived),
    ("dead_keys", check_dead_keys),
    ("hardcoded", check_hardcoded),
    ("ungated_broker", check_ungated),
    ("cross_basis", check_cross_basis),
    ("endgame", check_endgame),
)


def run_checks(repo_root: Path, rules: Rules | None = None) -> Report:
    rules = rules or Rules.load(repo_root / "rules.yml")
    rep = Report()
    for name, fn in CHECKS:
        findings, n = fn(repo_root, rules)
        rep.findings.extend(findings)
        rep.checked[name] = n
    return rep
```

- [ ] **Step 4: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_consistency.py && mypy && ruff check . ../tests/engine`
Expected: `8 passed`. If `test_real_repo_is_consistent` fails, the finding is real — a doc drifted from `rules.yml`. Fix the doc (never the checker) in a separate commit, then rerun. Note that the real repo's `docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` contains the literal string `--jesus-take-the-wheel` in no place, but `docker/docker-compose.yml` mentions it in comments only; the comment filter handles that.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/rules/consistency.py tests/engine/unit/test_consistency.py
git commit -m "engine: consistency checker reaching .claude/ and engine/

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 5: Broker models — the typed shapes every loop reads

**Files:**
- Create: `engine/tc/broker/__init__.py`, `engine/tc/broker/models.py`
- Test: `tests/engine/unit/test_broker_models.py`

**Interfaces:**
- Produces (pydantic, `extra="ignore"` on the raw-payload parsers because Schwab adds fields):
  - `Position(symbol, asset_type: Literal["EQUITY","OPTION","COLLECTIVE_INVESTMENT","ETF"]|str, quantity: int, average_price: Decimal, market_value: Decimal, day_pl: Decimal, settled_quantity: int)` with `.lifetime_pl -> Decimal`
  - `AccountSnapshot(account_hash: str, read_at: datetime, liquidation_value, cash_available_for_trading, unsettled_cash, cash_balance, cash_call, is_closing_only_restricted: bool, positions: list[Position])` with `.reserve_cash -> Decimal`
  - `OrderLeg(instruction: str, quantity: int, symbol: str, asset_type: str)`
  - `OrderRow(order_id: int, status: str, order_type: str, duration: str, entered_at: datetime, quantity: int, filled_quantity: int, price: Decimal|None, stop_price: Decimal|None, legs: list[OrderLeg])` with `.is_resting_stop -> bool` (status WORKING/QUEUED/ACCEPTED/PENDING_ACTIVATION and type STOP/STOP_LIMIT) and `.symbol -> str` (first leg)
  - `Quote(symbol: str, last: Decimal, bid: Decimal, ask: Decimal, quote_time: datetime, description: str)`
  - `MarketWindow(date: date, is_trading_day: bool, rth_start: datetime|None, rth_end: datetime|None)`
  - `DailyBar(date: date, open, high, low, close: Decimal)`
  - classmethods `AccountSnapshot.from_payload(account_hash, payload: dict, read_at)`, `OrderRow.from_payload(dict)`, `Quote.from_payload(symbol, dict)`, `MarketWindow.from_payload(date, dict)`, `DailyBar.from_payload(dict)`

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_broker_models.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal

from tc.broker.models import AccountSnapshot, MarketWindow, OrderRow, Quote

ACCOUNT = {
    "securitiesAccount": {
        "type": "CASH",
        "isClosingOnlyRestricted": False,
        "positions": [
            {
                "instrument": {"symbol": "AMH", "assetType": "EQUITY"},
                "longQuantity": 29.0, "shortQuantity": 0.0, "settledLongQuantity": 29.0,
                "averagePrice": 34.79, "marketValue": 990.64, "currentDayProfitLoss": -11.6,
            }
        ],
        "currentBalances": {
            "liquidationValue": 3781.06, "cashAvailableForTrading": 2393.57,
            "unsettledCash": 0.0, "cashBalance": 2393.57, "cashCall": 0.0,
        },
        "initialBalances": {"liquidationValue": 999999.0},
    }
}

ORDER = {
    "orderId": 1000000000001, "status": "WORKING", "orderType": "STOP_LIMIT", "duration": "GOOD_TILL_CANCEL",
    "enteredTime": "2026-08-24T14:32:11+0000", "quantity": 29.0, "filledQuantity": 0.0,
    "price": 30.40, "stopPrice": 32.01,
    "orderLegCollection": [{"instruction": "SELL", "quantity": 29.0,
                            "instrument": {"symbol": "AMH", "assetType": "EQUITY"}}],
}

QUOTE = {"AMH": {"quote": {"lastPrice": 34.16, "bidPrice": 34.15, "askPrice": 34.17,
                           "quoteTime": 1756838400000},
                 "reference": {"description": "AMERICAN HOMES 4 RENT"}}}

HOURS_OPEN = {"equity": {"EQ": {"date": "2026-09-02", "isOpen": True, "sessionHours": {
    "regularMarket": [{"start": "2026-09-02T09:30:00-04:00", "end": "2026-09-02T16:00:00-04:00"}]}}}}
HOURS_CLOSED = {"equity": {"equity": {"date": "2026-09-07", "isOpen": False}}}


def test_account_reads_current_balances_only() -> None:
    now = datetime(2026, 9, 2, 16, 5, tzinfo=UTC)
    a = AccountSnapshot.from_payload("HASH", ACCOUNT, now)
    assert a.liquidation_value == Decimal("3781.06")          # not 999999 from initialBalances
    assert a.reserve_cash == Decimal("2393.57")
    assert a.positions[0].quantity == 29
    assert a.positions[0].lifetime_pl == Decimal("-18.27")
    assert a.positions[0].day_pl == Decimal("-11.60")
    assert a.is_closing_only_restricted is False


def test_order_row_resting_stop() -> None:
    o = OrderRow.from_payload(ORDER)
    assert o.symbol == "AMH" and o.is_resting_stop
    assert o.stop_price == Decimal("32.01") and o.price == Decimal("30.40")
    assert o.entered_at.tzinfo is not None


def test_quote_time_is_datetime() -> None:
    q = Quote.from_payload("AMH", QUOTE["AMH"])
    assert q.last == Decimal("34.16")
    assert q.quote_time == datetime.fromtimestamp(1756838400, tz=UTC)
    assert "AMERICAN HOMES" in q.description


def test_market_window_open_and_closed_shapes() -> None:
    w = MarketWindow.from_payload(date(2026, 9, 2), HOURS_OPEN)
    assert w.is_trading_day and w.rth_start is not None and w.rth_start.hour == 9
    c = MarketWindow.from_payload(date(2026, 9, 7), HOURS_CLOSED)
    assert not c.is_trading_day and c.rth_start is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_broker_models.py`
Expected: FAIL, `No module named 'tc.broker'`.

- [ ] **Step 3: Implement `engine/tc/broker/models.py`** (plus empty `engine/tc/broker/__init__.py`)

```python
"""Typed views over Schwab Trader API payloads.

Field traps carried over from tick.md §B2/§B5 and the schwab-mcp-notes skill:
read currentBalances never initialBalances; currentDayProfitLoss is the DAY
move (schwab-mcp exposed it as unrealizedPL), lifetime P/L is computed; the
market-hours payload nests under "EQ" on a trading day and "equity" on a
closed one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from tc.money import D, cents

RESTING = {"WORKING", "QUEUED", "ACCEPTED", "PENDING_ACTIVATION", "AWAITING_STOP_CONDITION"}
STOP_TYPES = {"STOP", "STOP_LIMIT"}


def _dec(v: Any, default: str = "0") -> Decimal:
    return D(default) if v is None else cents(D(str(v)))


def _int(v: Any) -> int:
    return int(D(str(v or 0)))


def _dt(s: str) -> datetime:
    # Schwab emits "2026-08-24T14:32:11+0000"; fromisoformat needs "+00:00"
    if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return datetime.fromisoformat(s)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    asset_type: str
    quantity: int
    average_price: Decimal
    market_value: Decimal
    day_pl: Decimal
    settled_quantity: int

    @property
    def lifetime_pl(self) -> Decimal:
        return cents(self.market_value - self.average_price * abs(self.quantity))


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_hash: str
    read_at: datetime
    liquidation_value: Decimal
    cash_available_for_trading: Decimal
    unsettled_cash: Decimal
    cash_balance: Decimal
    cash_call: Decimal
    is_closing_only_restricted: bool
    positions: list[Position]

    @property
    def reserve_cash(self) -> Decimal:
        return min(self.cash_balance, self.cash_available_for_trading + self.unsettled_cash)

    @classmethod
    def from_payload(cls, account_hash: str, payload: dict[str, Any], read_at: datetime) -> AccountSnapshot:
        acct = payload["securitiesAccount"]
        bal = acct["currentBalances"]
        positions = [
            Position(
                symbol=p["instrument"]["symbol"],
                asset_type=p["instrument"].get("assetType", ""),
                quantity=_int(p.get("longQuantity")) - _int(p.get("shortQuantity")),
                average_price=D(str(p.get("averagePrice", 0))),
                market_value=_dec(p.get("marketValue")),
                day_pl=_dec(p.get("currentDayProfitLoss")),
                settled_quantity=_int(p.get("settledLongQuantity")),
            )
            for p in acct.get("positions", [])
        ]
        return cls(
            account_hash=account_hash,
            read_at=read_at,
            liquidation_value=_dec(bal.get("liquidationValue")),
            cash_available_for_trading=_dec(bal.get("cashAvailableForTrading")),
            unsettled_cash=_dec(bal.get("unsettledCash")),
            cash_balance=_dec(bal.get("cashBalance")),
            cash_call=_dec(bal.get("cashCall")),
            is_closing_only_restricted=bool(acct.get("isClosingOnlyRestricted", False)),
            positions=positions,
        )


class OrderLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str
    quantity: int
    symbol: str
    asset_type: str


class OrderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int
    status: str
    order_type: str
    duration: str
    entered_at: datetime
    quantity: int
    filled_quantity: int
    price: Decimal | None
    stop_price: Decimal | None
    legs: list[OrderLeg]

    @property
    def symbol(self) -> str:
        return self.legs[0].symbol if self.legs else ""

    @property
    def is_resting_stop(self) -> bool:
        return self.status in RESTING and self.order_type in STOP_TYPES

    @classmethod
    def from_payload(cls, o: dict[str, Any]) -> OrderRow:
        return cls(
            order_id=int(o["orderId"]),
            status=str(o.get("status", "")),
            order_type=str(o.get("orderType", "")),
            duration=str(o.get("duration", "")),
            entered_at=_dt(o["enteredTime"]),
            quantity=_int(o.get("quantity")),
            filled_quantity=_int(o.get("filledQuantity")),
            price=None if o.get("price") is None else _dec(o["price"]),
            stop_price=None if o.get("stopPrice") is None else _dec(o["stopPrice"]),
            legs=[
                OrderLeg(
                    instruction=str(leg.get("instruction", "")),
                    quantity=_int(leg.get("quantity")),
                    symbol=leg["instrument"]["symbol"],
                    asset_type=leg["instrument"].get("assetType", ""),
                )
                for leg in o.get("orderLegCollection", [])
            ],
        )


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    quote_time: datetime
    description: str

    @classmethod
    def from_payload(cls, symbol: str, q: dict[str, Any]) -> Quote:
        quote = q.get("quote", {})
        return cls(
            symbol=symbol,
            last=_dec(quote.get("lastPrice")),
            bid=_dec(quote.get("bidPrice")),
            ask=_dec(quote.get("askPrice")),
            quote_time=datetime.fromtimestamp(int(quote.get("quoteTime", 0)) / 1000, tz=UTC),
            description=str(q.get("reference", {}).get("description", "")),
        )


class MarketWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    is_trading_day: bool
    rth_start: datetime | None
    rth_end: datetime | None

    @classmethod
    def from_payload(cls, d: date, payload: dict[str, Any]) -> MarketWindow:
        equity = payload.get("equity") or {}
        body = next(iter(equity.values()), {}) if equity else {}
        rth = (body.get("sessionHours") or {}).get("regularMarket") or []
        if body.get("isOpen") and rth:
            return cls(date=d, is_trading_day=True,
                       rth_start=_dt(rth[0]["start"]), rth_end=_dt(rth[0]["end"]))
        return cls(date=d, is_trading_day=False, rth_start=None, rth_end=None)


class DailyBar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def from_payload(cls, c: dict[str, Any]) -> DailyBar:
        d = datetime.fromtimestamp(int(c["datetime"]) / 1000, tz=UTC).date()
        return cls(date=d, open=_dec(c["open"]), high=_dec(c["high"]), low=_dec(c["low"]), close=_dec(c["close"]))
```

- [ ] **Step 4: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_broker_models.py && mypy && ruff check . ../tests/engine`
Expected: `4 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/broker/__init__.py engine/tc/broker/models.py tests/engine/unit/test_broker_models.py
git commit -m "engine: typed broker payload models

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 6: `TokenStore` — file, age, state, auth URL, and the callback exchange

**Files:**
- Create: `engine/tc/broker/token.py`
- Test: `tests/engine/unit/test_token.py`

**Interfaces:**
- Consumes: `TokenConfig` (Task 1).
- Produces:
  - `TokenState = Literal["absent", "fresh", "reauth_due", "dead"]`
  - `TokenStore(path: Path, cfg: TokenConfig, app_key: str, app_secret: str, clock: Callable[[], float] = time.time)`
  - `.read() -> dict | None` — the schwab-py wrapped token (`{"creation_timestamp": int, "token": {...}}`) or `None`
  - `.write(wrapped: dict) -> None` — atomic (`tmp` + `os.replace`), mode 0600
  - `.age_days() -> float | None`, `.days_until_dead() -> float | None`, `.state() -> TokenState`
  - `.begin_auth() -> str` — builds `schwab.auth.get_auth_context`, persists `{callback_url, state}` next to the token as `auth-context.json`, returns the authorization URL
  - `.complete_auth(received_url: str) -> None` — reconstructs the `AuthContext`, calls `schwab.auth.client_from_received_url(..., token_write_func=self._unwrapped_write, asyncio=False)`; the client is discarded — the engine builds its own via `client_from_access_functions` on the next `read()`
  - `.read_func()` / `.write_func()` — the `token_read_func` / `token_write_func` pair for `client_from_access_functions`

`days_until_dead` and `state` are the numbers `/health` and Discord will print; the wording "healthy" never appears (spec §7).

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_token.py`:
```python
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import AnyHttpUrl

from tc.broker import token as tok
from tc.config import TokenConfig

CFG = TokenConfig(reauth_after_days=5, hard_expiry_days=7,
                  callback_url=AnyHttpUrl("https://pi.example.ts.net/oauth/callback"))
DAY = 86400.0


def _store(tmp_path: Path, now: float) -> tok.TokenStore:
    return tok.TokenStore(tmp_path / "token.json", CFG, "key", "secret", clock=lambda: now)


def _wrapped(created: float) -> dict[str, Any]:
    return {"creation_timestamp": int(created), "token": {"access_token": "a", "refresh_token": "r"}}


def test_absent_state(tmp_path: Path) -> None:
    s = _store(tmp_path, 1_000_000.0)
    assert s.read() is None and s.state() == "absent" and s.days_until_dead() is None


def test_states_by_age(tmp_path: Path) -> None:
    now = 1_000_000.0
    s = _store(tmp_path, now)
    s.write(_wrapped(now - 1 * DAY))
    assert s.state() == "fresh" and round(s.days_until_dead() or 0, 2) == 6.0
    s.write(_wrapped(now - 5 * DAY))
    assert s.state() == "reauth_due"
    s.write(_wrapped(now - 7 * DAY))
    assert s.state() == "dead" and (s.days_until_dead() or 1) <= 0


def test_write_is_atomic_and_private(tmp_path: Path) -> None:
    s = _store(tmp_path, 1.0)
    s.write(_wrapped(1.0))
    p = tmp_path / "token.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(p.read_text())["creation_timestamp"] == 1


def test_corrupt_file_reads_as_absent_and_is_quarantined(tmp_path: Path) -> None:
    p = tmp_path / "token.json"
    p.write_text("{not json")
    s = _store(tmp_path, 1.0)
    assert s.read() is None and s.state() == "absent"
    assert (tmp_path / "token.json.corrupt").exists()


def test_begin_auth_persists_context_and_returns_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Ctx:
        callback_url = str(CFG.callback_url)
        authorization_url = "https://api.schwabapi.com/v1/oauth/authorize?x=1&state=S"
        state = "S"

    monkeypatch.setattr(tok.schwab_auth, "get_auth_context", lambda api_key, callback_url: Ctx())
    s = _store(tmp_path, 1.0)
    url = s.begin_auth()
    assert url.startswith("https://api.schwabapi.com")
    ctx = json.loads((tmp_path / "auth-context.json").read_text())
    assert ctx == {"callback_url": str(CFG.callback_url), "state": "S"}


def test_complete_auth_writes_token_via_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "auth-context.json").write_text(json.dumps({"callback_url": "https://x/cb", "state": "S"}))
    seen: dict[str, Any] = {}

    def fake_client_from_received_url(api_key: str, app_secret: str, auth_context: Any, received_url: str,
                                      token_write_func: Any, asyncio: bool = False, **kw: Any) -> object:
        seen["state"] = auth_context.state
        seen["url"] = received_url
        token_write_func({"creation_timestamp": 42, "token": {"access_token": "new"}})
        return object()

    monkeypatch.setattr(tok.schwab_auth, "client_from_received_url", fake_client_from_received_url)
    s = _store(tmp_path, 1.0)
    s.complete_auth("https://x/cb?code=abc&state=S")
    assert seen == {"state": "S", "url": "https://x/cb?code=abc&state=S"}
    assert (s.read() or {})["creation_timestamp"] == 42
    assert not (tmp_path / "auth-context.json").exists()


def test_complete_auth_without_context_raises(tmp_path: Path) -> None:
    s = _store(tmp_path, 1.0)
    with pytest.raises(tok.NoAuthInProgress):
        s.complete_auth("https://x/cb?code=abc")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_token.py`
Expected: FAIL, `No module named 'tc.broker.token'`.

- [ ] **Step 3: Implement `engine/tc/broker/token.py`**

```python
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


class NoAuthInProgress(Exception):
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
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or "creation_timestamp" not in data or "token" not in data:
                raise ValueError("token file missing schwab-py metadata")
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
        self._ctx_path.write_text(json.dumps({"callback_url": ctx.callback_url, "state": ctx.state}))
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
```

`schwab.auth.AuthContext` is the namedtuple `(callback_url, authorization_url, state)` in schwab-py 1.5.1; `client_from_received_url` reads only `.callback_url` and `.state` from it (verified in the installed source), so the empty `authorization_url` is fine.

- [ ] **Step 4: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_token.py && mypy && ruff check . ../tests/engine`
Expected: `7 passed`, clean. mypy may need `# type: ignore[attr-defined]` on the two `schwab_auth.` calls because the package has no stubs and `ignore_missing_imports` only silences the import; add it if reported.

- [ ] **Step 5: Commit**

```bash
git add engine/tc/broker/token.py tests/engine/unit/test_token.py
git commit -m "engine: TokenStore with atomic writes, age states, and phone re-auth exchange

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 7: `Broker` protocol, `SchwabBroker`, `FakeBroker`, and the redacting recorder

**Files:**
- Create: `engine/tc/broker/client.py`, `engine/tc/broker/fake.py`
- Test: `tests/engine/unit/test_fake_broker.py`, `tests/engine/contract/test_fixtures_redacted.py`, fixtures under `tests/engine/fixtures/broker/` (synthetic, created in Step 1)

**Interfaces:**
- Consumes: models (Task 5), `TokenStore` (Task 6).
- Produces:
  - `class Broker(Protocol)` with **read-only** async methods: `account_hashes() -> list[str]`, `account(hash) -> AccountSnapshot`, `orders(hash, from_dt, to_dt) -> list[OrderRow]`, `quotes(symbols: Sequence[str]) -> dict[str, Quote]`, `market_window(d: date) -> MarketWindow`, `daily_bars(symbol, days: int) -> list[DailyBar]`, `now() -> datetime` (broker time is not needed; `now` exists so the fake can be frozen — real returns `datetime.now(UTC)`). **No write method exists on this protocol in Phase 0a**; Phase 2 adds a separate `OrderBroker`.
  - `BrokerError(Exception)`, `BrokerUnauthorized(BrokerError)` (401/invalid_grant → the token is dead).
  - `SchwabBroker(store: TokenStore, app_key, app_secret)` with `async def open() -> None` (builds `schwab.auth.client_from_access_functions(..., asyncio=True)`), `async def close()`.
  - `FakeBroker(fixture_dir: Path, frozen_now: datetime)` loading `account.json`, `orders.json`, `quotes.json`, `hours-<date>.json`, `bars-<symbol>.json`; raises `BrokerUnauthorized` if a file `unauthorized` exists in the dir.
  - `Recorder(broker: SchwabBroker, out_dir: Path)` with `async def record(symbols: Sequence[str], d: date) -> None` — calls each read, **redacts** before writing: `accountNumber` → `"REDACTED"`, every hash value → `"HASH_REDACTED"`, and the recorded `account_hashes()` list → `["HASH_REDACTED"]`.

- [ ] **Step 1: Create synthetic fixtures**

`tests/engine/fixtures/broker/account.json` — the `ACCOUNT` dict from Task 5's test, as JSON. `orders.json` — a list containing Task 5's `ORDER`. `quotes.json` — Task 5's `QUOTE`. `hours-2026-09-02.json` — `HOURS_OPEN`; `hours-2026-09-07.json` — `HOURS_CLOSED`. `bars-AMH.json`:
```json
{"candles": [
  {"datetime": 1756425600000, "open": 34.0, "high": 34.6, "low": 33.7, "close": 34.3},
  {"datetime": 1756512000000, "open": 34.3, "high": 34.9, "low": 34.0, "close": 34.8},
  {"datetime": 1756771200000, "open": 34.8, "high": 35.1, "low": 34.1, "close": 34.2}
]}
```

- [ ] **Step 2: Write the failing tests**

`tests/engine/unit/test_fake_broker.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc.broker.client import BrokerUnauthorized
from tc.broker.fake import FakeBroker

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "broker"
NOW = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


async def test_reads_all_fixtures() -> None:
    b = FakeBroker(FIX, NOW)
    hashes = await b.account_hashes()
    assert hashes == ["HASH_REDACTED"]
    a = await b.account(hashes[0])
    assert a.liquidation_value == Decimal("3781.06") and a.read_at == NOW
    orders = await b.orders(hashes[0], NOW, NOW)
    assert orders[0].is_resting_stop
    q = await b.quotes(["AMH"])
    assert q["AMH"].last == Decimal("34.16")
    w = await b.market_window(date(2026, 9, 2))
    assert w.is_trading_day
    bars = await b.daily_bars("AMH", 3)
    assert [x.close for x in bars][-1] == Decimal("34.20")
    assert b.now() == NOW


async def test_unauthorized_marker(tmp_path: Path) -> None:
    (tmp_path / "unauthorized").touch()
    b = FakeBroker(tmp_path, NOW)
    with pytest.raises(BrokerUnauthorized):
        await b.account_hashes()
```

`tests/engine/contract/test_fixtures_redacted.py`:
```python
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
```

- [ ] **Step 3: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_fake_broker.py ../tests/engine/contract`
Expected: the fake test FAILS with import error; the contract test PASSES (fixtures are clean).

- [ ] **Step 4: Implement `engine/tc/broker/client.py`**

```python
"""Read-only broker access. No write method exists in this module by design
(spec §5: the order path is a separate, later component)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx
from schwab import auth as schwab_auth
from schwab.client import AsyncClient

from tc.broker.models import AccountSnapshot, DailyBar, MarketWindow, OrderRow, Quote
from tc.broker.token import TokenStore


class BrokerError(Exception):
    pass


class BrokerUnauthorized(BrokerError):
    """401 / invalid_grant: the token is dead. Only a human re-auth fixes this."""


class Broker(Protocol):
    async def account_hashes(self) -> list[str]: ...
    async def account(self, account_hash: str) -> AccountSnapshot: ...
    async def orders(self, account_hash: str, from_dt: datetime, to_dt: datetime) -> list[OrderRow]: ...
    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...
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
        try:
            self._client = schwab_auth.client_from_access_functions(
                self._app_key, self._app_secret,
                token_read_func=self._store.read_func(),
                token_write_func=self._store.write_func(),
                asyncio=True,
            )
        except FileNotFoundError as e:
            raise BrokerUnauthorized(str(e)) from e
        self._client.set_timeout(30)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.session.aclose()
            self._client = None

    def _c(self) -> AsyncClient:
        if self._client is None:
            raise BrokerError("broker not opened")
        return self._client

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def account_hashes(self) -> list[str]:
        data = _raise_for(await self._c().get_account_numbers())
        assert isinstance(data, list)
        return [str(x["hashValue"]) for x in data]

    async def account(self, account_hash: str) -> AccountSnapshot:
        c = self._c()
        data = _raise_for(await c.get_account(account_hash, fields=[c.Account.Fields.POSITIONS]))
        assert isinstance(data, dict)
        return AccountSnapshot.from_payload(account_hash, data, self.now())

    async def orders(self, account_hash: str, from_dt: datetime, to_dt: datetime) -> list[OrderRow]:
        data = _raise_for(await self._c().get_orders_for_account(
            account_hash, from_entered_datetime=from_dt, to_entered_datetime=to_dt))
        assert isinstance(data, list)
        return [OrderRow.from_payload(o) for o in data]

    async def quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        data = _raise_for(await self._c().get_quotes(list(symbols)))
        assert isinstance(data, dict)
        return {s: Quote.from_payload(s, q) for s, q in data.items() if "quote" in q}

    async def market_window(self, d: date) -> MarketWindow:
        c = self._c()
        data = _raise_for(await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d))
        assert isinstance(data, dict)
        return MarketWindow.from_payload(d, data)

    async def daily_bars(self, symbol: str, days: int) -> list[DailyBar]:
        end = self.now()
        start = end - timedelta(days=days * 2 + 7)  # weekends/holidays; trimmed below
        data = _raise_for(await self._c().get_price_history_every_day(
            symbol, start_datetime=start, end_datetime=end))
        assert isinstance(data, dict)
        bars = [DailyBar.from_payload(c) for c in data.get("candles", [])]
        return bars[-days:]
```

- [ ] **Step 5: Implement `engine/tc/broker/fake.py`**

```python
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

    async def orders(self, account_hash: str, from_dt: datetime, to_dt: datetime) -> list[OrderRow]:
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
        c = self.broker._c()  # noqa: SLF001 — recorder is a test-support tool
        h = (await self.broker.account_hashes())[0]
        raw = {
            "account.json": (await c.get_account(h, fields=[c.Account.Fields.POSITIONS])).json(),
            "orders.json": (await c.get_orders_for_account(h)).json(),
            "quotes.json": (await c.get_quotes(list(symbols))).json(),
            f"hours-{d.isoformat()}.json": (await c.get_market_hours([c.MarketHours.Market.EQUITY], date=d)).json(),
        }
        for s in symbols:
            raw[f"bars-{s}.json"] = (await c.get_price_history_every_day(s)).json()
        for name, payload in raw.items():
            (self.out / name).write_text(json.dumps(redact(payload), indent=1))
```

- [ ] **Step 6: Run tests, mypy, ruff**

Run: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`
Expected: all green. ruff `S101` (assert) is already ignored; if `ASYNC` rules flag the sync `json` reads in the fake, add `# noqa: ASYNC240` — fixture reads are tiny and deliberate.

- [ ] **Step 7: Commit**

```bash
git add engine/tc/broker/client.py engine/tc/broker/fake.py tests/engine/unit/test_fake_broker.py tests/engine/contract/test_fixtures_redacted.py tests/engine/fixtures/broker
git commit -m "engine: read-only Broker protocol, SchwabBroker, FakeBroker, redacting recorder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 8: `Store` — SQLite single writer, migrations, append-only triggers

**Files:**
- Create: `engine/tc/store/__init__.py`, `engine/tc/store/schema.sql`, `engine/tc/store/db.py`
- Test: `tests/engine/unit/test_store.py`

**Interfaces:**
- Consumes: models (Task 5).
- Produces: `Store(path: Path)` with `async open()`, `async close()`, `async execute(sql, params) -> None`, `async fetchall(sql, params) -> list[sqlite3.Row]`, `async fetchone(...)`, and typed helpers used by Phase 0b:
  - `record_account(snapshot: AccountSnapshot) -> int` (writes `account_snapshots` + `position_snapshots` in one transaction, returns snapshot id)
  - `record_orders(account_hash, rows: list[OrderRow], read_at) -> None`
  - `record_job_run(job, started_at, ended_at, verdict, detail: dict) -> None`; `Verdict = Literal["done","noop","content_failed","failed","timeout","missed"]`
  - `latest_session_status() -> SessionStatusRow | None`; `write_session_status(row) -> None` (`SessionStatusRow(date, close_value, hwm, halt, drawdown_pct, level, prior_hwm, ratcheted: bool, intraday_high: Decimal|None)`)
  - `append_tick(TickRow) -> None` (fields mirror `scripts/tick-append.sh`: `at_et, state, account_value, comp_capital, hwm, drawdown_pct, level, positions, stops, orders, settled, unsettled, reserve, flags, note`)
  - `record_token_event(kind, detail) -> None`; `open_alert(kind, message) -> int`; `ack_alert(id) -> None`; `open_alerts() -> list[AlertRow]`
  - `record_rules_version(sha256, path) -> None`
- A single `aiosqlite` connection guarded by an `asyncio.Lock`; `PRAGMA journal_mode=WAL; synchronous=NORMAL; foreign_keys=ON`. Migrations: `schema_version` table; `schema.sql` is idempotent (`CREATE TABLE IF NOT EXISTS`).
- Append-only enforced by SQL triggers (`RAISE(ABORT, ...)` on UPDATE/DELETE) on `ticks`, `job_runs`, `order_snapshots`, `token_events`, `trade_log` (created now, used in Phase 2).

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_store.py`:
```python
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tc.broker.models import AccountSnapshot, Position
from tc.store.db import SessionStatusRow, Store, TickRow


def _snap() -> AccountSnapshot:
    return AccountSnapshot(
        account_hash="H", read_at=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
        liquidation_value=Decimal("3781.06"), cash_available_for_trading=Decimal("2393.57"),
        unsettled_cash=Decimal("0"), cash_balance=Decimal("2393.57"), cash_call=Decimal("0"),
        is_closing_only_restricted=False,
        positions=[Position(symbol="AMH", asset_type="EQUITY", quantity=29, average_price=Decimal("34.79"),
                            market_value=Decimal("990.64"), day_pl=Decimal("-11.60"), settled_quantity=29)],
    )


async def test_opens_migrates_and_is_wal(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    row = await s.fetchone("PRAGMA journal_mode")
    assert row is not None and row[0] == "wal"
    v = await s.fetchone("SELECT MAX(version) FROM schema_version")
    assert v is not None and v[0] >= 1
    await s.close()


async def test_record_account_and_positions(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    sid = await s.record_account(_snap())
    rows = await s.fetchall("SELECT symbol, quantity, market_value FROM position_snapshots WHERE snapshot_id=?", (sid,))
    assert [tuple(r) for r in rows] == [("AMH", 29, "990.64")]
    acct = await s.fetchone("SELECT liquidation_value FROM account_snapshots WHERE id=?", (sid,))
    assert acct is not None and Decimal(acct[0]) == Decimal("3781.06")
    await s.close()


async def test_ticks_are_append_only(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    await s.append_tick(TickRow(at_et="2026-09-02 14:35", state="RTH", account_value=Decimal("3781.06"),
                                comp_capital=Decimal("2881.06"), hwm=Decimal("3800"), drawdown_pct=Decimal("-0.50"),
                                level="OK", positions=1, stops=1, orders=0, settled=Decimal("2393.57"),
                                unsettled=Decimal("0"), reserve=Decimal("2393.57"), flags="-", note=""))
    with pytest.raises(Exception, match="append-only"):
        await s.execute("UPDATE ticks SET level='HALT'")
    with pytest.raises(Exception, match="append-only"):
        await s.execute("DELETE FROM ticks")
    await s.close()


async def test_session_status_roundtrip_and_latest(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    assert await s.latest_session_status() is None
    r1 = SessionStatusRow(date=date(2026, 9, 1), close_value=Decimal("3758"), hwm=Decimal("3800"), halt=Decimal("3040"),
                          drawdown_pct=Decimal("-1.11"), level="OK", prior_hwm=Decimal("3800"), ratcheted=False,
                          intraday_high=None)
    r2 = r1.model_copy(update={"date": date(2026, 9, 2), "close_value": Decimal("3810"), "hwm": Decimal("3810"),
                               "ratcheted": True})
    await s.write_session_status(r1)
    await s.write_session_status(r2)
    latest = await s.latest_session_status()
    assert latest is not None and latest.date == date(2026, 9, 2) and latest.hwm == Decimal("3810")
    await s.close()


async def test_alerts_and_job_runs(tmp_path: Path) -> None:
    s = Store(tmp_path / "e.db")
    await s.open()
    aid = await s.open_alert("token", "days_until_dead=1.2")
    assert [a.id for a in await s.open_alerts()] == [aid]
    await s.ack_alert(aid)
    assert await s.open_alerts() == []
    t = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)
    await s.record_job_run("tick", t, t, "content_failed", {"reason": "no structured verdict"})
    row = await s.fetchone("SELECT verdict FROM job_runs")
    assert row is not None and row[0] == "content_failed"
    with pytest.raises(ValueError):
        await s.record_job_run("tick", t, t, "ok", {})  # 'ok' is not a verdict; the old deadman's mistake
    await s.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_store.py`
Expected: FAIL, `No module named 'tc.store'`.

- [ ] **Step 3: Write `engine/tc/store/schema.sql`**

```sql
-- v3 engine schema, Phase 0. Idempotent; applied by Store.open().
-- Money columns are TEXT holding Decimal strings: SQLite REAL would silently
-- turn 0.1+0.2 into a float, which is the rounding class the spec forbids.
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY,
  account_hash TEXT NOT NULL,
  read_at TEXT NOT NULL,
  liquidation_value TEXT NOT NULL,
  cash_available_for_trading TEXT NOT NULL,
  unsettled_cash TEXT NOT NULL,
  cash_balance TEXT NOT NULL,
  cash_call TEXT NOT NULL,
  is_closing_only_restricted INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS position_snapshots (
  snapshot_id INTEGER NOT NULL REFERENCES account_snapshots(id),
  symbol TEXT NOT NULL, asset_type TEXT NOT NULL, quantity INTEGER NOT NULL,
  average_price TEXT NOT NULL, market_value TEXT NOT NULL, day_pl TEXT NOT NULL,
  settled_quantity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_snapshots (
  id INTEGER PRIMARY KEY, account_hash TEXT NOT NULL, read_at TEXT NOT NULL,
  order_id INTEGER NOT NULL, status TEXT NOT NULL, order_type TEXT NOT NULL, duration TEXT NOT NULL,
  entered_at TEXT NOT NULL, symbol TEXT NOT NULL, quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL,
  price TEXT, stop_price TEXT, legs_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticks (
  id INTEGER PRIMARY KEY, at_et TEXT NOT NULL, state TEXT NOT NULL,
  account_value TEXT NOT NULL, comp_capital TEXT NOT NULL, hwm TEXT NOT NULL, drawdown_pct TEXT NOT NULL,
  level TEXT NOT NULL, positions INTEGER NOT NULL, stops INTEGER NOT NULL, orders INTEGER NOT NULL,
  settled TEXT NOT NULL, unsettled TEXT NOT NULL, reserve TEXT NOT NULL, flags TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_status (
  date TEXT PRIMARY KEY, close_value TEXT NOT NULL, hwm TEXT NOT NULL, halt TEXT NOT NULL,
  drawdown_pct TEXT NOT NULL, level TEXT NOT NULL, prior_hwm TEXT NOT NULL, ratcheted INTEGER NOT NULL,
  intraday_high TEXT, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('done','noop','content_failed','failed','timeout','missed')),
  detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, opened_at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, acked_at TEXT
);
CREATE TABLE IF NOT EXISTS rules_versions (sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expectations_log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_log (
  id INTEGER PRIMARY KEY, at_et TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL,
  quantity INTEGER NOT NULL, price TEXT NOT NULL, order_id INTEGER, pretrade_json TEXT NOT NULL, note TEXT NOT NULL
);

-- Append-only ledgers (CLAUDE.md §7.1: never edited once written; a correction is a new row).
CREATE TRIGGER IF NOT EXISTS ticks_no_update BEFORE UPDATE ON ticks BEGIN SELECT RAISE(ABORT, 'ticks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ticks_no_delete BEFORE DELETE ON ticks BEGIN SELECT RAISE(ABORT, 'ticks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_runs_no_update BEFORE UPDATE ON job_runs BEGIN SELECT RAISE(ABORT, 'job_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_runs_no_delete BEFORE DELETE ON job_runs BEGIN SELECT RAISE(ABORT, 'job_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_snapshots_no_update BEFORE UPDATE ON order_snapshots BEGIN SELECT RAISE(ABORT, 'order_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_snapshots_no_delete BEFORE DELETE ON order_snapshots BEGIN SELECT RAISE(ABORT, 'order_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS token_events_no_update BEFORE UPDATE ON token_events BEGIN SELECT RAISE(ABORT, 'token_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS token_events_no_delete BEFORE DELETE ON token_events BEGIN SELECT RAISE(ABORT, 'token_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_log_no_update BEFORE UPDATE ON trade_log BEGIN SELECT RAISE(ABORT, 'trade_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_log_no_delete BEFORE DELETE ON trade_log BEGIN SELECT RAISE(ABORT, 'trade_log is append-only'); END;
```

- [ ] **Step 4: Implement `engine/tc/store/db.py`** (plus empty `engine/tc/store/__init__.py`)

```python
"""SQLite store: one writer, WAL, Decimal-as-text, append-only ledgers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict

from tc.broker.models import AccountSnapshot, OrderRow

Verdict = Literal["done", "noop", "content_failed", "failed", "timeout", "missed"]
VERDICTS: frozenset[str] = frozenset({"done", "noop", "content_failed", "failed", "timeout", "missed"})
SCHEMA_VERSION = 1


class SessionStatusRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    close_value: Decimal
    hwm: Decimal
    halt: Decimal
    drawdown_pct: Decimal
    level: Literal["OK", "HALT"]
    prior_hwm: Decimal
    ratcheted: bool
    intraday_high: Decimal | None


class TickRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_et: str
    state: Literal["RTH", "PRE", "POST", "STALE", "BLIND"]
    account_value: Decimal
    comp_capital: Decimal
    hwm: Decimal
    drawdown_pct: Decimal
    level: Literal["OK", "HALT"]
    positions: int
    stops: int
    orders: int
    settled: Decimal
    unsettled: Decimal
    reserve: Decimal
    flags: str
    note: str


class AlertRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    opened_at: datetime
    kind: str
    message: str
    acked_at: datetime | None


def _s(d: Decimal | None) -> str | None:
    return None if d is None else str(d)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)  # autocommit; we BEGIN explicitly
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        schema = resources.files("tc.store").joinpath("schema.sql").read_text()
        await self._conn.executescript(schema)
        await self._conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, _now())
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _c(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("store not opened")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._lock:
            await self._c().execute(sql, params)

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        cur = await self._c().execute(sql, params)
        return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        cur = await self._c().execute(sql, params)
        row = await cur.fetchone()
        return row

    # --- typed helpers -----------------------------------------------------
    async def record_account(self, s: AccountSnapshot) -> int:
        async with self._lock:
            c = self._c()
            await c.execute("BEGIN")
            try:
                cur = await c.execute(
                    "INSERT INTO account_snapshots(account_hash, read_at, liquidation_value, cash_available_for_trading,"
                    " unsettled_cash, cash_balance, cash_call, is_closing_only_restricted) VALUES (?,?,?,?,?,?,?,?)",
                    (s.account_hash, s.read_at.isoformat(), _s(s.liquidation_value), _s(s.cash_available_for_trading),
                     _s(s.unsettled_cash), _s(s.cash_balance), _s(s.cash_call), int(s.is_closing_only_restricted)),
                )
                sid = int(cur.lastrowid or 0)
                await c.executemany(
                    "INSERT INTO position_snapshots(snapshot_id, symbol, asset_type, quantity, average_price,"
                    " market_value, day_pl, settled_quantity) VALUES (?,?,?,?,?,?,?,?)",
                    [(sid, p.symbol, p.asset_type, p.quantity, _s(p.average_price), _s(p.market_value),
                      _s(p.day_pl), p.settled_quantity) for p in s.positions],
                )
                await c.execute("COMMIT")
            except Exception:
                await c.execute("ROLLBACK")
                raise
            return sid

    async def record_orders(self, account_hash: str, rows: list[OrderRow], read_at: datetime) -> None:
        async with self._lock:
            await self._c().executemany(
                "INSERT INTO order_snapshots(account_hash, read_at, order_id, status, order_type, duration, entered_at,"
                " symbol, quantity, filled_quantity, price, stop_price, legs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(account_hash, read_at.isoformat(), o.order_id, o.status, o.order_type, o.duration,
                  o.entered_at.isoformat(), o.symbol, o.quantity, o.filled_quantity, _s(o.price), _s(o.stop_price),
                  json.dumps([leg.model_dump() for leg in o.legs])) for o in rows],
            )

    async def append_tick(self, t: TickRow) -> None:
        d = t.model_dump()
        cols = ",".join(d)
        await self.execute(
            f"INSERT INTO ticks({cols}) VALUES ({','.join('?' * len(d))})",  # noqa: S608 — column names from the model
            tuple(str(v) if isinstance(v, Decimal) else v for v in d.values()),
        )

    async def write_session_status(self, r: SessionStatusRow) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO session_status(date, close_value, hwm, halt, drawdown_pct, level, prior_hwm,"
            " ratcheted, intraday_high, written_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.date.isoformat(), _s(r.close_value), _s(r.hwm), _s(r.halt), _s(r.drawdown_pct), r.level,
             _s(r.prior_hwm), int(r.ratcheted), _s(r.intraday_high), _now()),
        )

    async def latest_session_status(self) -> SessionStatusRow | None:
        row = await self.fetchone("SELECT * FROM session_status ORDER BY date DESC LIMIT 1")
        if row is None:
            return None
        return SessionStatusRow(
            date=date.fromisoformat(row["date"]), close_value=Decimal(row["close_value"]), hwm=Decimal(row["hwm"]),
            halt=Decimal(row["halt"]), drawdown_pct=Decimal(row["drawdown_pct"]), level=row["level"],
            prior_hwm=Decimal(row["prior_hwm"]), ratcheted=bool(row["ratcheted"]),
            intraday_high=None if row["intraday_high"] is None else Decimal(row["intraday_high"]),
        )

    async def record_job_run(self, job: str, started: datetime, ended: datetime, verdict: str, detail: dict[str, Any]) -> None:
        if verdict not in VERDICTS:
            raise ValueError(f"not a verdict: {verdict!r} (one of {sorted(VERDICTS)})")
        await self.execute(
            "INSERT INTO job_runs(job, started_at, ended_at, verdict, detail_json) VALUES (?,?,?,?,?)",
            (job, started.isoformat(), ended.isoformat(), verdict, json.dumps(detail, default=str)),
        )

    async def record_token_event(self, kind: str, detail: str) -> None:
        await self.execute("INSERT INTO token_events(at, kind, detail) VALUES (?,?,?)", (_now(), kind, detail))

    async def open_alert(self, kind: str, message: str) -> int:
        async with self._lock:
            cur = await self._c().execute(
                "INSERT INTO alerts(opened_at, kind, message) VALUES (?,?,?)", (_now(), kind, message))
            return int(cur.lastrowid or 0)

    async def ack_alert(self, alert_id: int) -> None:
        await self.execute("UPDATE alerts SET acked_at=? WHERE id=? AND acked_at IS NULL", (_now(), alert_id))

    async def open_alerts(self) -> list[AlertRow]:
        rows = await self.fetchall("SELECT * FROM alerts WHERE acked_at IS NULL ORDER BY id")
        return [AlertRow(id=r["id"], opened_at=datetime.fromisoformat(r["opened_at"]), kind=r["kind"],
                         message=r["message"], acked_at=None) for r in rows]

    async def record_rules_version(self, sha256: str, path: str) -> None:
        await self.execute("INSERT OR IGNORE INTO rules_versions(sha256, path, seen_at) VALUES (?,?,?)",
                           (sha256, path, _now()))
```

- [ ] **Step 5: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_store.py && mypy && ruff check . ../tests/engine`
Expected: `5 passed`, clean. The trigger message contains "append-only", which the `pytest.raises(match=...)` relies on; do not reword it.

- [ ] **Step 6: Commit**

```bash
git add engine/tc/store/__init__.py engine/tc/store/schema.sql engine/tc/store/db.py tests/engine/unit/test_store.py
git commit -m "engine: SQLite Store with single writer and append-only ledgers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 9: `clock.py` — Eastern time, session phase, trading-day gate

**Files:**
- Create: `engine/tc/clock.py`
- Test: `tests/engine/unit/test_clock.py`

**Interfaces:**
- Consumes: `MarketWindow` (Task 5).
- Produces:
  - `ET = ZoneInfo("America/New_York")`
  - `SessionPhase = Literal["PRE", "RTH", "POST", "CLOSED"]` (`CLOSED` = not a trading day)
  - `phase_for(now: datetime, window: MarketWindow) -> SessionPhase` — `PRE` before `rth_start`, `RTH` in `[rth_start, rth_end)`, `POST` after; `CLOSED` when `not window.is_trading_day`
  - `et_now(clock: Callable[[], datetime]) -> datetime` — converts any aware datetime to ET
  - `et_stamp(dt: datetime) -> str` — `"YYYY-MM-DD HH:MM"` in ET, the ledger `at_et` format
  - `in_window(now_et: datetime, start: str, end: str) -> bool` — `"HH:MM"` strings, half-open `[start, end)`
  - `fallback_window(d: date) -> MarketWindow` — weekday → 09:30–16:00 ET trading day, weekend → closed; used only when the broker is blind (spec §3: "fail toward monitoring")

The machine clock is never read directly anywhere else; `clock` callables are injected so every loop test can freeze time.

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_clock.py`:
```python
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from tc.broker.models import MarketWindow
from tc.clock import ET, et_stamp, fallback_window, in_window, phase_for

D = date(2026, 9, 2)
W = MarketWindow(date=D, is_trading_day=True,
                 rth_start=datetime(2026, 9, 2, 9, 30, tzinfo=ET), rth_end=datetime(2026, 9, 2, 16, 0, tzinfo=ET))


def _et(h: int, m: int) -> datetime:
    return datetime(2026, 9, 2, h, m, tzinfo=ET)


def test_phases() -> None:
    assert phase_for(_et(9, 29), W) == "PRE"
    assert phase_for(_et(9, 30), W) == "RTH"
    assert phase_for(_et(15, 59), W) == "RTH"
    assert phase_for(_et(16, 0), W) == "POST"
    assert phase_for(_et(12, 0), MarketWindow(date=D, is_trading_day=False, rth_start=None, rth_end=None)) == "CLOSED"


def test_phase_accepts_utc_input() -> None:
    assert phase_for(datetime(2026, 9, 2, 13, 30, tzinfo=UTC), W) == "RTH"  # 09:30 EDT


def test_stamp_and_window() -> None:
    assert et_stamp(datetime(2026, 9, 2, 18, 35, tzinfo=UTC)) == "2026-09-02 14:35"
    assert in_window(_et(9, 32), "09:32", "15:47")
    assert not in_window(_et(15, 47), "09:32", "15:47")


def test_fallback_window() -> None:
    wk = fallback_window(date(2026, 9, 2))   # Wednesday
    assert wk.is_trading_day and wk.rth_start == datetime(2026, 9, 2, 9, 30, tzinfo=ET)
    assert not fallback_window(date(2026, 9, 5)).is_trading_day  # Saturday
    assert ZoneInfo("America/New_York") == ET
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_clock.py`
Expected: FAIL, `No module named 'tc.clock'`.

- [ ] **Step 3: Implement `engine/tc/clock.py`**

```python
"""Eastern-time helpers. Gate on the clock against the market-hours window,
never on get_market_hours' isOpen (tick.md §B1: it means 'trading day', and
reads true at 23:20 ET)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from tc.broker.models import MarketWindow

ET = ZoneInfo("America/New_York")
SessionPhase = Literal["PRE", "RTH", "POST", "CLOSED"]


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; the engine only handles aware times")
    return dt.astimezone(ET)


def et_now(clock: Callable[[], datetime]) -> datetime:
    return _aware(clock())


def et_stamp(dt: datetime) -> str:
    return _aware(dt).strftime("%Y-%m-%d %H:%M")


def phase_for(now: datetime, window: MarketWindow) -> SessionPhase:
    if not window.is_trading_day or window.rth_start is None or window.rth_end is None:
        return "CLOSED"
    n = _aware(now)
    if n < window.rth_start:
        return "PRE"
    if n < window.rth_end:
        return "RTH"
    return "POST"


def in_window(now_et: datetime, start: str, end: str) -> bool:
    n = _aware(now_et).time()
    s = time.fromisoformat(start)
    e = time.fromisoformat(end)
    return s <= n < e


def fallback_window(d: date) -> MarketWindow:
    """Only for a BLIND engine. Weekday => assume a trading day so monitoring
    keeps running; weekend => closed. Holidays are unknown here by design."""
    if d.weekday() >= 5:
        return MarketWindow(date=d, is_trading_day=False, rth_start=None, rth_end=None)
    return MarketWindow(
        date=d, is_trading_day=True,
        rth_start=datetime.combine(d, time(9, 30), tzinfo=ET),
        rth_end=datetime.combine(d, time(16, 0), tzinfo=ET),
    )
```

- [ ] **Step 4: Run tests, mypy, ruff**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_clock.py && mypy && ruff check . ../tests/engine`
Expected: `4 passed`, clean. (`tzdata` must be present on the Pi image; the Dockerfile in Phase 0b installs it — on macOS/Linux dev boxes the system zoneinfo suffices.)

- [ ] **Step 5: Commit**

```bash
git add engine/tc/clock.py tests/engine/unit/test_clock.py
git commit -m "engine: ET clock, session phase, blind fallback window

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
### Task 10: `tc` CLI — `check-consistency`, `token-status`, `auth-url`, `auth-complete`, `record-fixtures`

**Files:**
- Create: `engine/tc/cli.py`
- Test: `tests/engine/unit/test_cli.py`

**Interfaces:**
- Consumes: `load_settings` (Task 1), `Rules` (Task 2), `run_checks` (Task 4), `TokenStore` (Task 6), `SchwabBroker`, `Recorder` (Task 7).
- Produces: `main(argv: list[str] | None = None) -> int` (exit code), registered as the `tc` console script. Subcommands:
  - `tc check-consistency [--repo PATH]` → prints one line per finding, then `CONSISTENT` / `N inconsistency(ies)`; exit 0/1 (same contract as the bash script so Phase 0's side-by-side comparison is a diff of two outputs).
  - `tc token-status` → `state=<fresh|reauth_due|dead|absent> age_days=<x> days_until_dead=<y> action=<none|REAUTH NOW|...>`; exit 0 fresh, 2 reauth_due, 3 dead/absent. **Never prints the word "healthy".**
  - `tc auth-url` → prints the Schwab authorization URL (persists the auth context).
  - `tc auth-complete <received_url>` → completes the exchange; prints the new `token-status` line.
  - `tc record-fixtures --out DIR --symbols AMH,CSX` → records redacted fixtures via `Recorder`.
  - Global options: `--config config.yml` (default `./config.yml`), `--env .env` (default `./.env` if it exists).

- [ ] **Step 1: Write the failing tests**

`tests/engine/unit/test_cli.py`:
```python
from pathlib import Path

import pytest

from tc import cli

REPO = Path(__file__).resolve().parents[3]

CONFIG = """
engine: {timezone: America/New_York, data_dir: "%s", http_bind: 127.0.0.1:8080, reserve_usd: "900.00"}
token: {reauth_after_days: 5, hard_expiry_days: 7, callback_url: https://pi.example.ts.net/oauth/callback}
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\nTC_DISCORD_WEBHOOK_URL=https://d.example/h\n"


def _cfg(tmp_path: Path) -> list[str]:
    (tmp_path / "config.yml").write_text(CONFIG % tmp_path)
    (tmp_path / ".env").write_text(ENV)
    return ["--config", str(tmp_path / "config.yml"), "--env", str(tmp_path / ".env")]


def test_check_consistency_on_real_repo(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["check-consistency", "--repo", str(REPO)])
    out = capsys.readouterr().out
    assert rc == 0 and out.strip().endswith("CONSISTENT")


def test_token_status_absent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main([*_cfg(tmp_path), "token-status"])
    out = capsys.readouterr().out
    assert rc == 3 and "state=absent" in out and "healthy" not in out.lower()


def test_auth_url_prints_url(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tc.broker import token as tok

    class Ctx:
        callback_url = "https://pi.example.ts.net/oauth/callback"
        authorization_url = "https://api.schwabapi.com/v1/oauth/authorize?state=S"
        state = "S"

    monkeypatch.setattr(tok.schwab_auth, "get_auth_context", lambda api_key, callback_url: Ctx())
    rc = cli.main([*_cfg(tmp_path), "auth-url"])
    out = capsys.readouterr().out
    assert rc == 0 and out.strip() == Ctx.authorization_url
    assert (tmp_path / "auth-context.json").exists()


def test_unknown_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["bogus"])
    assert e.value.code == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `cd engine && pytest -q -c pyproject.toml ../tests/engine/unit/test_cli.py`
Expected: FAIL, `No module named 'tc.cli'`.

- [ ] **Step 3: Implement `engine/tc/cli.py`**

```python
"""`tc` — operator commands for the engine. Everything here is read-only at
the broker except auth-complete, which writes the token file."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from tc.broker.client import SchwabBroker
from tc.broker.fake import Recorder
from tc.broker.token import TokenStore
from tc.config import Settings, load_settings
from tc.rules.consistency import run_checks


def _settings(ns: argparse.Namespace) -> Settings:
    env = Path(ns.env) if ns.env else (Path(".env") if Path(".env").exists() else None)
    return load_settings(Path(ns.config), env)


def _token_store(s: Settings) -> TokenStore:
    return TokenStore(s.engine.data_dir / "token.json", s.token, s.schwab_app_key, s.schwab_app_secret)


def _token_line(store: TokenStore) -> tuple[str, int]:
    state = store.state()
    age = store.age_days()
    left = store.days_until_dead()
    action = {"fresh": "none", "reauth_due": "REAUTH NOW: tc auth-url, open on phone",
              "dead": "DEAD — account is blind until re-auth", "absent": "no token — run tc auth-url"}[state]
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
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
    print(f"{len(rep.findings)} inconsistency(ies). rules.yml is the source of truth; fix the other side.")
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
    await broker.open()
    try:
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
    c = sub.add_parser("check-consistency"); c.add_argument("--repo", default="."); c.set_defaults(fn=cmd_check_consistency)
    sub.add_parser("token-status").set_defaults(fn=cmd_token_status)
    sub.add_parser("auth-url").set_defaults(fn=cmd_auth_url)
    a = sub.add_parser("auth-complete"); a.add_argument("received_url"); a.set_defaults(fn=cmd_auth_complete)
    r = sub.add_parser("record-fixtures"); r.add_argument("--out", required=True); r.add_argument("--symbols", required=True)
    r.set_defaults(fn=cmd_record_fixtures)
    return p


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    rc: int = ns.fn(ns)
    return rc


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests, mypy, ruff, and the whole suite**

Run: `cd engine && pytest -q && mypy && ruff check . ../tests/engine`
Expected: everything green. If ruff flags `E702` on the semicolon lines in `build_parser`, split them onto separate lines.

- [ ] **Step 5: Compare the two consistency checkers on the real repo**

Run (repo root):
```bash
scripts/check-consistency.sh > /tmp/bash-cc.txt; echo "bash rc=$?"
(cd engine && tc check-consistency --repo ..) > /tmp/py-cc.txt; echo "py rc=$?"
```
Expected: both rc=0 and both end with `CONSISTENT`. Record the two outputs in the commit body — this is the Phase 0 "both must agree" evidence (spec §11).

- [ ] **Step 6: Commit**

```bash
git add engine/tc/cli.py tests/engine/unit/test_cli.py
git commit -m "engine: tc CLI — check-consistency, token-status, auth-url, auth-complete, record-fixtures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---
## Done criteria for this plan

- `cd engine && pytest -q && mypy && ruff check . ../tests/engine` green on macOS (arm64) and on the Pi (`python3.12` in a venv; the Docker image comes in Phase 0b).
- `tc check-consistency --repo ..` and `scripts/check-consistency.sh` both report `CONSISTENT` on `main`.
- `tc token-status` against an empty `data_dir` reports `state=absent` and exits 3.
- No file under `engine/` contains a rule number, an account identifier, or the ungated flag (the checker and the fixture contract test prove it).

## What Phase 0b consumes from here (interfaces frozen by this plan)

`Settings`, `Rules`, `arith.*`, `run_checks`, `TokenStore` (`state`, `days_until_dead`, `begin_auth`, `complete_auth`, `read_func`, `write_func`), `Broker` protocol + `SchwabBroker`/`FakeBroker`, models, `Store` helpers, `clock.*`. Phase 0b adds `loops/`, `scheduler.py`, `notify/` (Discord webhook, healthchecks), `http/app.py` (`/health`, `/oauth/callback` → `TokenStore.complete_auth`), `shadow/diff.py`, `main.py`, `docker/`, `host/`.

## Self-review (done at authoring time)

- **Spec coverage:** §3 topology → 0b; §5.2 gates arithmetic → Task 3 (caps, notional, price floor) with the order-specific gates deferred to Phase 2; §6 store → Task 8; §8 token/re-auth → Task 6 + Task 10; §9 config/consistency → Tasks 1, 4; §10 testing (fake, property, redaction contract) → Tasks 3, 7. Nothing in this plan writes to the broker, matching spec §11 Phase 0 ("no store writes, no orders").
- **Placeholder scan:** none. The `callback_url` in `config.yml` is a deliberate `REPLACE-ME` because the Tailscale hostname does not exist until Chris installs it (spec §11 prerequisite 2); Task 1's test uses a concrete value.
- **Type consistency:** `TokenStore.state()` returns the `TokenState` literal used by the CLI map in Task 10; `Store.record_job_run` accepts the `Verdict` strings that Task 8's CHECK constraint enforces; `AccountSnapshot.reserve_cash` and `arith.reserve_cash` compute the same expression (the model property exists so a snapshot can be judged without importing rules); `MarketWindow` is the shared type between Task 5, Task 7 and Task 9.
