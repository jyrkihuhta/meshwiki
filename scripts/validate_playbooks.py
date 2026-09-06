#!/usr/bin/env python3
"""Validate YAML frontmatter in Molly playbook ``.md`` files.

Playbooks are Markdown files with a YAML frontmatter block delimited by
``---`` markers. This script validates the frontmatter contract that
``orchestrator/factory/nodes/validate_armory.py`` checks on PR diffs, but
runs locally on disk so a grinder agent can verify a freshly written
playbook before opening a PR — without waiting for CI.

Usage:
    python scripts/validate_playbooks.py <file.md> [<file.md> ...]
    python scripts/validate_playbooks.py playbooks/      # all *.md under dir

Exit codes:
    0  every file passed (frontmatter is valid YAML and has required keys)
    1  one or more files failed validation (errors printed to stderr)
    2  usage error (no files, bad path)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml

_PLAYBOOK_REQUIRED_FM: tuple[str, ...] = ("playbook", "name", "leaf_type", "scope")
_VALID_PLAYBOOK_SCOPES: frozenset[str] = frozenset({"generic", "target-specific"})
_VALID_MODES: frozenset[str] = frozenset({"deterministic", "analytical", "idea", "oob"})
_VALID_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low", "info", "unknown"}
)
_CHECK_REQUIRED_KEYS: tuple[str, ...] = ("id", "name", "mode", "category", "severity")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _extract_frontmatter(text: str) -> str | None:
    """Return the YAML between the leading ``---`` markers, or ``None``."""
    match = _FRONTMATTER_RE.match(text)
    return match.group(1) if match else None


def _load_frontmatter(path: Path) -> tuple[dict | None, str | None]:
    """Load and parse the YAML frontmatter from ``path``.

    Returns ``(frontmatter_dict, error_string)``. Exactly one is non-None.
    An empty frontmatter block parses to ``{}`` and is returned as ``({}, None)``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read file: {exc}"

    raw = _extract_frontmatter(text)
    if raw is None:
        return None, "missing YAML frontmatter (expected `---` block at top of file)"

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}"

    if parsed is None:
        return {}, None
    if not isinstance(parsed, dict):
        return None, f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
    return parsed, None


def _validate_checks(filename: str, checks: object) -> list[str]:
    """Validate the ``checks:`` list (frontmatter or fenced YAML block)."""
    errors: list[str] = []
    if not isinstance(checks, list):
        return [f"`{filename}`: `checks` must be a list, got {type(checks).__name__}"]
    if not checks:
        return [f"`{filename}`: `checks` list must not be empty"]
    for idx, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"`{filename}`: check[{idx}] must be a mapping")
            continue
        for key in _CHECK_REQUIRED_KEYS:
            if key not in check:
                errors.append(
                    f"`{filename}`: check[{idx}] missing required field `{key}`"
                )
        mode = check.get("mode")
        if mode and mode not in _VALID_MODES:
            errors.append(
                f"`{filename}`: check[{idx}] invalid mode `{mode}` "
                f"(must be one of: {', '.join(sorted(_VALID_MODES))})"
            )
        severity = check.get("severity")
        if severity and severity not in _VALID_SEVERITIES:
            errors.append(
                f"`{filename}`: check[{idx}] invalid severity `{severity}` "
                f"(must be one of: {', '.join(sorted(_VALID_SEVERITIES))})"
            )
    return errors


def validate_playbook(path: Path) -> list[str]:
    """Validate ``path`` as a Molly playbook.

    Returns a list of error strings (empty list means the playbook is valid).
    Mirrors the rules in
    ``orchestrator/factory/nodes/validate_armory.py::_check_playbook_files``
    so that a local pass implies CI pass for the frontmatter portion.
    """
    errors: list[str] = []
    filename = str(path)

    fm, err = _load_frontmatter(path)
    if err is not None:
        errors.append(f"`{filename}`: {err}")
        return errors
    assert fm is not None

    for field in _PLAYBOOK_REQUIRED_FM:
        if field not in fm:
            errors.append(f"`{filename}`: missing required frontmatter field `{field}`")

    scope = fm.get("scope")
    if scope is not None and scope not in _VALID_PLAYBOOK_SCOPES:
        errors.append(
            f"`{filename}`: invalid scope `{scope}` (must be one of: "
            f"{', '.join(sorted(_VALID_PLAYBOOK_SCOPES))})"
        )
    if scope == "target-specific" and not fm.get("target"):
        errors.append(
            f"`{filename}`: scope=target-specific requires a `target:` "
            "field naming which leaf this fires on"
        )

    if "checks" in fm:
        errors.extend(_validate_checks(filename, fm["checks"]))

    return errors


def _iter_targets(paths: Iterable[Path]) -> list[Path]:
    """Expand directories to their ``*.md`` children, keep files as-is."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.rglob("*.md")))
        elif p.is_file():
            out.append(p)
        else:
            print(f"warning: skipping non-existent path: {p}", file=sys.stderr)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Playbook .md files or directories containing them.",
    )
    args = parser.parse_args(argv)

    targets = _iter_targets(args.paths)
    if not targets:
        print("error: no .md files found in given paths", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for path in targets:
        errs = validate_playbook(path)
        if errs:
            all_errors.extend(errs)
        else:
            print(f"OK  {path}")

    if all_errors:
        print("\nFAILURES:", file=sys.stderr)
        for e in all_errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    print(f"\n{len(targets)} playbook(s) valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
