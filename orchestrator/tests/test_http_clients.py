"""Tests verifying that MeshWikiClient and GitHubClient share a single httpx.AsyncClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from factory.integrations.github_client import GitHubClient
from factory.integrations.meshwiki_client import MeshWikiClient

_MW_BASE = "http://localhost"
_GH_BASE = "https://api.github.com"
_GH_REPO = "owner/repo"


class TestMeshWikiClientSharedClient:
    def test_creates_one_async_client(self) -> None:
        """MeshWikiClient.__init__ must create exactly one httpx.AsyncClient."""
        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _ = MeshWikiClient(base_url=_MW_BASE, api_key="tok")
            assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        """close() must call aclose() on the underlying AsyncClient."""
        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient"
        ) as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            client = MeshWikiClient(base_url=_MW_BASE, api_key="tok")
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
            async with MeshWikiClient(base_url=_MW_BASE, api_key="tok"):
                pass
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    @respx.mock
    async def test_same_async_client_reused_across_calls(self) -> None:
        """Multiple method calls must use the same underlying AsyncClient instance.

        If a new AsyncClient were created per call the connection pool would be
        discarded between requests, defeating the purpose of session sharing.
        We verify this by asserting that the internal ``_client`` object is
        identical before and after two successive API calls.
        """
        respx.get(f"{_MW_BASE}/api/v1/pages/PageA").mock(
            return_value=httpx.Response(200, json={"name": "PageA", "content": ""})
        )
        respx.put(f"{_MW_BASE}/api/v1/pages/PageA").mock(
            return_value=httpx.Response(200, json={"name": "PageA", "content": "new"})
        )

        client = MeshWikiClient(base_url=_MW_BASE, api_key="tok")
        initial_http_client = client._client

        await client.get_page("PageA")
        assert (
            client._client is initial_http_client
        ), "get_page must not replace the shared AsyncClient"

        await client.create_page("PageA", "new")
        assert (
            client._client is initial_http_client
        ), "create_page must not replace the shared AsyncClient"

        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_new_async_client_created_per_method_call(self) -> None:
        """httpx.AsyncClient constructor is called exactly once across multiple requests."""
        respx.get(f"{_MW_BASE}/api/v1/tasks").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.post(f"{_MW_BASE}/api/v1/tasks/T1/transition").mock(
            return_value=httpx.Response(200, json={})
        )

        with patch(
            "factory.integrations.meshwiki_client.httpx.AsyncClient",
            wraps=httpx.AsyncClient,
        ) as mock_cls:
            client = MeshWikiClient(base_url=_MW_BASE, api_key="tok")
            constructor_calls_after_init = mock_cls.call_count

            await client.list_tasks()
            await client.transition_task("T1", "in_progress")
            await client.close()

        # Constructor must have been called exactly once (at __init__), never again.
        assert constructor_calls_after_init == 1
        assert mock_cls.call_count == 1


class TestGitHubClientSharedClient:
    def test_creates_one_async_client(self) -> None:
        """GitHubClient.__init__ must create exactly one httpx.AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            _ = GitHubClient(token="tok", repo=_GH_REPO)
            assert mock_cls.call_count == 1

    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        """close() must call aclose() on the underlying AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            client = GitHubClient(token="tok", repo=_GH_REPO)
            await client.close()
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_context_manager_calls_aclose_on_exit(self) -> None:
        """Exiting async with must call aclose() on the underlying AsyncClient."""
        with patch("factory.integrations.github_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            async with GitHubClient(token="tok", repo=_GH_REPO):
                pass
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    @respx.mock
    async def test_same_async_client_reused_across_calls(self) -> None:
        """Multiple method calls must use the same underlying AsyncClient instance.

        Verifies that successive API calls (get_pr, create_pr_comment) do not
        each create a fresh AsyncClient — i.e., connection pooling is preserved
        across the lifetime of a single GitHubClient.
        """
        pr_url = f"{_GH_BASE}/repos/{_GH_REPO}/pulls/7"
        comment_url = f"{_GH_BASE}/repos/{_GH_REPO}/issues/7/comments"

        respx.get(pr_url).mock(
            return_value=httpx.Response(
                200, json={"number": 7, "state": "open", "merged": False}
            )
        )
        respx.post(comment_url).mock(
            return_value=httpx.Response(201, json={"id": 1, "body": "hi"})
        )

        client = GitHubClient(token="tok", repo=_GH_REPO)
        initial_http_client = client._client

        await client.get_pr(7)
        assert (
            client._client is initial_http_client
        ), "get_pr must not replace the shared AsyncClient"

        await client.create_pr_comment(7, "hi")
        assert (
            client._client is initial_http_client
        ), "create_pr_comment must not replace the shared AsyncClient"

        await client.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_new_async_client_created_per_method_call(self) -> None:
        """httpx.AsyncClient constructor is called exactly once across multiple requests."""
        pr_url = f"{_GH_BASE}/repos/{_GH_REPO}/pulls/3"
        reviews_url = f"{_GH_BASE}/repos/{_GH_REPO}/pulls/3/reviews"

        respx.get(pr_url).mock(
            return_value=httpx.Response(
                200, json={"number": 3, "state": "open", "merged": False}
            )
        )
        respx.post(reviews_url).mock(
            return_value=httpx.Response(200, json={"id": 9, "state": "APPROVED"})
        )

        with patch(
            "factory.integrations.github_client.httpx.AsyncClient",
            wraps=httpx.AsyncClient,
        ) as mock_cls:
            client = GitHubClient(token="tok", repo=_GH_REPO)
            constructor_calls_after_init = mock_cls.call_count

            await client.get_pr(3)
            await client.approve_pr(3)
            await client.close()

        # Constructor must have been called exactly once (at __init__), never again.
        assert constructor_calls_after_init == 1
        assert mock_cls.call_count == 1
