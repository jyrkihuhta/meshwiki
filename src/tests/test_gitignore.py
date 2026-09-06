"""Verify .gitignore covers Python bytecode and test artifacts.

These tests guard against regressions where Python bytecode files or test
caches get tracked in git, which forces a `git stash && rebase && stash pop`
dance during rebases.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git_ls_files(pattern: str) -> list[str]:
    """Return tracked paths matching *pattern* (regex)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if re.search(pattern, line)]


def _git_check_ignore(*paths: str) -> list[str]:
    """Return paths that git would ignore from the given list."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


@pytest.mark.parametrize(
    "pattern",
    ["__pycache__", r"\.pyc$", r"\.pyo$"],
)
def test_gitignore_has_bytecode_pattern(pattern: str) -> None:
    """The given sample path (regex) must be ignored by git."""
    sample = {
        "__pycache__": "src/__pycache__/foo.cpython-313.pyc",
        r"\.pyc$": "src/meshwiki/loose.pyc",
        r"\.pyo$": "src/meshwiki/loose.pyo",
    }[pattern]
    ignored = _git_check_ignore(sample)
    assert ignored, f".gitignore must ignore {sample!r}"


@pytest.mark.parametrize(
    "sample",
    [
        ".pytest_cache/v/cache/lastfailed",
        ".mypy_cache/3.13/meshwiki/foo.json",
        ".ruff_cache/.gitignore",
        ".coverage",
    ],
)
def test_gitignore_has_tool_cache_pattern(sample: str) -> None:
    ignored = _git_check_ignore(sample)
    assert ignored, f".gitignore must ignore {sample!r}"


def test_gitignore_ignores_bytecode_paths() -> None:
    """A fresh pycache tree and loose .pyc files must be ignored."""
    ignored = _git_check_ignore(
        "src/__pycache__/foo.cpython-313.pyc",
        "src/meshwiki/__pycache__/parser.cpython-313.pyc",
        "src/meshwiki/loose.pyc",
    )
    assert ignored, "Expected bytecode paths to be ignored by .gitignore"


def test_gitignore_ignores_test_caches() -> None:
    ignored = _git_check_ignore(
        ".pytest_cache/v/cache/lastfailed",
        ".mypy_cache/3.13/meshwiki/foo.json",
        ".ruff_cache/.gitignore",
        "htmlcov/index.html",
        ".coverage",
        "coverage.xml",
    )
    assert ignored, "Expected test/tool cache paths to be ignored by .gitignore"


def test_no_tracked_bytecode_files() -> None:
    """No __pycache__ directories or .pyc files should be tracked in git."""
    bytecode_paths = _git_ls_files(r"(__pycache__|\.pyc$|\.pyo$)")
    assert (
        not bytecode_paths
    ), f"Tracked bytecode should have been removed: {bytecode_paths[:10]}"


def test_no_tracked_tool_caches() -> None:
    """No test/tool cache artifacts should be tracked in git."""
    bad = _git_ls_files(
        r"(\.pytest_cache|\.mypy_cache|\.ruff_cache|htmlcov|\.coverage(\.|$)|coverage\.xml)"
    )
    assert not bad, f"Tracked cache artifacts found: {bad[:10]}"
