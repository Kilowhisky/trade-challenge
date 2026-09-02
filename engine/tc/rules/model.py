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
        if section not in SECTIONS:
            raise KeyError(f"no such rules section: {section!r} (expected one of {SECTIONS})")
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
