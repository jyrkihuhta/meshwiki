"""Tests for ``scripts/bootstrap.sh``.

These tests pin the acceptance criteria for the documented sandbox shell
bootstrap that the grinder task prompt references. Like the playbook test
script tests, they don't need to actually run a Python import — they
verify the *contract* of the script (file presence, structural contents,
command-line behavior) so a future refactor can't silently regress it.

Why this exists: armory repos (molly-armory, molly playbook repos, etc.)
do not ship a pyproject.toml, so `molly.playbook`, `molly.tools.*`, and
other clone-root module imports fail with ModuleNotFoundError unless
PYTHONPATH points at the repo root. The grinder sandbox already sets
PYTHONPATH=/tmp/repo in its `envs` block and in the kilo command prefix,
but if the agent opens a fresh subshell the env is lost; this script
gives the agent a documented one-liner to re-export it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bootstrap.sh"


# ---------------------------------------------------------------------------
# Acceptance #1 — script exists, is executable, and is a bash script
# ---------------------------------------------------------------------------


def test_bootstrap_script_exists() -> None:
    """Acceptance #1: ``scripts/bootstrap.sh`` must exist in the tree.

    This is the file the grinder task prompt template references so
    agents can re-export PYTHONPATH in a fresh subshell. Without it,
    agents that `cd` away from the repo root lose clone-root module
    imports and waste a retreat iteration retrying `pip install -e .`.
    """
    assert SCRIPT_PATH.is_file(), (
        f"expected {SCRIPT_PATH} to exist; the grinder prompt template "
        "references scripts/bootstrap.sh but no such file is present."
    )


def test_bootstrap_script_is_executable() -> None:
    """The script must be executable so agents can ``source``/invoke it.

    Not all filesystems preserve the +x bit; on Windows or some CI
    containers it may be stripped. We test the executable bit only on
    POSIX systems.
    """
    if os.name == "posix":
        assert os.access(SCRIPT_PATH, os.X_OK), (
            f"{SCRIPT_PATH} is not executable; chmod +x the script so the "
            "grinder prompt template's `source scripts/bootstrap.sh` "
            "invocation actually works."
        )


def test_bootstrap_script_uses_bash() -> None:
    """Shebang must be bash so the script runs under bash explicitly.

    This guards against accidentally committing a ``sh`` shebang that
    later breaks when the script starts using bash-only features (the
    current implementation uses `${VAR//-/_}`-style parameter expansion
    implicitly through `set -euo pipefail` patterns).
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n") or text.startswith(
        "#!/bin/bash\n"
    ), "shebang must be bash, not sh"


# ---------------------------------------------------------------------------
# Acceptance #2 — defaults to repo root when no arg is given
# ---------------------------------------------------------------------------


def test_bootstrap_script_defaults_to_repo_root() -> None:
    """Acceptance #2: with no arg, the script must use the repo root.

    The grinder sandbox clones the repo to ``/tmp/repo`` — that is the
    only path the bootstrap needs to handle by default. The script must
    resolve REPO_ROOT from its own location (``scripts/../``) so it works
    regardless of the caller's cwd.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "SCRIPT_DIR" in text, (
        "script must derive REPO_ROOT from its own location (via BASH_SOURCE) "
        "so it works regardless of caller cwd"
    )
    assert re.search(r'SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE', text), (
        "script must resolve SCRIPT_DIR via BASH_SOURCE so the default REPO_ROOT is correct"
    )


def test_bootstrap_script_exports_pythonpath_by_default() -> None:
    """Acceptance #2: with no arg, the script must ``export PYTHONPATH``
    so clone-root modules import cleanly. Without the default, agents that
    forget to pass an argument get an unset PYTHONPATH and fall back to
    ``pip install -e .``, which fails on armory repos.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*export\s+PYTHONPATH=", text, re.MULTILINE), (
        "script must `export PYTHONPATH=...` so clone-root modules import"
    )


