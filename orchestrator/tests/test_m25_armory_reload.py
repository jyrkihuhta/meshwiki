"""Tests for M25: finalize_node triggers Molly /armory/reload for armory tool tasks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.nodes.finalize import _notify_molly_reload, finalize_node
from factory.state import FactoryState

from .test_nodes import _make_state, _make_subtask, _mock_client_for_cm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _armory_state(**kwargs) -> FactoryState:
    merged_sub = _make_subtask(status="merged")
    defaults = {"subtasks": [merged_sub], "artifact_type": "tool"}
    defaults.update(kwargs)
    return _make_state(**defaults)


def _wiki_client_mock(status: str = "in_progress") -> AsyncMock:
    inst = _mock_client_for_cm(AsyncMock())
    inst.get_page = AsyncMock(return_value={"metadata": {"status": status}})
    inst.transition_task = AsyncMock(return_value={})
    return inst


# ---------------------------------------------------------------------------
# _notify_molly_reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_molly_reload_skips_when_no_url() -> None:
    """_notify_molly_reload does nothing when molly_url is not configured."""
    mock_settings = MagicMock(molly_url="", molly_api_token="")
    with patch("factory.nodes.finalize.get_settings", return_value=mock_settings):
        with patch("factory.nodes.finalize.httpx.AsyncClient") as mock_client_cls:
            await _notify_molly_reload()
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_notify_molly_reload_posts_to_correct_url() -> None:
    """_notify_molly_reload POSTs to {molly_url}/armory/reload."""
    mock_settings = MagicMock(
        molly_url="http://molly:8780",
        molly_api_token="test-token",
    )
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.nodes.finalize.get_settings", return_value=mock_settings):
        with patch("factory.nodes.finalize.httpx.AsyncClient", return_value=mock_http):
            await _notify_molly_reload()

    mock_http.post.assert_awaited_once()
    call_args = mock_http.post.call_args
    assert call_args[0][0] == "http://molly:8780/armory/reload"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_notify_molly_reload_no_auth_header_when_no_token() -> None:
    """_notify_molly_reload omits Authorization header when molly_api_token is empty."""
    mock_settings = MagicMock(molly_url="http://molly:8780", molly_api_token="")
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.nodes.finalize.get_settings", return_value=mock_settings):
        with patch("factory.nodes.finalize.httpx.AsyncClient", return_value=mock_http):
            await _notify_molly_reload()

    headers = mock_http.post.call_args[1]["headers"]
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_notify_molly_reload_swallows_http_error() -> None:
    """_notify_molly_reload does not raise on network/HTTP errors."""
    mock_settings = MagicMock(molly_url="http://molly:8780", molly_api_token="tok")
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=Exception("connection refused"))
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.nodes.finalize.get_settings", return_value=mock_settings):
        with patch("factory.nodes.finalize.httpx.AsyncClient", return_value=mock_http):
            # Should not raise
            await _notify_molly_reload()


# ---------------------------------------------------------------------------
# finalize_node integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_calls_molly_reload_for_tool_artifact() -> None:
    """finalize_node calls _notify_molly_reload when artifact_type is 'tool'."""
    state = _armory_state()
    client = _wiki_client_mock()

    with patch("factory.nodes.finalize.MeshWikiClient", MagicMock(return_value=client)):
        with patch("factory.nodes.finalize._notify_molly_reload", new_callable=AsyncMock) as mock_reload:
            result = await finalize_node(state)

    assert result["graph_status"] == "completed"
    mock_reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_skips_molly_reload_for_non_tool_artifact() -> None:
    """finalize_node does NOT call _notify_molly_reload for playbook/code artifacts."""
    for artifact in (None, "code", "playbook", "wordlist"):
        merged_sub = _make_subtask(status="merged")
        state = _make_state(subtasks=[merged_sub], artifact_type=artifact)
        client = _wiki_client_mock()

        with patch("factory.nodes.finalize.MeshWikiClient", MagicMock(return_value=client)):
            with patch("factory.nodes.finalize._notify_molly_reload", new_callable=AsyncMock) as mock_reload:
                await finalize_node(state)

        mock_reload.assert_not_awaited(), f"should not reload for artifact_type={artifact!r}"
        mock_reload.reset_mock()


@pytest.mark.asyncio
async def test_finalize_skips_molly_reload_when_subtasks_pending() -> None:
    """finalize_node does not call reload when subtasks guard fires (early return)."""
    pending_sub = _make_subtask(status="in_progress")
    state = _make_state(subtasks=[pending_sub], artifact_type="tool")
    client = _wiki_client_mock()

    with patch("factory.nodes.finalize.MeshWikiClient", MagicMock(return_value=client)):
        with patch("factory.nodes.finalize._notify_molly_reload", new_callable=AsyncMock) as mock_reload:
            result = await finalize_node(state)

    assert result["graph_status"] == "completed"
    mock_reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_skips_molly_reload_when_transition_fails() -> None:
    """finalize_node does not call reload when MeshWiki transition errors out."""
    merged_sub = _make_subtask(status="merged")
    state = _make_state(subtasks=[merged_sub], artifact_type="tool")
    client = _wiki_client_mock()
    client.transition_task = AsyncMock(side_effect=RuntimeError("wiki down"))

    with patch("factory.nodes.finalize.MeshWikiClient", MagicMock(return_value=client)):
        with patch("factory.nodes.finalize._notify_molly_reload", new_callable=AsyncMock) as mock_reload:
            result = await finalize_node(state)

    assert result["graph_status"] == "completed"
    mock_reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_reload_failure_does_not_affect_result() -> None:
    """A Molly reload failure still returns graph_status=completed."""
    state = _armory_state()
    client = _wiki_client_mock()

    with patch("factory.nodes.finalize.MeshWikiClient", MagicMock(return_value=client)):
        with patch(
            "factory.nodes.finalize._notify_molly_reload",
            new_callable=AsyncMock,
            side_effect=Exception("reload error"),
        ):
            result = await finalize_node(state)

    assert result["graph_status"] == "completed"


@pytest.mark.asyncio
async def test_finalize_trailing_slash_stripped_from_molly_url() -> None:
    """_notify_molly_reload strips trailing slash from molly_url."""
    mock_settings = MagicMock(molly_url="http://molly:8780/", molly_api_token="")
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("factory.nodes.finalize.get_settings", return_value=mock_settings):
        with patch("factory.nodes.finalize.httpx.AsyncClient", return_value=mock_http):
            await _notify_molly_reload()

    url = mock_http.post.call_args[0][0]
    assert url == "http://molly:8780/armory/reload"
    assert "//" not in url.split("://")[1]
