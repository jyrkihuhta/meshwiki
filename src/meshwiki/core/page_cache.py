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


async def get_pages_metadata() -> list["Page"]:
    """Return all pages with metadata, from cache or disk."""
    global _pages_cache
    if not _ENABLED:
        from meshwiki.core.dependencies import get_storage

        return await get_storage().list_pages_with_metadata()

    if _pages_cache is not None:
        return _pages_cache

    async with _lock:
        if _pages_cache is not None:  # double-check after acquiring lock
            return _pages_cache
        from meshwiki.core.dependencies import get_storage

        _pages_cache = await get_storage().list_pages_with_metadata()
    return _pages_cache  # type: ignore[return-value]


def invalidate() -> None:
    """Clear the cached page list. Thread/coroutine-safe (just a pointer swap)."""
    global _pages_cache
    _pages_cache = None
