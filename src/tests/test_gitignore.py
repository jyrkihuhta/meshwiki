"""Verify the repository .gitignore correctly excludes Python bytecode.

These tests guard against the regression that motivated this task: ``__pycache__``
directories and ``.pyc`` files showing up as unstaged changes after ``pytest``
runs, which blocks ``git rebase origin/staging`` and forces a stash dance.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GITIGNORE = REPO_ROOT / ".gitignore"


def _read_gitignore() -> str:
    assert GITIGNORE.exists(), f"missing root .gitignore at {GITIGNORE}"
    return GITIGNORE.read_text()


def _check_ignore(relative_path: str) -> bool:
    """Return True if git considers ``relative_path`` ignored in the repo."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "__pycache__/foo.pyc",
        "src/meshwiki/__pycache__/main.cpython-312.pyc",
        "src/meshwiki/core/__pycache__/parser.cpython-312.pyc",
        "src/tests/__pycache__/test_parser.cpython-312.pyc",
        "orchestrator/__pycache__/foo.pyc",
        "orchestrator/factory/__pycache__/bar.pyc",
        "src/meshwiki/foo.pyc",
        "orchestrator/factory/agents/bot.pyc",
    ],
)
def test_gitignore_excludes_pycache_artifacts(path: str) -> None:
    """Bytecode files and __pycache__ directories must be ignored everywhere."""
    assert _check_ignore(path), f"{path!r} is not ignored by .gitignore"


def test_gitignore_has_pycache_directive() -> None:
    """The .gitignore must contain an explicit __pycache__/ rule."""
    content = _read_gitignore()
    assert re.search(
        r"^__pycache__/?$", content, re.MULTILINE
    ), "root .gitignore must include a top-level __pycache__/ rule"


def test_gitignore_has_pyc_extension() -> None:
    """The .gitignore must explicitly cover .pyc bytecode files."""
    content = _read_gitignore()
    assert "*.pyc" in content, "root .gitignore must include *.pyc"


def test_no_tracked_pycache_files() -> None:
    """No __pycache__ directories or .pyc files should be tracked in git."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = result.stdout.splitlines()
    offenders = [
        path for path in tracked if "__pycache__" in path or path.endswith(".pyc")
    ]
    assert not offenders, f"unexpected tracked bytecode files: {offenders}"


def test_pytest_run_leaves_no_untracked_pycache(tmp_path: Path) -> None:
    """Acceptance criterion: pytest must not leave untracked bytecode behind.

    Touching a Python file and compiling it (the cheapest stand-in for a pytest
    run that imports modules) must not surface ``__pycache__`` or ``.pyc``
    artifacts via ``git status --porcelain``.
    """
    import py_compile

    src_py = tmp_path / "sample_module.py"
    src_py.write_text("x = 1\n")
    py_compile.compile(str(src_py), cfile=str(tmp_path / "sample_module.pyc"))
    (tmp_path / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / "__pycache__" / "sample.pyc").write_bytes(b"")

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    porcelain = result.stdout
    offenders = [
        line
        for line in porcelain.splitlines()
        if "__pycache__" in line or line.endswith(".pyc")
    ]
    assert not offenders, f"pytest would leak bytecode into git status: {offenders}"
