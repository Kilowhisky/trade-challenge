import os
import shutil
from pathlib import Path

import pytest

from tc.rules.consistency import run_checks
from tc.rules.model import Rules

REPO = Path(__file__).resolve().parents[3]
# Built by concatenation so this test file (and the plan that quotes it) never
# contains a literal annotation for the checkers to parse.
MARK = "<!--" + "rule:manual_single_position_pct-->"


def _rules() -> Rules:
    return Rules.load(REPO / "rules.yml")


def _mini_repo(tmp_path: Path) -> Path:
    """A minimal copy: rules.yml, CLAUDE.md, strategy.md, .claude/, scripts/ (one file)."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(REPO / "rules.yml", root / "rules.yml")
    shutil.copy(REPO / "config.yml", root / "config.yml")
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


def test_ungated_flag_in_extensionless_file_is_found(tmp_path: Path) -> None:
    # Built by concatenation so this test file never carries the literal.
    flag = "--jesus-" + "take-the-wheel"
    root = _mini_repo(tmp_path)
    (root / "docker").mkdir()
    (root / "docker" / "Dockerfile").write_text(f"RUN schwab-mcp server {flag}\n")
    rep = run_checks(root)
    assert any(
        f.check == "ungated_broker" and f.path and f.path.endswith("docker/Dockerfile")
        for f in rep.findings
    )


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


def test_cross_basis_in_python_is_found(tmp_path: Path) -> None:
    """The 2026-08-31 cross-basis halt shipped once already. It can ship again
    in Python, where the markdown-shaped pattern never looked."""
    root = _mini_repo(tmp_path)
    (root / "engine" / "tc" / "y.py").write_text(
        'level = "HALT" if comp_capital <= halt else "OK"\n'
    )
    rep = run_checks(root)
    assert any(
        f.check == "cross_basis" and f.path and f.path.endswith("y.py") for f in rep.findings
    )


def test_cross_basis_in_python_ignores_comments(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / "engine" / "tc" / "y.py").write_text("# never write comp_capital <= halt\n")
    rep = run_checks(root)
    assert not any(f.check == "cross_basis" for f in rep.findings)


def test_missing_rules_yml_is_a_finding(tmp_path: Path) -> None:
    """A repo with no rules.yml must report, not raise: an unreadable input to
    the checker is the checker's loudest finding, never a crash."""
    root = tmp_path / "bare"
    (root / "engine" / "tc").mkdir(parents=True)
    (root / "engine" / "tc" / "y.py").write_text("x = 1\n")
    rep = run_checks(root)
    assert not rep.ok
    assert any(f.check in {"rules_load", "dead_keys"} for f in rep.findings)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file mode bits")
def test_unreadable_file_is_a_finding_not_a_crash(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    bad = root / "engine" / "tc" / "y.py"
    bad.write_text("x = 1\n")
    bad.chmod(0o000)
    try:
        rep = run_checks(root)
    finally:
        bad.chmod(0o644)
    assert any("unreadable" in f.message for f in rep.findings)


# --- the MCP tool registry (spec §9/§10) --------------------------------


def test_tool_registry_check_trips_on_an_order_shaped_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bash checker could not see this at all -- there was no registry to
    see. It lives in the checker, not only in the contract test, because a
    rule only a test enforces is a rule the build does not."""
    import tc.mcp.registry as reg
    from tc.rules.consistency import check_tool_registry

    monkeypatch.setitem(reg.ROLE_TOOLS, "research", ("quotes", "cancel_order"))
    findings, _ = check_tool_registry(REPO, _rules())
    assert [f.message for f in findings] == [
        "role 'research' exposes order-shaped tool 'cancel_order'"
    ]


def test_tool_registry_check_passes_as_shipped() -> None:
    from tc.rules.consistency import check_tool_registry

    findings, count = check_tool_registry(REPO, _rules())
    assert findings == []
    assert count > 0


def test_run_checks_includes_the_tool_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wired into CHECKS, not merely importable: an order-shaped tool must
    fail `tc check`, which is what the gate actually runs."""
    import tc.mcp.registry as reg

    rep = run_checks(REPO)
    assert "tool_registry" in rep.checked

    monkeypatch.setitem(reg.ROLE_TOOLS, "decide", ("place_order",))
    rep = run_checks(REPO)
    assert not rep.ok
    assert any(f.check == "tool_registry" for f in rep.findings)


# --- schedule vs doc --------------------------------------------------------

def test_schedule_check_passes_as_shipped() -> None:
    from tc.rules.consistency import check_schedule_vs_doc

    findings, count = check_schedule_vs_doc(REPO, _rules())
    assert findings == []
    assert count > 0


def test_a_schedule_key_naming_no_job_is_found(tmp_path: Path) -> None:
    """`Engine.start` rejects it too — but at start, on an unattended box,
    where "it did not come up" is discovered whenever someone next looks."""
    root = _mini_repo(tmp_path)
    cfg = root / "config.yml"
    cfg.write_text(cfg.read_text().replace("  scout:", "  scoot:", 1))
    rep = run_checks(root)
    assert not rep.ok
    assert any("'scoot'" in f.message for f in rep.findings if f.check == "schedule_vs_doc")


def test_a_job_spec_with_no_schedule_entry_is_found(tmp_path: Path) -> None:
    """A job that has a spec and nothing that fires it reads as a working
    feature and is not one."""
    root = _mini_repo(tmp_path)
    cfg = root / "config.yml"
    cfg.write_text("\n".join(
        line for line in cfg.read_text().splitlines() if not line.startswith("  catalyst:")
    ))
    rep = run_checks(root)
    assert any(
        "can never fire" in f.message for f in rep.findings if f.check == "schedule_vs_doc"
    )


def test_research_cadence_drifting_from_its_command_file_is_found(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    cfg = root / "config.yml"
    cfg.write_text(
        cfg.read_text().replace('"every 60m 09:57-14:57 weekdays"', '"every 60m 09:12-15:12 weekdays"')
    )
    rep = run_checks(root)
    assert not rep.ok
    messages = [f.message for f in rep.findings if f.check == "schedule_vs_doc"]
    assert any("hourly at :57" in m for m in messages)
    assert any("hours 9-14" in m for m in messages)


def test_a_command_file_that_stops_stating_the_cadence_is_found(tmp_path: Path) -> None:
    """The check reads the doc's own words rather than restating them, so the
    doc losing them is itself the finding — not a silently skipped check."""
    root = _mini_repo(tmp_path)
    doc = root / ".claude" / "commands" / "research.md"
    doc.write_text(doc.read_text().replace("hourly at :57", "whenever"))
    rep = run_checks(root)
    assert any(
        "no longer states the research cadence" in f.message
        for f in rep.findings
        if f.check == "schedule_vs_doc"
    )
