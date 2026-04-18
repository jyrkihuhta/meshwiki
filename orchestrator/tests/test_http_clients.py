"""Tests verifying that MeshWikiClient and GitHubClient share a single httpx.AsyncClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.integrations.github_client import GitHubClient
from factory.integrations.meshwiki_client import MeshWikiClient


class TestMeshWikiClientSharedClient:
    def test_creates_one_async_client(self) -> None:
        """MeshWikiClient.__init__ must create exactly one httpx.AsyncClient."""
        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _ = MeshWikiClient(base_url="http://localhost", api_key="tok")
            assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        """close() must call aclose() on the underlying AsyncClient."""
        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            client = MeshWikiClient(base_url="http://localhost", api_key="tok")
            await client.close()
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_calls_aclose_on_exit(self) -> None:
        """Exiting async with must call aclose() on the underlying AsyncClient."""
        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            async with MeshWikiClient(base_url="http://localhost", api_key="tok"):
                pass
            mock_instance.aclose.assert_awaited_once()


class TestGitHubClientSharedClient:
    def test_creates_one_async_client(self) -> None:
        """GitHubClient.__init__ must create exactly one httpx.AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            _ = GitHubClient(token="tok", repo="owner/repo")
            assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        """close() must call aclose() on the underlying AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            client = GitHubClient(token="tok", repo="owner/repo")
            await client.close()
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_calls_aclose_on_exit(self) -> None:
        """Exiting async with must call aclose() on the underlying AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            async with GitHubClient(token="tok", repo="owner/repo"):
                pass
            mock_instance.aclose.assert_awaited_once()
