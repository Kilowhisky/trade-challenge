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


def test_ungated_flag_in_extensionless_file_is_found(tmp_path: Path) -> None:
    # Built by concatenation so this test file never carries the literal.
    FLAG = "--jesus-" + "take-the-wheel"
    root = _mini_repo(tmp_path)
    (root / "docker").mkdir()
    (root / "docker" / "Dockerfile").write_text(f"RUN schwab-mcp server {FLAG}\n")
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
