"""Task 2: the desk's settings block and rule keys."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

from desk_fixtures import REPO, RULES

from tc.config import Settings, load_settings
from tc.rules.consistency import run_checks

BASE = """
engine:
  data_dir: {p}
  repo_dir: {repo}
  research_dir: {p}/research
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n"


def _load(tmp_path: Path, extra: str = "") -> Settings:
    (tmp_path / "config.yml").write_text(BASE.format(p=tmp_path, repo=REPO) + extra)
    (tmp_path / ".env").write_text(ENV)
    return load_settings(tmp_path / "config.yml", tmp_path / ".env")


def test_desk_block_is_optional_with_safe_defaults(tmp_path: Path) -> None:
    s = _load(tmp_path)
    assert s.desk.etf_list == []
    assert s.desk.checkpoint_date == date(2026, 12, 31)
    assert (s.desk.entry_window_start, s.desk.entry_window_end) == (time(10, 0), time(15, 0))


def test_desk_symbols_are_normalised_to_upper_case(tmp_path: Path) -> None:
    s = _load(tmp_path, "desk:\n  etf_list: [spy, ' xlk ']\n  context_symbols: [uup]\n")
    assert s.desk.etf_list == ["SPY", "XLK"]
    assert s.desk.context_symbols == ["UUP"]


def test_repo_config_carries_the_spec_etf_list() -> None:
    import yaml

    cfg = yaml.safe_load((REPO / "config.yml").read_text())
    etfs = cfg["desk"]["etf_list"]
    for sym in ("SPY", "QQQ", "XLK", "XLE", "SMH", "GLD", "TLT", "USO"):
        assert sym in etfs


def test_desk_rule_keys_exist_with_spec_values() -> None:
    g = lambda k: RULES.get("strategy", k)  # noqa: E731
    assert g("desk_max_pitches_per_analyst_run") == Decimal("5")  # type: ignore[no-untyped-call]
    assert g("desk_preopen_max_new_pitches") == Decimal("2")  # type: ignore[no-untyped-call]
    assert g("desk_max_new_calls_per_day") == Decimal("5")  # type: ignore[no-untyped-call]
    assert (g("pitch_horizon_min_days"), g("pitch_horizon_max_days")) == (Decimal("2"), Decimal("20"))  # type: ignore[no-untyped-call]
    assert g("pitch_level_max_distance_pct") == Decimal("30")  # type: ignore[no-untyped-call]
    assert [g(f"size_shares_pct_conviction_{c}") for c in (3, 4, 5)] == [Decimal("10"), Decimal("15"), Decimal("20")]  # type: ignore[no-untyped-call]
    assert [g(f"size_option_premium_pct_conviction_{c}") for c in (3, 4, 5)] == [  # type: ignore[no-untyped-call]
        Decimal("5"), Decimal("7.5"), Decimal("10"),
    ]
    assert g("max_funded_positions") == Decimal("8")  # type: ignore[no-untyped-call]
    assert g("option_target_delta") == Decimal("0.60")  # type: ignore[no-untyped-call]
    assert g("option_min_delta") == Decimal("0.45")  # type: ignore[no-untyped-call]
    assert g("veto_window_minutes") == Decimal("10")  # type: ignore[no-untyped-call]


def test_the_repo_stays_consistent_with_the_new_keys() -> None:
    rep = run_checks(REPO)
    assert rep.ok, [f.message for f in rep.findings]
