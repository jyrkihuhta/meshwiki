"""Tests for scripts/validate_playbooks.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_playbooks.py"

_VALID_FM = (
    "---\n"
    "assignee: factory\n"
    "playbook: my-playbook\n"
    "name: My Playbook\n"
    "leaf_type: sqli\n"
    "scope: generic\n"
    "---\n\n"
    "# Body\n\n"
    "Prose goes here.\n"
)

_VALID_FM_WITH_CHECKS = (
    "---\n"
    "playbook: with-checks\n"
    "name: With Checks\n"
    "leaf_type: sqli\n"
    "scope: generic\n"
    "checks:\n"
    "  - id: chk-1\n"
    "    name: A check\n"
    "    mode: deterministic\n"
    "    category: sqli\n"
    "    severity: high\n"
    "---\n\n"
    "Body.\n"
)

_VALID_TARGET_SPECIFIC = (
    "---\n"
    "playbook: target-one\n"
    "name: Target Specific\n"
    "leaf_type: sqli\n"
    "scope: target-specific\n"
    "target: app.example.com\n"
    "---\n\n"
    "Body.\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_playbook_returns_zero(tmp_path: Path) -> None:
    p = _write(tmp_path, "good.md", _VALID_FM)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_invalid_yaml_returns_nonzero(tmp_path: Path) -> None:
    bad = (
        "---\n"
        "playbook: bad\n"
        "name: Bad\n"
        "leaf_type: : [unbalanced\n"
        "scope: generic\n"
        "---\n\n"
        "Body.\n"
    )
    p = _write(tmp_path, "bad.md", bad)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "invalid YAML" in proc.stderr


def test_missing_required_field(tmp_path: Path) -> None:
    # Missing `name` and `leaf_type`
    p = _write(
        tmp_path,
        "incomplete.md",
        "---\nplaybook: x\nscope: generic\n---\n\nBody.\n",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "`name`" in proc.stderr
    assert "`leaf_type`" in proc.stderr


def test_missing_frontmatter(tmp_path: Path) -> None:
    p = _write(tmp_path, "no-fm.md", "# Just a heading\n\nNo frontmatter here.\n")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "missing YAML frontmatter" in proc.stderr


def test_invalid_scope(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "bad-scope.md",
        "---\nplaybook: x\nname: X\nleaf_type: y\nscope: wrong\n---\n",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "invalid scope" in proc.stderr


def test_target_specific_without_target_field(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "no-target.md",
        "---\nplaybook: x\nname: X\nleaf_type: y\nscope: target-specific\n---\n",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "target" in proc.stderr.lower()


def test_valid_with_checks(tmp_path: Path) -> None:
    p = _write(tmp_path, "checks.md", _VALID_FM_WITH_CHECKS)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_invalid_check_mode(tmp_path: Path) -> None:
    fm = (
        "---\n"
        "playbook: x\n"
        "name: X\n"
        "leaf_type: y\n"
        "scope: generic\n"
        "checks:\n"
        "  - id: c\n"
        "    name: C\n"
        "    mode: invalid_mode\n"
        "    category: sqli\n"
        "    severity: high\n"
        "---\n"
    )
    p = _write(tmp_path, "bad-mode.md", fm)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "invalid mode" in proc.stderr


def test_empty_checks_list(tmp_path: Path) -> None:
    fm = "---\nplaybook: x\nname: X\nleaf_type: y\nscope: generic\nchecks: []\n---\n"
    p = _write(tmp_path, "empty.md", fm)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "must not be empty" in proc.stderr


def test_directory_argument_expands_recursively(tmp_path: Path) -> None:
    sub = tmp_path / "playbooks"
    sub.mkdir()
    _write(sub, "a.md", _VALID_FM)
    _write(sub, "b.md", _VALID_TARGET_SPECIFIC)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(sub)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
    assert "2 playbook(s) valid" in proc.stdout


def test_multiple_files_with_one_bad(tmp_path: Path) -> None:
    good = _write(tmp_path, "good.md", _VALID_FM)
    bad = _write(
        tmp_path,
        "bad.md",
        "---\nplaybook: x\nscope: generic\n---\n",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(good), str(bad)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    # Good file reported OK, bad file in failures
    assert "OK" in proc.stdout
    assert "FAILURES" in proc.stderr


def test_nonexistent_path_returns_warning_but_zero(tmp_path: Path) -> None:
    good = _write(tmp_path, "good.md", _VALID_FM)
    ghost = tmp_path / "does-not-exist.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(good), str(ghost)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "warning" in proc.stderr


def test_no_paths_returns_usage_error(tmp_path: Path) -> None:
    # nargs="+" means argparse exits with code 2 automatically; just verify
    # the script's own error path when a directory has no .md files.
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(empty)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_importable_module_exposes_validate_playbook(tmp_path: Path) -> None:
    """The script can be imported to call validate_playbook() directly."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_playbooks", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    good = _write(tmp_path, "good.md", _VALID_FM)
    bad = _write(
        tmp_path,
        "bad.md",
        "---\nplaybook: x\nscope: generic\n---\n",
    )
    assert mod.validate_playbook(good) == []
    errs = mod.validate_playbook(bad)
    assert errs
    assert any("name" in e for e in errs)
    assert any("leaf_type" in e for e in errs)
