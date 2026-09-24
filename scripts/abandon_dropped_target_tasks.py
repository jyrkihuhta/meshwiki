#!/usr/bin/env python3
"""Abandon Factory tasks for targets that have been dropped from Molly.

Polls Molly's GET /factory/active-targets and lists every Task_Playbook_*
page on MeshWiki. Any task whose target prefix no longer appears in the
active set is transitioned to ``blocked`` with a `blocked_reason` field.

Idempotent — already-terminal pages (done, merged, blocked) are skipped.

Designed to be run periodically (cron) or as a manual cleanup after
deliberately dropping a target.

Usage:
    python3 abandon_dropped_target_tasks.py [--dry-run] [--molly-url URL]

Exit codes:
    0 — clean run (changes made or none needed)
    1 — Molly unreachable, MeshWiki unreachable, or unexpected exception
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

# Task pages from FactoryBridge follow this naming convention. The prefix is
# Task_Playbook_<sanitized_target>_<sanitized_vuln_class>_<MMDD> and we want
# the target portion. We extract the leading slug after Task_Playbook_ and
# match it against the active-targets list.
_TASK_NAME_RE = re.compile(r"^Task[_ ]Playbook[_ ]([a-zA-Z0-9_]+?)[_ ][a-zA-Z0-9_]+[_ ]\d{4}")

# Target handles can contain underscores (e.g. "whatnot-anon" → "whatnot_anon"
# after FactoryBridge sanitization). The simple regex above captures only the
# first underscore-separated segment, so we fall back to longest-prefix-match
# against the known active set + known dropped patterns.
_TERMINAL_STATUSES = {"done", "merged", "blocked"}


def fetch_active_targets(molly_url: str) -> set[str]:
    """Return the active target handle set from Molly."""
    url = molly_url.rstrip("/") + "/factory/active-targets"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    return set(data.get("active", []))


def fetch_task_pages(meshwiki_url: str, token: str) -> list[dict]:
    """Return all Task_Playbook_* pages from MeshWiki."""
    url = meshwiki_url.rstrip("/") + "/api/v1/pages"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        all_pages = json.load(r)
    return [
        p for p in all_pages
        if isinstance(p, dict)
        and (p.get("name", "").startswith("Task_Playbook_")
             or p.get("name", "").startswith("Task Playbook "))
    ]


def extract_target_handle(page_name: str, active: set[str]) -> str | None:
    """Pick the best candidate target handle for a Task_Playbook_* page.

    Strategy: every active handle's sanitized form is tested as a prefix
    after ``Task_Playbook_`` / ``Task Playbook ``. The longest match wins.
    Returns the handle in its canonical (active-set) form, or None if no
    active handle prefixes the name (which is what we want — that signals
    "for a dropped target").

    Note: matching against the *active* set means a renamed target could
    look "dropped". That's correct — Factory should re-dispatch under the
    new name; the old task is genuinely orphaned.
    """
    # Normalize separator
    if page_name.startswith("Task Playbook "):
        suffix = page_name[len("Task Playbook "):]
    elif page_name.startswith("Task_Playbook_"):
        suffix = page_name[len("Task_Playbook_"):]
    else:
        return None
    # Check whether suffix starts with any active handle
    for h in sorted(active, key=len, reverse=True):
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", h)
        # Match either underscore or space after the handle
        if (
            suffix.startswith(sanitized + "_")
            or suffix.startswith(sanitized + " ")
            or suffix == sanitized
        ):
            return h
    return None


def is_orphan(page: dict, active: set[str]) -> bool:
    """Return True if the page targets a dropped target."""
    fm = page.get("metadata") or page.get("frontmatter") or {}
    status = fm.get("status", "")
    if status in _TERMINAL_STATUSES:
        return False
    # Subtask pages (Task_Playbook_X_Y_0503_TASK001_...) inherit their
    # parent's target; same matching works.
    return extract_target_handle(page["name"], active) is None


def transition_to_blocked(
    meshwiki_url: str,
    token: str,
    page_name: str,
    reason: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Transition a task page to ``blocked`` with a reason.

    Returns (success, message).
    """
    if dry_run:
        return True, "(dry-run, not sent)"

    # MeshWiki URL-quotes spaces internally; we send the page name verbatim.
    url = meshwiki_url.rstrip("/") + f"/api/v1/tasks/{urllib.request.quote(page_name)}/transition"
    payload = json.dumps({
        "status": "blocked",
        "blocked_reason": reason,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"HTTP {e.code}: {body}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--molly-url", default=os.environ.get("MOLLY_URL", "http://molly:8780"),
                   help="Molly base URL (default: $MOLLY_URL or http://molly:8780)")
    p.add_argument("--meshwiki-url", default=os.environ.get(
        "MESHWIKI_URL", "http://meshwiki-staging:8000"),
                   help="MeshWiki base URL")
    p.add_argument("--meshwiki-token", default=os.environ.get(
        "FACTORY_MESHWIKI_API_KEY") or os.environ.get("MESHWIKI_API_TOKEN", ""),
                   help="MeshWiki API token (default: $FACTORY_MESHWIKI_API_KEY)")
    p.add_argument("--dry-run", action="store_true",
                   help="List orphans without transitioning")
    args = p.parse_args()

    if not args.meshwiki_token:
        print("ERROR: no MeshWiki token (set FACTORY_MESHWIKI_API_KEY or pass --meshwiki-token)",
              file=sys.stderr)
        return 1

    try:
        active = fetch_active_targets(args.molly_url)
    except Exception as exc:
        print(f"ERROR: failed to reach Molly at {args.molly_url}: {exc}", file=sys.stderr)
        return 1

    print(f"Molly active targets: {sorted(active)}")

    try:
        pages = fetch_task_pages(args.meshwiki_url, args.meshwiki_token)
    except Exception as exc:
        print(f"ERROR: failed to fetch tasks from {args.meshwiki_url}: {exc}",
              file=sys.stderr)
        return 1

    print(f"Total Task_Playbook_* pages: {len(pages)}")

    orphans = [p for p in pages if is_orphan(p, active)]
    print(f"Orphan tasks (target dropped): {len(orphans)}")

    if not orphans:
        return 0

    blocked = 0
    skipped = 0
    failed = 0
    for page in orphans:
        name = page["name"]
        fm = page.get("metadata") or page.get("frontmatter") or {}
        status = fm.get("status", "?")
        reason = (
            "Target was removed from molly.json. Auto-transitioned by "
            "abandon_dropped_target_tasks.py — unblock manually if the "
            "target returns."
        )
        ok, msg = transition_to_blocked(
            args.meshwiki_url, args.meshwiki_token, name, reason, args.dry_run
        )
        marker = "[DRY]" if args.dry_run else ("OK" if ok else "FAIL")
        print(f"  {marker} {name} (was: {status}) — {msg}")
        if ok:
            blocked += 1
        else:
            failed += 1
            # in_progress is the only state that can't go to blocked? No —
            # all non-terminal states reach blocked. If this fails, it's a
            # real bug we should surface, not silently retry.

    print()
    print(f"Summary: {blocked} transitioned to blocked, {failed} failed, "
          f"{skipped} skipped")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
