#!/usr/bin/env bash
# Run the Molly PlaybookLoader tests with sane defaults.
#
# Usage:
#   scripts/test_playbook.sh                  # all PlaybookLoader tests
#   scripts/test_playbook.sh <playbook_slug>  # narrow to one playbook
#
# Why this script exists
# ----------------------
# Grinder agents repeatedly hit two failure modes when validating playbook
# changes:
#
#   1. They ran `python -m pytest -k "other"` (or similar broad negative
#      filters), which deselected 299 tests and exited with a confusing
#      warning instead of running the one test they actually cared about.
#   2. They ran the full `pytest tests/` suite, which pulls in `cryptography`-
#      dependent fixtures that aren't installed in the grinder image and
#      timed out at 120s.
#
# This script encodes the known-good invocation: it always uses
# `--timeout=60` and selects tests with `-k <playbook_slug>` (or runs the
# full loader suite with no `-k` when no slug is given, which produces no
# "X deselected" warnings).
#
# Environment
# -----------
# PYTHONPATH is set to the repo root so that `molly.playbook_loader`
# (the armory's loader class) is importable in pytest without an editable
# install. Override with `PYTHONPATH=...` before invoking if needed.
#
# Exit codes follow pytest: 0 = all selected tests passed, non-zero = at
# least one failed / errored / was collected.
set -euo pipefail

# Resolve repo root from the script's own location so the script works
# regardless of the caller's cwd (e.g. when called from CI with `bash
# scripts/test_playbook.sh`).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${PYTHON_ROOT:-${REPO_ROOT}}${PYTHONPATH:+:${PYTHONPATH}}"

# Per-test timeout — keeps the 60s-per-test contract that the grinder
# prompt advertises. pytest-timeout is required for `--timeout` to be
# honored; the loader tests themselves should be fast (<5s each).
TIMEOUT_SECONDS="${TEST_PLAYBOOK_TIMEOUT:-60}"

PLAYBOOK_TEST_FILE="tests/test_playbook_loader.py"

if [ ! -f "${REPO_ROOT}/${PLAYBOOK_TEST_FILE}" ]; then
    echo "error: ${PLAYBOOK_TEST_FILE} not found at ${REPO_ROOT}" >&2
    echo "  This script is designed to be run from the armory repo root." >&2
    echo "  If you are running it from MeshWiki, the playbook-loader tests" >&2
    echo "  live in the sibling molly-armory repo." >&2
    exit 2
fi

if [ $# -eq 0 ]; then
    # No playbook slug given. Use NO `-k` filter so pytest doesn't warn
    # about "N deselected" — every test in the loader file is relevant.
    echo "Running all PlaybookLoader tests (no filter, ${TIMEOUT_SECONDS}s/test)..."
    exec python -m pytest "${PLAYBOOK_TEST_FILE}" \
        --timeout="${TIMEOUT_SECONDS}" \
        -v
fi

PLAYBOOK_SLUG="$1"

# Sanity check the slug: Molly playbook slugs are lowercase snake_case.
# Reject anything that looks like a glob or shell-injection attempt so the
# `-k` filter can't be hijacked into deselecting real tests.
if ! [[ "${PLAYBOOK_SLUG}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "error: invalid playbook slug '${PLAYBOOK_SLUG}'" >&2
    echo "  expected: lowercase snake_case (a-z, 0-9, '-', '_')" >&2
    exit 2
fi

# Convert slug dashes to underscores for pytest `-k` matching: the loader
# tests are conventionally named like `test_load_jwt_algorithm_confusion`,
# so `my-playbook` should match `test_load_my_playbook`. The original
# slug (with dashes) is kept as a substring match in case the test uses
# the kebab-case form.
SLUG_UNDERSCORE="${PLAYBOOK_SLUG//-/_}"

echo "Running PlaybookLoader tests for slug '${PLAYBOOK_SLUG}' (${TIMEOUT_SECONDS}s/test)..."
exec python -m pytest "${PLAYBOOK_TEST_FILE}" \
    --timeout="${TIMEOUT_SECONDS}" \
    -k "${PLAYBOOK_SLUG} or ${SLUG_UNDERSCORE}" \
    -v