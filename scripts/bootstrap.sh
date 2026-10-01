#!/usr/bin/env bash
# Bootstrap the sandbox shell so clone-root Python imports work.
#
# Armory repos (molly-armory, molly playbook repos, etc.) do not ship a
# pyproject.toml, so `molly.playbook`, `molly.tools.*`, and any other clone-
# root module import fails with ModuleNotFoundError unless PYTHONPATH points
# at the repo root. Trying `pip install -e .` on a bootstrap that lacks
# pyproject.toml wastes a grinder iteration every time.
#
# Usage:
#   source scripts/bootstrap.sh                 # use the default repo root
#   source scripts/bootstrap.sh /path/to/repo   # explicit override
#
# After sourcing, the script prints the resolved repo root and exports
# PYTHONPATH (prepending so any existing value is preserved). It is safe to
# source repeatedly; each call just refreshes PYTHONPATH.
#
# Exit codes: 0 on success. Non-zero only if the supplied path does not
# exist or is not a directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPO_ROOT="${1:-${REPO_ROOT:-${DEFAULT_REPO_ROOT}}}"

if [ ! -d "${REPO_ROOT}" ]; then
    echo "error: repo root '${REPO_ROOT}' is not a directory" >&2
    return 1 2>/dev/null || exit 1
fi

# Convert to an absolute path so the export is unambiguous regardless of the
# caller's cwd (the agent may invoke us from /tmp or /workspace).
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

echo "bootstrap: PYTHONPATH=${PYTHONPATH}"
echo "bootstrap: REPO_ROOT=${REPO_ROOT}"