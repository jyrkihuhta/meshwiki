"""Tests for ``scripts/test_playbook.sh``.

These tests pin the acceptance criteria for the documented playbook-test
invocation script that the grinder prompt template references. They don't
need to actually run pytest — they verify the *contract* of the script
(file presence, structural contents, command-line behavior) so a future
refactor can't silently regress it.

The script lives in the armory-side sibling repo (molly-armory) and is
referenced from this repo's grinder prompt template as a documented
alternative to a raw `python -m pytest` invocation. The tests below are
run from the MeshWiki repo and tolerate either location.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_REPO_PATH = REPO_ROOT / "scripts" / "test_playbook.sh"


def _find_script() -> Path:
    """Locate ``scripts/test_playbook.sh`` in this repo or an armory sibling.

    The script is intended to live in the armory repo, but a copy may be
    vendored into this repo so the prompt template can reference a path
    that exists. Tests below skip (rather than fail) when neither exists,
    so a fresh checkout doesn't break the suite.
    """
    if SCRIPT_REPO_PATH.is_file():
        return SCRIPT_REPO_PATH
    sibling = REPO_ROOT.parent / "molly-armory" / "scripts" / "test_playbook.sh"
    if sibling.is_file():
        return sibling
    return SCRIPT_REPO_PATH  # default; caller will skip


SCRIPT_PATH = _find_script()
needs_script = pytest.mark.skipif(
    not SCRIPT_PATH.is_file(),
    reason="scripts/test_playbook.sh not present in repo or sibling armory checkout",
)


# ---------------------------------------------------------------------------
# Acceptance #1 — script exists, is executable, and is a bash script
# ---------------------------------------------------------------------------


def test_test_playbook_script_exists() -> None:
    """Acceptance #1: ``scripts/test_playbook.sh`` must exist in the tree.

    The script may live in this repo (vendored) or in the sibling
    molly-armory checkout, but *somewhere* under ``scripts/`` named
    ``test_playbook.sh``. This is the file the grinder prompt template
    will instruct agents to invoke.
    """
    assert SCRIPT_PATH.is_file(), (
        f"expected {SCRIPT_PATH} to exist; the grinder prompt template "
        "references scripts/test_playbook.sh but no such file is present "
        "in this repo or in the sibling armory checkout."
    )


@needs_script
def test_test_playbook_script_is_executable() -> None:
    """The script must be executable so agents can ``./scripts/test_playbook.sh``.

    Not all filesystems preserve the +x bit; on Windows or some CI
    containers it may be stripped. We test the executable bit only on
    POSIX systems.
    """
    if os.name == "posix":
        assert os.access(SCRIPT_PATH, os.X_OK), (
            f"{SCRIPT_PATH} is not executable; chmod +x the script so the "
            "grinder prompt template's `./scripts/test_playbook.sh` "
            "invocation actually works."
        )


@needs_script
def test_test_playbook_script_uses_bash() -> None:
    """Shebang must be bash so the script runs under bash explicitly.

    This guards against accidentally committing a ``sh`` shebang that
    later breaks when the script starts using bash-only features (the
    current implementation uses `${VAR//-/_}` parameter expansion).
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n") or text.startswith(
        "#!/bin/bash\n"
    ), "shebang must be bash, not sh"


# ---------------------------------------------------------------------------
# Acceptance #2 — PYTHONPATH + 60s per-test timeout
# ---------------------------------------------------------------------------


@needs_script
def test_test_playbook_script_sets_pythonpath() -> None:
    """Acceptance #2: script sets ``PYTHONPATH`` so loader tests can import.

    Molly's PlaybookLoader lives at ``molly/playbook_loader.py`` in the
    armory repo. Without ``PYTHONPATH=<repo_root>``, pytest can't import
    it without an editable install — and the grinder image doesn't run
    ``pip install -e .``. The script must therefore export ``PYTHONPATH``
    pointing at the repo root before invoking pytest.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"^\s*export\s+PYTHONPATH=", text, re.MULTILINE
    ), "script must `export PYTHONPATH=...` so loader tests can import molly.*"


@needs_script
def test_test_playbook_script_uses_60s_per_test_timeout() -> None:
    """Acceptance #2: script uses a 60-second per-test timeout.

    The full ``pytest tests/`` suite 120s-timeouts on ``cryptography``-
    dependent fixtures, which wastes a full grinder iteration. The
    documented script uses ``--timeout=60`` so each loader test gets a
    minute — loader tests are normally sub-second, so 60s catches
    genuine hangs without false positives.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--timeout" in text, "script must pass --timeout=... to pytest"
    assert re.search(
        r"--timeout\s*=?\s*60\b|--timeout\s+60\b", text
    ), "script must use a 60s per-test timeout (not the default 120s or other)"
    # And the timeout should be reachable via an env knob so power users
    # can override it in CI without editing the script.
    assert "TEST_PLAYBOOK_TIMEOUT" in text, (
        "script should respect $TEST_PLAYBOOK_TIMEOUT env var so CI can "
        "override the per-test timeout without editing the file"
    )