def test_bootstrap_script_accepts_explicit_repo_root() -> None:
    """The script must accept an explicit ``$1`` repo root argument.

    This covers the rare case where the agent clones into a different
    location (e.g. a CI runner that mirrors the repo elsewhere).
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert re.search(r'REPO_ROOT="\$\{1:-', text) or re.search(
        r"\$\{1:-\$\{REPO_ROOT:", text
    ), (
        "script must accept an explicit repo-root argument (`$1`) and fall "
        "back to the default when it is absent"
    )


# ---------------------------------------------------------------------------
# Acceptance #3 — usable location vs. source
# ---------------------------------------------------------------------------


def test_bootstrap_script_source_exports_pythonpath() -> None:
    """Sourcing the script in a fresh shell must export PYTHONPATH.

    The whole point of this script is that the agent can
    ``source scripts/bootstrap.sh`` after ``cd``-ing into a subdirectory
    or opening a new shell, then run ``python -c "import molly.playbook"``
    without setting PYTHONPATH manually. Verify that by sourcing it into
    a bash subprocess and asserting PYTHONPATH points at the repo root.
    """
    proc = subprocess.run(
        ["bash", "-c", f'source {SCRIPT_PATH} && echo "$PYTHONPATH"'],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert proc.returncode == 0, (
        f"sourcing bootstrap.sh failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    pythonpath = proc.stdout.strip()
    expected_root = str(REPO_ROOT.resolve())
    assert expected_root in pythonpath, (
        f"PYTHONPATH after sourcing must include {expected_root!r}; got {pythonpath!r}"
    )


def test_bootstrap_script_rejects_nonexistent_repo_root() -> None:
    """An explicit, nonexistent repo-root argument must exit non-zero.

    This prevents the agent from silently exporting a wrong PYTHONPATH
    if it mistypes the path (e.g. ``/tmp/rpo`` instead of ``/tmp/repo``)
    and then hitting ModuleNotFoundError downstream.
    """
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source {SCRIPT_PATH} /nonexistent/path 2>&1; echo "exit=$?"',
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode != 0, (
        "sourcing bootstrap.sh /nonexistent must exit non-zero; "
        f"got stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "not a directory" in combined or "No such file" in combined, (
        "error message must mention that the repo root is not a directory "
        f"so the agent can self-diagnose; got {combined!r}"
    )


# ---------------------------------------------------------------------------
# Acceptance #4 — task prompt template references the script
# ---------------------------------------------------------------------------


def test_grinder_prompt_references_bootstrap_script() -> None:
    """The grinder task prompt must reference ``scripts/bootstrap.sh``.

    Acceptance criterion: the task prompt template must include a one-
    line note pointing agents at the bootstrap script so they can re-
    export PYTHONPATH in a fresh subshell. Without this note, agents
    that ``cd`` away from the repo root lose clone-root imports and fall
    back to ``pip install -e .``, which is the exact failure mode this
    task was opened to fix.
    """
    from factory.agents.grinder_agent import build_grinder_task_prompt

    prompt = build_grinder_task_prompt(
        subtask={
            "id": "0008-bootstrap-doc",
            "title": "Document PYTHONPATH requirement",
            "wiki_page": "Task_0008",
            "files_touched": [],
        },
        page_content="task body",
        review_feedback="",
        is_rework=False,
        artifact_type=None,
        task_repo_root=None,
        is_meshwiki=True,
        base_branch="staging",
    )
    assert "scripts/bootstrap.sh" in prompt, (
        "task prompt must mention `source scripts/bootstrap.sh` so agents "
        "can re-export PYTHONPATH in a fresh subshell"
    )


def test_grinder_prompt_documents_pythonpath_test_form() -> None:
    """Acceptance criterion: the task prompt must include a one-line note
    telling the agent to invoke tests as
    ``PYTHONPATH=$REPO_ROOT python -m pytest <path>``.
    """
    from factory.agents.grinder_agent import build_grinder_task_prompt

    prompt = build_grinder_task_prompt(
        subtask={
            "id": "0008-bootstrap-doc",
            "title": "Document PYTHONPATH requirement",
            "wiki_page": "Task_0008",
            "files_touched": [],
        },
        page_content="task body",
        review_feedback="",
        is_rework=False,
        artifact_type=None,
        task_repo_root=None,
        is_meshwiki=True,
        base_branch="staging",
    )
    assert "PYTHONPATH=$REPO_ROOT python -m pytest" in prompt, (
        "task prompt must include the `PYTHONPATH=$REPO_ROOT python -m pytest <path>` "
        "test-invocation note so agents always set PYTHONPATH when running pytest"
    )
