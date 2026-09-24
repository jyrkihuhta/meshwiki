#!/usr/bin/env python3
"""One-shot migration: flatten Docs/ and Notes/ subpages to root.

Usage:
    python scripts/flatten_subpages.py [--dry-run] [--reverse]

Options:
    --dry-run   Show what would happen without making changes.
    --reverse   Move flat pages back under their original subdirectories
                (restores pre-migration layout).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PAGES_DIR = REPO_ROOT / "src" / "data" / "pages"

# Mapping: old slash path → new flat name (without .md extension).
# This is a one-shot migration script for the specific Docs/ and Notes/ pages
# that existed at the time of the MoC navigation overhaul.  It is not a general
# tool for future page moves; add an entry here only if running a second migration.
MIGRATIONS: dict[str, str] = {
    "Docs/Architecture_Overview": "Architecture_Overview",
    "Docs/Getting_Started": "Getting_Started",
    "Docs/Kubernetes_Setup": "Kubernetes_Setup",
    "Docs/Markdown_Guide": "Markdown_Guide",
    "Docs/MetaTable_Queries": "MetaTable_Queries",
    "Docs/Python_Development": "Python_Development",
    "Docs/Rust_Graph_Engine": "Rust_Graph_Engine",
    "Docs/Wiki_Best_Practices": "Wiki_Best_Practices",
    "Notes/Meeting_2026-02-10": "Meeting_2026-02-10",
}

# Regex that matches [[Old/Name]] or [[Old/Name|display text]]
# and replaces with [[NewName]] or [[NewName|display text]]
_WIKILINK_PAT = re.compile(
    r"\[\[(" + "|".join(re.escape(old) for old in MIGRATIONS) + r")(\|[^\]]+)?\]\]"
)


def _build_replacement_map() -> dict[str, str]:
    return {old: new for old, new in MIGRATIONS.items()}


def rewrite_links(content: str, replacement_map: dict[str, str]) -> str:
    def replace(m: re.Match) -> str:
        old = m.group(1)
        display = m.group(2) or ""
        new = replacement_map[old]
        return f"[[{new}{display}]]"

    return _WIKILINK_PAT.sub(replace, content)


def run(dry_run: bool = False, reverse: bool = False) -> None:
    replacement_map = _build_replacement_map()

    if reverse:
        _do_reverse(dry_run)
        return

    # 1. Move files from subdirectory paths to flat root
    for old_rel, new_name in MIGRATIONS.items():
        old_path = PAGES_DIR / Path(old_rel.replace("/", "/") + ".md")
        new_path = PAGES_DIR / f"{new_name}.md"

        if old_path.exists():
            print(
                f"MOVE  {old_path.relative_to(REPO_ROOT)} → {new_path.relative_to(REPO_ROOT)}"
            )
            if not dry_run:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)
        elif new_path.exists():
            print(f"SKIP  {new_name}.md already at root (already migrated)")
        else:
            print(f"WARN  Neither {old_path.name} nor {new_path.name} found — skipping")

    # 2. Rewrite [[Docs/X]] / [[Notes/X]] links in all .md files
    for md_file in PAGES_DIR.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        new_content = rewrite_links(content, replacement_map)
        if new_content != content:
            rel = md_file.relative_to(REPO_ROOT)
            print(f"LINKS {rel}")
            if not dry_run:
                md_file.write_text(new_content, encoding="utf-8")

    # 3. Clean up empty subdirectories
    for prefix in ("Docs", "Notes"):
        subdir = PAGES_DIR / prefix
        if subdir.exists() and not any(subdir.iterdir()):
            print(f"RMDIR {subdir.relative_to(REPO_ROOT)}")
            if not dry_run:
                subdir.rmdir()

    if dry_run:
        print("\n(dry-run: no changes made)")
    else:
        print("\nMigration complete.")


def _build_reverse_replacement_map() -> dict[str, str]:
    """Return flat-name → slash-path mapping for wiki-link reverse rewrite."""
    return {new: old for old, new in MIGRATIONS.items()}


def rewrite_links_reverse(content: str, reverse_map: dict[str, str]) -> str:
    """Rewrite [[FlatName]] → [[Dir/FlatName]] for all known migrations."""
    pat = re.compile(
        r"\[\[(" + "|".join(re.escape(flat) for flat in reverse_map) + r")(\|[^\]]+)?\]\]"
    )

    def replace(m: re.Match) -> str:
        flat = m.group(1)
        display = m.group(2) or ""
        original = reverse_map[flat]
        return f"[[{original}{display}]]"

    return pat.sub(replace, content)


def _do_reverse(dry_run: bool) -> None:
    reverse_map = _build_reverse_replacement_map()

    # 1. Rewrite [[FlatName]] links back to [[Dir/OriginalName]] before moving files,
    #    so that links resolve correctly under the restored subdirectory layout.
    for md_file in PAGES_DIR.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        new_content = rewrite_links_reverse(content, reverse_map)
        if new_content != content:
            rel = md_file.relative_to(REPO_ROOT)
            print(f"LINKS {rel}")
            if not dry_run:
                md_file.write_text(new_content, encoding="utf-8")

    # 2. Move flat files back under their original subdirectories.
    for old_rel, new_name in MIGRATIONS.items():
        new_path = PAGES_DIR / f"{new_name}.md"
        old_path = PAGES_DIR / Path(old_rel + ".md")

        if new_path.exists():
            print(
                f"MOVE  {new_path.relative_to(REPO_ROOT)} → {old_path.relative_to(REPO_ROOT)}"
            )
            if not dry_run:
                old_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.rename(old_path)
        else:
            print(f"SKIP  {new_name}.md not found at root — already reversed?")

    if dry_run:
        print("\n(dry-run: no changes made)")
    else:
        print("\nReverse migration complete.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    reverse = "--reverse" in sys.argv
    run(dry_run=dry_run, reverse=reverse)
