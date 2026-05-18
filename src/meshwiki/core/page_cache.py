"""In-process cache for list_pages_with_metadata.

Wraps the expensive disk scan (glob + read every .md file) so that repeated
calls within a single request — and across concurrent requests — hit memory.

Invalidated by:
  - graph events processed in ws_manager._poll_loop (file watcher → event → clear)
  - write-path calls in main.py (save_page, delete_page) for immediate consistency

Disable with env var MESHWIKI_PAGE_CACHE=0 (useful in tests that assert exact
call counts to the underlying storage).
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meshwiki.core.models import Page

_ENABLED: bool = os.getenv("MESHWIKI_PAGE_CACHE", "1") != "0"

_lock: asyncio.Lock = asyncio.Lock()
_pages_cache: list[Any] | None = None  # list[Page], typed loosely to avoid import cycle
_tree_cache: list[Any] | None = None  # list[dict] page tree for sidebar


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


def invalidate() -> None:
    """Clear the cached page list and tree. Thread/coroutine-safe (just pointer swaps)."""
    global _pages_cache, _tree_cache
    _pages_cache = None
    _tree_cache = None
