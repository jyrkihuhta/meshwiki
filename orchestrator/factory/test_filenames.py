"""Helpers for deriving deterministic test filenames from playbook names.

Background
----------
Grinders have repeatedly committed test files whose stem does NOT match the
playbook they were meant to validate (e.g. ``tests/playbooks/test_mass_assignment_tenant_user_creation_acronis.py``
for the ``tenant-users-get-acronis`` playbook). The mismatch is invisible at
``pytest`` time — the test passes — but it silently validates the wrong
artifact, which then ships to staging.

These helpers centralise the slugification rule so that:

1. The grinder prompt (in ``armory_prompts.PLAYBOOK_SCHEMA``) can tell the
   agent the exact filename to create (``tests/playbooks/test_<snake>.py``).
2. The armory validator (in ``nodes/validate_armory._check_playbook_files``)
   can detect a mismatch when the agent gets it wrong and reject the PR.

The slugification rule mirrors the existing ``slugify`` utility used by
MeshWiki tasks (see ``orchestrator/test_grinder_live.py``), but extended to
also produce the ``test_<x>.py`` filename that the molly-armory contract
test demands.
"""

from __future__ import annotations

import re

__all__ = [
    "slugify",
    "snake_case",
    "playbook_test_filename",
    "expected_test_stem",
]


_NON_ALNUM_HYPHEN = re.compile(r"[^a-z0-9-]+")
_MULTI_HYPHEN = re.compile(r"-+")


def slugify(text: str) -> str:
    """Return a lowercase, hyphen-separated slug of *text*.

    Rules:
    - Lowercase the input.
    - Replace spaces and underscores with hyphens.
    - Strip any character that is not alphanumeric or a hyphen.
    - Collapse multiple consecutive hyphens into one.
    - Strip leading/trailing hyphens.

    Args:
        text: Arbitrary text (e.g. ``"Tenant Users (Get — Acronis)"``).

    Returns:
        A slug suitable for use in filenames (e.g. ``"tenant-users-get-acronis"``).

    Examples:
        >>> slugify("Hello World!")
        'hello-world'
        >>> slugify("tenant-users-get-acronis")
        'tenant-users-get-acronis'
        >>> slugify("  multi   spaces  ")
        'multi-spaces'
    """
    s = (text or "").lower()
    s = s.replace(" ", "-").replace("_", "-")
    s = _NON_ALNUM_HYPHEN.sub("", s)
    s = _MULTI_HYPHEN.sub("-", s)
    return s.strip("-")


def snake_case(text: str) -> str:
    """Return the snake_case form of *text*.

    Equivalent to :func:`slugify` with hyphens converted to underscores.
    The slugify step guarantees no leading/trailing/consecutive separators
    survive, so the conversion is a plain substitution.

    Args:
        text: Arbitrary text.

    Returns:
        A snake_case identifier (e.g. ``"tenant_users_get_acronis"``).

    Examples:
        >>> snake_case("Tenant Users (Get — Acronis)")
        'tenant_users_get_acronis'
        >>> snake_case("Hello-World!")
        'hello_world'
    """
    return slugify(text).replace("-", "_")


def expected_test_stem(playbook_name: str) -> str:
    """Return the test file stem (no ``.py``) the playbook must produce.

    Args:
        playbook_name: The playbook's ``playbook:`` slug (e.g.
            ``"tenant-users-get-acronis"``).

    Returns:
        The stem ``test_<snake_case>`` (e.g. ``"test_tenant_users_get_acronis"``).

    Examples:
        >>> expected_test_stem("tenant-users-get-acronis")
        'test_tenant_users_get_acronis'
    """
    return f"test_{snake_case(playbook_name)}"


def playbook_test_filename(playbook_name: str, tests_dir: str = "tests") -> str:
    """Return the full repo-relative test path the playbook must produce.

    Args:
        playbook_name: The playbook's ``playbook:`` slug.
        tests_dir: Directory where playbook tests live (default ``"tests"``).
            The molly-armory convention is ``tests/playbooks/`` but the
            validator accepts any subpath.

    Returns:
        Path like ``"tests/test_<snake_case>.py"`` or, when *tests_dir* ends in
        ``/playbooks`` / ``playbooks``, the playbook-style layout.

    Examples:
        >>> playbook_test_filename("tenant-users-get-acronis")
        'tests/test_tenant_users_get_acronis.py'
        >>> playbook_test_filename("tenant-users-get-acronis", "tests/playbooks")
        'tests/playbooks/test_tenant_users_get_acronis.py'
    """
    stem = expected_test_stem(playbook_name)
    base = (tests_dir or "tests").rstrip("/")
    return f"{base}/{stem}.py"
