"""Seed the staging wiki with N planned factory tasks to pressure-test the scheduler.

Usage:
    # Seed 20 tasks (default)
    python scripts/seed_pressure_test.py

    # Seed 50 tasks at a custom URL
    python scripts/seed_pressure_test.py --count 50 --url https://staging.wiki.penni.fi

    # Delete all PressureTest_ pages created by a previous run
    python scripts/seed_pressure_test.py --delete

Environment variables:
    MESHWIKI_URL      Wiki base URL (default: https://staging.wiki.penni.fi)
    MESHWIKI_API_KEY  Bearer token for the wiki API

All created pages are named  PressureTest_NNN_<slug>  so they are trivially
identifiable and safe to bulk-delete without touching real tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_URL = "https://staging.wiki.penni.fi"
PAGE_PREFIX = "PressureTest_"

PRIORITIES = ["urgent", "high", "normal", "low"]

TASK_TEMPLATES = [
    {
        "slug": "add_copy_button",
        "title": "Add copy-to-clipboard button to code blocks",
        "description": (
            "Code blocks in the wiki lack a copy button. "
            "Add a small JS snippet that injects a copy icon into every `<pre><code>` block."
        ),
        "criteria": "- Copy button appears on all code blocks\n- Clicking copies the text to clipboard",
    },
    {
        "slug": "improve_search_ranking",
        "title": "Improve full-text search result ranking",
        "description": (
            "Search results are returned in arbitrary order. "
            "Rank by recency (modified date) as a tiebreaker when relevance scores are equal."
        ),
        "criteria": "- Recently modified pages appear above older ones with equal match score",
    },
    {
        "slug": "add_page_word_count",
        "title": "Show word count in page metadata strip",
        "description": (
            "Add a word count to the page info strip beneath the title so readers "
            "can gauge reading time at a glance."
        ),
        "criteria": "- Word count shown in page header\n- Updates when page is edited",
    },
    {
        "slug": "fix_toc_anchor_offset",
        "title": "Fix TOC anchor scroll offset for sticky header",
        "description": (
            "Clicking a TOC link scrolls the target heading behind the sticky top nav. "
            "Add a CSS scroll-margin-top to all heading anchors."
        ),
        "criteria": "- TOC links scroll heading into view below the header",
    },
    {
        "slug": "add_backlink_count",
        "title": "Show backlink count on page view",
        "description": (
            "Display how many other wiki pages link to the current page. "
            "Query the graph engine and render a small badge near the page title."
        ),
        "criteria": "- Backlink count badge visible on page view\n- Zero count hidden",
    },
    {
        "slug": "dark_mode_code_contrast",
        "title": "Improve dark mode code block contrast",
        "description": (
            "Several highlight.js themes have low contrast in dark mode. "
            "Switch to a dark-specific theme when data-theme='dark' is active."
        ),
        "criteria": "- Code blocks readable in dark mode\n- Light mode unaffected",
    },
    {
        "slug": "keyboard_shortcut_new_page",
        "title": "Add keyboard shortcut to create new page",
        "description": (
            "Power users want to create a new page without leaving the keyboard. "
            "Add Ctrl+Shift+N (Cmd+Shift+N on Mac) that opens the new-page dialog."
        ),
        "criteria": "- Shortcut opens new-page form\n- Shortcut documented in help",
    },
    {
        "slug": "tag_autocomplete_editor",
        "title": "Add tag autocomplete in frontmatter editor",
        "description": (
            "When editing frontmatter tags, suggest existing tags as the user types "
            "to encourage consistent tagging."
        ),
        "criteria": "- Suggestions appear while typing tag values\n- Existing tags shown first",
    },
]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


async def create_page(client: httpx.AsyncClient, name: str, content: str) -> bool:
    url = f"/api/v1/pages/{name}"
    resp = await client.put(url, json={"name": name, "content": content})
    if resp.status_code in (200, 201):
        return True
    print(f"  ERROR {resp.status_code}: {resp.text[:120]}", file=sys.stderr)
    return False


async def list_pressure_pages(client: httpx.AsyncClient) -> list[str]:
    """Return all page names that start with PAGE_PREFIX (underscore or space variant)."""
    resp = await client.get("/api/v1/pages")
    resp.raise_for_status()
    all_pages = resp.json()
    # Wiki may normalise underscores to spaces in stored names
    prefix_space = PAGE_PREFIX.replace("_", " ")
    return [
        p["name"] if isinstance(p, dict) else p
        for p in all_pages
        if (p["name"] if isinstance(p, dict) else p).startswith(PAGE_PREFIX)
        or (p["name"] if isinstance(p, dict) else p).startswith(prefix_space)
    ]


async def delete_page(client: httpx.AsyncClient, name: str) -> bool:
    resp = await client.post(f"/api/v1/pages/{name}/delete")
    if resp.status_code in (200, 204):
        return True
    # Try alternative endpoint
    resp2 = await client.delete(f"/api/v1/pages/{name}")
    return resp2.status_code in (200, 204)


def build_page_content(num: int, template: dict, priority: str) -> tuple[str, str]:
    """Return (page_name, content) for task number *num*."""
    name = f"{PAGE_PREFIX}{num:03d}_{template['slug']}"
    content = (
        f"---\n"
        f"title: {template['title']}\n"
        f"type: task\n"
        f"status: planned\n"
        f"assignee: factory\n"
        f"priority: {priority}\n"
        f"skip_decomposition: true\n"
        f"tags:\n"
        f"  - factory\n"
        f"  - pressure-test\n"
        f"---\n\n"
        f"# {template['title']}\n\n"
        f"{template['description']}\n\n"
        f"## Acceptance Criteria\n\n"
        f"{template['criteria']}\n\n"
        f"## Note\n\n"
        f"This is a pressure-test task. Safe to delete.\n"
    )
    return name, content


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


async def seed(client: httpx.AsyncClient, count: int) -> None:
    print(f"Seeding {count} planned tasks (prefix={PAGE_PREFIX!r})…\n")

    templates = itertools.cycle(TASK_TEMPLATES)
    priorities = itertools.cycle(PRIORITIES)

    created = 0
    t0 = time.monotonic()

    for i in range(1, count + 1):
        template = next(templates)
        priority = next(priorities)
        name, content = build_page_content(i, template, priority)

        ok = await create_page(client, name, content)
        status = "✓" if ok else "✗"
        print(f"  {status} [{priority:6}] {name}")
        if ok:
            created += 1

    elapsed = time.monotonic() - t0
    print(f"\nCreated {created}/{count} tasks in {elapsed:.1f}s")
    print(
        "\nScheduler tip: set FACTORY_MAX_CONCURRENT_PARENT_TASKS=2 and watch the "
        "factory dashboard to verify priority ordering and cap enforcement."
    )
    print(f"\nTo clean up: python scripts/seed_pressure_test.py --delete")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def delete_all(client: httpx.AsyncClient) -> None:
    print(f"Finding pages with prefix {PAGE_PREFIX!r}…")

    try:
        names = await list_pressure_pages(client)
    except Exception as exc:
        print(f"ERROR listing pages: {exc}", file=sys.stderr)
        sys.exit(1)

    if not names:
        print("No pressure-test pages found.")
        return

    print(f"Found {len(names)} page(s) to delete:\n")
    deleted = 0
    for name in sorted(names):
        ok = await delete_page(client, name)
        status = "✓" if ok else "✗"
        print(f"  {status} {name}")
        if ok:
            deleted += 1

    print(f"\nDeleted {deleted}/{len(names)} pages.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=20, help="Number of tasks to seed (default: 20)")
    parser.add_argument("--url", default=os.getenv("MESHWIKI_URL", DEFAULT_URL), help="Wiki base URL")
    parser.add_argument("--api-key", default=os.getenv("MESHWIKI_API_KEY", ""), dest="api_key", help="Bearer token")
    parser.add_argument("--delete", action="store_true", help="Delete all PressureTest_ pages instead of seeding")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: set MESHWIKI_API_KEY or pass --api-key", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {args.api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=30.0) as client:
        if args.delete:
            await delete_all(client)
        else:
            await seed(client, args.count)


if __name__ == "__main__":
    asyncio.run(main())
