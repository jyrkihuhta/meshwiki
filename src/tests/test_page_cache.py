"""Tests for meshwiki.core.page_cache."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear cache state before and after each test."""
    import meshwiki.core.page_cache as pc

    pc.hard_invalidate()
    yield
    pc.hard_invalidate()


@pytest.mark.asyncio
async def test_cache_hit_on_second_call(tmp_path):
    """list_pages_with_metadata is called exactly once across multiple get_pages_metadata calls."""
    import meshwiki.core.page_cache as pc

    mock_pages = []
    call_count = 0

    def fake_list_sync():
        nonlocal call_count
        call_count += 1
        return mock_pages

    mock_storage = AsyncMock()
    mock_storage.list_pages_with_metadata_sync = fake_list_sync

    with patch("meshwiki.core.dependencies._storage", mock_storage):
        result1 = await pc.get_pages_metadata()
        result2 = await pc.get_pages_metadata()
        result3 = await pc.get_pages_metadata()

    assert call_count == 1, f"Expected 1 call, got {call_count}"
    assert result1 is result2 is result3


@pytest.mark.asyncio
async def test_hard_invalidate_clears_cache():
    """After hard_invalidate(), the next call fetches from storage again."""
    import meshwiki.core.page_cache as pc

    call_count = 0

    def fake_list_sync():
        nonlocal call_count
        call_count += 1
        return []

    mock_storage = AsyncMock()
    mock_storage.list_pages_with_metadata_sync = fake_list_sync

    with patch("meshwiki.core.dependencies._storage", mock_storage):
        await pc.get_pages_metadata()
        assert call_count == 1

        pc.hard_invalidate()

        await pc.get_pages_metadata()
        assert call_count == 2


@pytest.mark.asyncio
async def test_invalidate_serves_stale_then_refreshes():
    """invalidate() keeps the cached value available while refreshing in background."""
    import meshwiki.core.page_cache as pc

    call_count = 0

    def fake_list_sync():
        nonlocal call_count
        call_count += 1
        return []

    mock_storage = AsyncMock()
    mock_storage.list_pages_with_metadata_sync = fake_list_sync

    with patch("meshwiki.core.dependencies._storage", mock_storage):
        # Populate cache
        result1 = await pc.get_pages_metadata()
        assert call_count == 1

        # invalidate() keeps old value; background task fires
        pc.invalidate()
        result2 = await pc.get_pages_metadata()
        assert result2 is result1, "should still serve cached value immediately"

        # Let the background refresh task complete
        if pc._refresh_task:
            await pc._refresh_task

        # After refresh, call count should be 2
        assert call_count == 2


@pytest.mark.asyncio
async def test_cache_disabled_via_env(monkeypatch):
    """When MESHWIKI_PAGE_CACHE=0, every call hits storage."""
    monkeypatch.setenv("MESHWIKI_PAGE_CACHE", "0")
    import meshwiki.core.page_cache as pc

    # Reload to pick up the env var change
    importlib.reload(pc)

    call_count = 0

    def fake_list_sync():
        nonlocal call_count
        call_count += 1
        return []

    mock_storage = AsyncMock()
    mock_storage.list_pages_with_metadata_sync = fake_list_sync

    with patch("meshwiki.core.dependencies._storage", mock_storage):
        await pc.get_pages_metadata()
        await pc.get_pages_metadata()

    assert call_count == 2

    # Restore default
    monkeypatch.delenv("MESHWIKI_PAGE_CACHE", raising=False)
    importlib.reload(pc)
