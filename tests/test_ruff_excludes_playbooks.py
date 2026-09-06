"""Tests that the repo `.ruff.toml` excludes the `playbooks/` directory.

Playbooks are Markdown files with YAML frontmatter. Running `ruff check`
against them produces `No Python files found`; running `black --check`
against them fails with `Cannot parse: 1:3: ---` (the frontmatter). The
factory should exclude `playbooks/` so graders and humans running ruff
at the repo root see a clean exit.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repo_root_has_ruff_toml() -> None:
    assert (REPO_ROOT / ".ruff.toml").exists(), (
        ".ruff.toml must exist at the repo root so `ruff check .` works cleanly"
    )


def test_ruff_toml_excludes_playbooks_dir() -> None:
    """`.ruff.toml` must list `playbooks/` in `extend-exclude`.

    Per the Factory task "Skip ruff/black on .md playbook files in agent
    prompts" (uuid 6ccbf918-...), agents kept invoking ruff against
    `playbooks/*.md` and wasting turns on a known-failing command.
    """
    cfg_path = REPO_ROOT / ".ruff.toml"
    if not cfg_path.exists():
        pytest.fail(f".ruff.toml not found at {cfg_path}")
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert "extend-exclude" in data, (
        "ruff config must have a top-level `extend-exclude` key"
    )
    excludes = data["extend-exclude"]
    assert isinstance(excludes, list), "`extend-exclude` must be a list"
    assert "playbooks/" in excludes, (
        "ruff config must exclude `playbooks/` — those are markdown files "
        "with YAML frontmatter that ruff can't lint"
    )


def test_ruff_toml_is_valid_toml() -> None:
    """The config file must parse as TOML so ruff actually accepts it."""
    cfg_path = REPO_ROOT / ".ruff.toml"
    if not cfg_path.exists():
        pytest.skip(".ruff.toml not present")
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert data, ".ruff.toml must not be empty"
