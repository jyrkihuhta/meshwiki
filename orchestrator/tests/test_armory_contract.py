"""Cross-repo armory contract test (MeshWiki / validator side).

Mirror of molly/tests/test_armory_contract.py, consuming the same fixture
set in molly-armory/contract-test/fixtures/. The validator-side contract
is stricter than Molly's (Molly is permissive by design):

- valid/   — every fixture must produce ZERO errors from _check_playbook_files
- invalid/ — every fixture must produce >= 1 error

If a fixture flips state, that's drift — which is exactly what we're here
to prevent. See the Booking incident, 2026-05-03.

The fixture path is resolved via, in order:
  1. ``MOLLY_ARMORY_PATH`` env var
  2. Sibling at ``/data/molly-armory/contract-test/fixtures`` (CI default)
  3. Sibling at ``../../../molly-armory/contract-test/fixtures``
Skip if not found — keeps CI green when only MeshWiki is in the workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factory.nodes.validate_armory import _check_playbook_files


def _fixture_root() -> Path | None:
    env = os.environ.get("MOLLY_ARMORY_PATH")
    if env:
        c = Path(env) / "contract-test" / "fixtures"
        if c.exists():
            return c
    for candidate in [
        Path("/data/molly-armory/contract-test/fixtures"),
        Path(__file__).resolve().parent.parent.parent.parent
        / "molly-armory"
        / "contract-test"
        / "fixtures",
    ]:
        if candidate.exists():
            return candidate
    return None


_ROOT = _fixture_root()
if _ROOT is None:
    pytestmark = pytest.mark.skip(reason="molly-armory/contract-test/fixtures not found")
    _VALID: list[Path] = []
    _INVALID: list[Path] = []
else:
    _VALID = sorted((_ROOT / "valid").glob("*.md"))
    _INVALID = sorted((_ROOT / "invalid").glob("*.md"))


def _make_pr_file(path: Path) -> list[dict]:
    """Wrap a fixture as a synthetic PR file diff (every line marked +added).

    The validator only inspects added lines, so framing the entire fixture
    as added is correct — we're testing what would happen if the fixture
    was the contents of a fresh playbook PR.
    """
    content = path.read_text()
    patch = "\n".join(f"+{line}" for line in content.splitlines())
    return [{"filename": f"playbooks/{path.name}", "patch": patch, "status": "added"}]


@pytest.mark.parametrize("path", _VALID, ids=lambda p: p.name)
def test_valid_fixture_passes_validator(path: Path) -> None:
    """Every fixture in valid/ must produce zero validation errors."""
    errors = _check_playbook_files(_make_pr_file(path))
    assert errors == [], f"{path.name}: rejected — {errors}"


@pytest.mark.parametrize("path", _INVALID, ids=lambda p: p.name)
def test_invalid_fixture_rejected_by_validator(path: Path) -> None:
    """Every fixture in invalid/ must produce at least one validation error."""
    errors = _check_playbook_files(_make_pr_file(path))
    assert errors, (
        f"{path.name}: validator missed an invalid fixture — broken playbooks "
        "would land. Either tighten the validator or move the fixture to valid/."
    )


def test_fixtures_present() -> None:
    if _ROOT is None:
        pytest.skip("fixtures not found")
    assert _VALID, f"no valid fixtures under {_ROOT / 'valid'}"
    assert _INVALID, f"no invalid fixtures under {_ROOT / 'invalid'}"