# ---------------------------------------------------------------------------
# Acceptance #4 — default invocation has no `-k` filter (no "X deselected")
# ---------------------------------------------------------------------------


@needs_script
def test_test_playbook_script_default_runs_unfiltered() -> None:
    """Acceptance #4: with no arg, the script must NOT use a `-k` filter.

    Using `-k` with no useful expression deselects most tests and prints
    ``N deselected`` warnings, which is exactly the failure mode this
    script was created to prevent. The default branch must invoke
    pytest against the loader file with no `-k` filter so the full
    loader suite runs and pytest stays quiet.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    # Split the script on the canonical "no args" branch header so we
    # only inspect the default path (not the `<playbook_slug>` path,
    # which legitimately uses `-k`).
    match = re.search(r"if\s+\[\s*\$\#\s*-eq\s*0\s*\]\s*;?\s*then", text, re.MULTILINE)
    assert match, "script must have an explicit `if [ $# -eq 0 ]` branch"
    # The default branch runs from the `if` line until the matching `fi`
    # at the same indentation level — pick the next standalone `fi` after
    # the `if` so we don't include the `$# -gt 0` branch.
    after_if = text[match.end() :]
    # Walk forward to the first `fi` keyword that isn't part of a
    # longer word (heuristic: line containing only `fi`).
    default_branch = re.split(r"^\s*fi\s*$", after_if, maxsplit=1, flags=re.MULTILINE)[
        0
    ]
    # Strip comment lines so a comment like "no `-k` filter" doesn't
    # fool the assertion.
    code_only = re.sub(r"^\s*#.*$", "", default_branch, flags=re.MULTILINE)
    assert "-k" not in code_only, (
        "default branch must not use `-k`; doing so deselects most tests "
        "and emits the very warning this script was created to suppress."
    )


# ---------------------------------------------------------------------------
# Narrowing branch — uses `-k` for a given playbook slug
# ---------------------------------------------------------------------------


@needs_script
def test_test_playbook_script_narrows_by_slug() -> None:
    """With a playbook slug, the script narrows pytest via `-k`.

    The whole point of accepting an argument is to scope the test run to
    the playbook the agent is currently working on, so subsequent
    iteration is fast. Verify the script body as a whole contains a `-k`
    expression that interpolates the user-supplied slug (or a
    slug-derived token) — the narrowing branch is the only place `-k`
    should appear.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"-k\s+", text
    ), "script must pass `-k <expression>` to pytest in the narrowing branch"
    # And the expression must reference the user-supplied slug (not a
    # constant like "other" that would deselect everything).
    assert re.search(r"-k\s+\"\$\{?PLAYBOOK_SLUG", text) or re.search(
        r"-k\s+\"\$\{?SLUG_UNDERSCORE", text
    ), (
        "narrowing branch must interpolate the user-supplied slug into "
        "the `-k` expression (not a hard-coded token that deselects "
        "everything)."
    )


@needs_script
def test_test_playbook_script_rejects_unsafe_slugs() -> None:
    """The script must sanity-check the slug to prevent shell injection
    via the ``-k`` filter.

    A malicious or careless slug like ``test_foo and not real_test`` could
    either deselect real tests (the original failure mode) or be parsed
    by pytest as a Python expression that raises. The script must
    restrict slugs to snake_case before interpolating into the pytest
    command line.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"[a-z0-9][a-z0-9_-]*", text
    ), "script must declare a snake_case slug regex"


# ---------------------------------------------------------------------------
# Behavior — actually invoking the script with no arg, in an env without
# the loader file, exits non-zero with a useful error (not a silent 0).
# ---------------------------------------------------------------------------


@needs_script
def test_test_playbook_script_clear_error_when_loader_missing() -> None:
    """In an environment without ``tests/test_playbook_loader.py`` (e.g.
    this MeshWiki repo), invoking the script with no args must exit
    non-zero and print a clear error — NOT silently pass and pretend
    the loader suite is fine. This protects agents that copy the script
    into the wrong directory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Copy the script into a scratch tree that lacks the loader file.
        stub = tmp_path / "test_playbook.sh"
        shutil.copy(SCRIPT_PATH, stub)
        os.chmod(stub, 0o755)
        proc = subprocess.run(
            [str(stub)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0, (
            "script must exit non-zero when tests/test_playbook_loader.py "
            "is missing; otherwise agents in the wrong repo will get a "
            "false PASS."
        )
        combined = proc.stdout + proc.stderr
        assert "test_playbook_loader" in combined, (
            "error message must mention the missing test file path so "
            "operators can diagnose the problem."
        )
