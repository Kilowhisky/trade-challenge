"""Verify that no document or script contradicts rules.yml.

This is a PARTIAL port of scripts/check-consistency.sh, not a replacement.

Ported here, because they check the rules themselves: annotations (the
script's check 1), strategy-vs-manual tightness (2), the derived-DTE and
delta-band identities (2b), hard-coded rule percentages (3), the
ungated-broker flag (4), the capital-basis cross check (N) and surviving
endgame dates (N+1). Added on top: dead §8 keys in rules.yml, markers in
.claude/ (the files that EXECUTE the rules — the 2026-08-31 cross-basis halt
shipped through a CONSISTENT report), and the same greps over engine/ (this
code). Exit semantics match the script: ok == no findings.

Deliberately NOT ported: the script's checks 5-8 — schedule-vs-doc agreement,
the sidecar gitignore guard, the documented-command/compose-flag check and
the deploy.sh-in-crontab check. Every one of them describes the OLD runtime
(crontab + docker compose + the store symlinks), not the v3 engine. The bash
checker keeps running beside this one until Phase 3 retires that runtime, so
those checks are not lost in the meantime; the sidecar guard becomes moot
when spec §9's layout removes the store symlinks, and schedule-vs-doc becomes
a Phase 0b concern for config.yml, where the schedule is data rather than a
crontab line to grep.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from tc.rules import arith
from tc.rules.model import Rules

SKIP_DIRS = {
    ".git", ".venv", "node_modules", "archive", ".playwright-mcp", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis",
}
# *.egg-info varies by project name, so it needs a suffix test rather than a
# set membership one. Tool caches and build metadata are generated files: a
# rule percentage or a flag "found" inside one is noise, and reading them is
# pure cost.
SKIP_DIR_SUFFIXES = (".egg-info",)

ANNOTATION = re.compile(r"(?P<num>[0-9][0-9.]*)%?\*\*<!--rule:(?P<key>[a-zA-Z0-9_]+)-->")
HARDCODE = re.compile(r"\*\s*(35|30|20|15|10|50)\s*/\s*100")
UNGATED = "--jesus-take-the-wheel"
CROSS_BASIS = re.compile(
    r"comp_capital[^|]*(<=|≤|>=|≥)[^|]*(halt|hwm)|(halt|hwm)[^|]*(<=|≤|>=|≥)[^|]*comp_capital"
)
CROSS_BASIS_PY = re.compile(
    r"comp_capital\s*(<=|>=|<|>)\s*(halt|hwm)|(halt|hwm)\s*(<=|>=|<|>)\s*comp_capital"
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


def _walk(
    root: Path, suffixes: tuple[str, ...] | None, under: Iterable[str] | None = None
) -> Iterable[Path]:
    """Yield files under `under` (or `root`), skipping SKIP_DIRS.

    `suffixes=None` means every regular file, regardless of extension —
    needed by check_ungated, whose bash original greps with no --include
    filter at all (an extensionless Dockerfile or a dotfile like .env is as
    much a place the broker flag can hide as any .py or .md).
    """
    bases = [root / u for u in under] if under else [root]
    for base in bases:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_dir() or (suffixes is not None and p.suffix not in suffixes):
                continue
            parts = p.relative_to(root).parts
            if any(part in SKIP_DIRS for part in parts):
                continue
            if any(part.endswith(SKIP_DIR_SUFFIXES) for part in parts[:-1]):
                continue
            yield p


def _rel(root: Path, p: Path) -> str:
    return str(p.relative_to(root))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _lines(root: Path, p: Path, check: str, out: list[Finding]) -> list[str]:
    """Read a file for checking, or report it unreadable and carry on.

    An OSError on one file must not abort the whole report: a single
    permission-denied file would otherwise turn every other finding invisible,
    which is exactly the "degrade into no caps applied" failure this checker
    exists to prevent. The unreadable file becomes its own finding instead.
    """
    try:
        return p.read_text(errors="replace").splitlines()
    except OSError as e:
        out.append(Finding(check, _rel(root, p), None, f"unreadable: {e}"))
        return []


def check_annotations(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    table = {f"manual_{k}": v for k, v in rules.manual.items()}
    table |= {f"strategy_{k}": v for k, v in rules.strategy.items()}
    for p in _walk(root, (".md",)):
        for i, line in enumerate(_lines(root, p, "annotations", out), 1):
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
            out.append(Finding(
                "tightness", "rules.yml", None, f"{label}: strategy {s} is LOOSER than manual {m}"
            ))
    return out, len(TIGHTNESS)


def check_derived(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    if not arith.dte_floor_identity_holds(rules):
        out.append(Finding("derived", "rules.yml", None,
                           "option_min_dte != close_at_dte + max_blind_days + execution_margin"))
    mn, mx = rules.get("manual", "option_min_delta"), rules.get("manual", "option_max_delta")
    sd = rules.get("strategy", "option_min_delta")
    if not mn < mx:
        out.append(Finding(
            "derived", "rules.yml", None, f"option delta band inverted: {mn} >= {mx}"
        ))
    if not mn <= sd <= mx:
        out.append(Finding(
            "derived", "rules.yml", None,
            f"strategy option_min_delta {sd} outside band {mn}-{mx}",
        ))
    return out, 3


def check_dead_keys(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    rules_yml = root / "rules.yml"
    try:
        text = rules_yml.read_text()
    except OSError as e:
        return [Finding("dead_keys", "rules.yml", None, f"unreadable: {e}")], 0
    for k in DEAD_KEYS:
        if re.search(rf"^\s+{k}:", text, re.MULTILINE):
            out.append(Finding(
                "dead_keys", "rules.yml", None,
                f"carries '{k}' — §8 and the endgame calendar were deleted 2026-08-31",
            ))
    return out, len(DEAD_KEYS)


def check_hardcoded(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    files = list(_walk(root, (".sh",), ["scripts"])) + list(_walk(root, (".py",), ["engine"]))
    for p in files:
        if p.name in {"lib-rules.sh", "check-consistency.sh", "consistency.py"}:
            continue
        n += 1
        for i, line in enumerate(_lines(root, p, "hardcoded", out), 1):
            if not _is_comment(line) and HARDCODE.search(line):
                out.append(Finding(
                    "hardcoded", _rel(root, p), i,
                    "hard-codes a rule percentage — read it from rules.yml",
                ))
    return out, n


def check_ungated(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    # No suffix filter: the bash original greps docker/ scripts/ .claude/ with
    # no --include, so an extensionless Dockerfile or a dotfile is exactly as
    # exposed as a .py file — the flag is "one copy-pasted line away at all
    # times" regardless of what the file is named.
    for p in _walk(root, None, ["docker", "scripts", ".claude", "engine"]):
        if p.name in {"check-consistency.sh", "consistency.py"}:
            continue
        n += 1
        for i, line in enumerate(_lines(root, p, "ungated_broker", out), 1):
            if UNGATED in line and not _is_comment(line):
                out.append(Finding(
                    "ungated_broker", _rel(root, p), i, "bypasses the Discord approval gate"
                ))
    return out, n


def check_cross_basis(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    n = 0
    for p in _walk(root, (".md",), [".claude"]):
        n += 1
        for i, line in enumerate(_lines(root, p, "cross_basis", out), 1):
            if CROSS_BASIS.search(line):
                out.append(Finding(
                    "cross_basis", _rel(root, p), i,
                    "compares comp_capital against halt/HWM — false halt",
                ))
    # The markdown pattern is table-shaped ([^|] between the operands) and
    # never matches code. The same defect in Python is a plain comparison, so
    # it needs its own pattern -- the 2026-08-31 cross-basis halt was a
    # document defect, and nothing stops the next one being a line of engine.
    for p in _walk(root, (".py",), ["engine"]):
        if p.name == "consistency.py":
            continue
        n += 1
        for i, line in enumerate(_lines(root, p, "cross_basis", out), 1):
            if not _is_comment(line) and CROSS_BASIS_PY.search(line):
                out.append(Finding(
                    "cross_basis", _rel(root, p), i,
                    "compares comp_capital against halt/HWM — false halt",
                ))
    return out, n


def check_endgame(root: Path, rules: Rules) -> tuple[list[Finding], int]:
    out: list[Finding] = []
    files = list(_walk(root, (".md",), [".claude"]))
    sr = root / "scripts" / "scheduled-run.sh"
    if sr.exists():
        files.append(sr)
    for p in files:
        for i, line in enumerate(_lines(root, p, "endgame", out), 1):
            if ENDGAME.search(line) and not ENDGAME_OK.search(line):
                out.append(Finding(
                    "endgame", _rel(root, p), i, "live endgame date from the deleted §8"
                ))
    return out, len(files)


# Whether a check needs a Rules object actually loaded from rules.yml to do
# its job. annotations/tightness/derived read rule VALUES; the rest only grep
# raw text (rules.yml's own text, or docs/scripts) and never touch `rules`.
# That split matters when rules.yml itself is the thing under test: a
# dead-key mutation (§ dead_keys) can make rules.yml fail strict decimal
# parsing (e.g. an injected date-shaped value) while remaining perfectly
# readable as text — exactly the case check_dead_keys exists to catch. A
# load failure must not silently swallow that finding, so the checks that
# do not need parsed rules still run, and the load failure itself becomes a
# Finding rather than an uncaught exception (CLAUDE.md's "fail loudly, never
# degrade into no caps applied" applies to this checker's own inputs too).
CHECKS: tuple[tuple[str, Callable[[Path, Rules], tuple[list[Finding], int]], bool], ...] = (
    ("annotations", check_annotations, True),
    ("tightness", check_tightness, True),
    ("derived", check_derived, True),
    ("dead_keys", check_dead_keys, False),
    ("hardcoded", check_hardcoded, False),
    ("ungated_broker", check_ungated, False),
    ("cross_basis", check_cross_basis, False),
    ("endgame", check_endgame, False),
)


def run_checks(repo_root: Path, rules: Rules | None = None) -> Report:
    rep = Report()
    loaded = rules
    if loaded is None:
        try:
            loaded = Rules.load(repo_root / "rules.yml")
        except Exception as exc:
            rep.findings.append(
                Finding("rules_load", "rules.yml", None, f"cannot load rules.yml: {exc}")
            )
    placeholder = Rules(
        manual=MappingProxyType({}), strategy=MappingProxyType({}), source=repo_root / "rules.yml"
    )
    for name, fn, needs_rules in CHECKS:
        if needs_rules:
            if loaded is None:
                continue
            findings, n = fn(repo_root, loaded)
        else:
            findings, n = fn(repo_root, loaded if loaded is not None else placeholder)
        rep.findings.extend(findings)
        rep.checked[name] = n
    return rep
