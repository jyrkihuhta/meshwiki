"""In-process cache for list_pages_with_metadata.

Wraps the expensive disk scan (glob + read every .md file) so that repeated
calls within a single request — and across concurrent requests — hit memory.

Refresh strategy (stale-while-revalidate):
  - invalidate() marks the cache as stale but does NOT clear it.
  - get_pages_metadata() returns the stale value immediately (fast path) and
    fires a background asyncio.Task to rebuild from disk.
  - The background task replaces the cache atomically only when its generation
    still matches the current one (hard_invalidate increments generation, which
    silently drops any in-flight background rebuild).
  - Factory bots write pages every ~30 s; without this they would constantly
    keep the cache cold.

Hard-clear (used only on storage swap in tests):
  - hard_invalidate() increments the generation counter (causing any in-flight
    background task to discard its result) and sets the cache to None, forcing
    the next read to block on the disk scan.

Disable with env var MESHWIKI_PAGE_CACHE=0 (useful in tests that assert exact
call counts to the underlying storage).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshwiki.core.models import Page

_ENABLED: bool = os.getenv("MESHWIKI_PAGE_CACHE", "1") != "0"

_lock: asyncio.Lock = asyncio.Lock()
_pages_cache: list[Any] | None = None  # list[Page], typed loosely to avoid import cycle
_tree_cache: list[Any] | None = None  # list[dict] page tree for sidebar
_stale: bool = False
_refresh_task: asyncio.Task[None] | None = None

# Minimum seconds between rebuild *starts*. A rebuild scans every page on
# disk (list_pages_with_metadata_sync) — expensive at wiki scale. This is a
# second line of defense beyond ws_manager's per-page event dedup: that dedup
# is keyed per page, so a storm that touches many *different* pages at once
# (e.g. a bind-mount metadata event sweeping the whole directory) still slips
# through it, since each distinct page passes its own fresh dedup window.
# Found 2026-09-05 — see PLAN.md M2 for the full incident.
_MIN_REBUILD_INTERVAL_SECS = 5.0
_last_rebuild_started: float = 0.0
_generation: int = 0  # incremented by hard_invalidate; background tasks abort if stale


async def get_pages_metadata() -> list["Page"]:
    """Return all pages with metadata, from cache or disk."""
    global _pages_cache
    if not _ENABLED:
        from meshwiki.core.dependencies import get_storage

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, get_storage().list_pages_with_metadata_sync
        )

    if _pages_cache is not None:
        return _pages_cache

    async with _lock:
        if _pages_cache is not None:  # double-check after acquiring lock
            return _pages_cache
        from meshwiki.core.dependencies import get_storage

        loop = asyncio.get_running_loop()
        storage = get_storage()
        # list_pages_with_metadata_sync does synchronous disk I/O (glob + read
        # every .md file).  Running in a thread so it doesn't block the event
        # loop and starve other requests / health checks during the scan.
        _pages_cache = await loop.run_in_executor(
            None, storage.list_pages_with_metadata_sync
        )
    return _pages_cache  # type: ignore[return-value]


async def get_page_tree() -> list[dict]:
    """Return the sidebar page tree, from cache or computed from page list."""
    global _tree_cache
    if not _ENABLED or _tree_cache is None:
        from meshwiki.main import build_page_tree_sync

        pages = await get_pages_metadata()
        tree = build_page_tree_sync(pages)
        if _ENABLED:
            _tree_cache = tree
        return tree
    return _tree_cache


async def _rebuild(gen: int, delay: float = 0.0) -> None:
    """Background task: rebuild pages + tree and replace the cache atomically.

    Silently discards the result if ``gen`` no longer matches ``_generation``
    (i.e. hard_invalidate() was called while we were rebuilding).

    ``delay`` (if >0) is slept off *before* the scan, so bursts of invalidate()
    calls that arrive while this task is pending coalesce into it instead of
    each scheduling their own — see _MIN_REBUILD_INTERVAL_SECS above.
    """
    global _pages_cache, _tree_cache, _stale, _refresh_task, _last_rebuild_started
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        if gen != _generation:
            return  # superseded by hard_invalidate() while we were waiting
        _last_rebuild_started = time.monotonic()

        from meshwiki.core.dependencies import get_storage
        from meshwiki.main import build_page_tree_sync

        loop = asyncio.get_running_loop()
        storage = get_storage()
        new_pages = await loop.run_in_executor(
            None, storage.list_pages_with_metadata_sync
        )
        new_tree = build_page_tree_sync(new_pages)
        if gen == _generation:  # still relevant — not superseded by hard_invalidate
            _pages_cache = new_pages
            _tree_cache = new_tree
            _stale = False
    except asyncio.CancelledError:
        pass
    finally:
        if gen == _generation:
            _refresh_task = None


def invalidate() -> None:
    """Mark the cache stale and kick off a background refresh.

    The existing cached values are kept so in-flight and subsequent requests
    return immediately with the previous data while the disk scan runs.
    If no cache exists yet (first call or after hard_invalidate), this is a
    no-op — the next read will populate normally.
    """
    global _stale, _refresh_task
    if not _ENABLED:
        return
    _stale = True
    if _pages_cache is None:
        return  # nothing to serve stale; let the next read block as usual
    if _refresh_task is None or _refresh_task.done():
        elapsed = time.monotonic() - _last_rebuild_started
        delay = max(0.0, _MIN_REBUILD_INTERVAL_SECS - elapsed)
        try:
            loop = asyncio.get_running_loop()
            _refresh_task = loop.create_task(_rebuild(_generation, delay))
        except RuntimeError:
            pass  # no running loop (e.g. module-level call in tests)


def hard_invalidate() -> None:
    """Clear the cache entirely (blocks next read on a fresh disk scan).

    Increments the generation counter so any in-flight background rebuild
    discards its result instead of overwriting the freshly-cleared cache.
    Use only when swapping the underlying storage instance (e.g. test reloads).
    """
    global _pages_cache, _tree_cache, _stale, _refresh_task, _generation, _last_rebuild_started
    _generation += 1
    _pages_cache = None
    _tree_cache = None
    _stale = False
    _last_rebuild_started = 0.0
    if _refresh_task is not None and not _refresh_task.done():
        _refresh_task.cancel()
    _refresh_task = None
