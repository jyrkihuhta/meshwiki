"""Tests for the MiniMax usage API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.integrations.minimax_client import MiniMaxUsageClient, TokenPlanStatus


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestTokenPlanStatusParsing:
    """MiniMaxUsageClient correctly parses all expected field-name variants."""

    @pytest.mark.asyncio
    async def test_parses_remaining_tokens_field(self) -> None:
        client = MiniMaxUsageClient(api_key="test-key")
        mock_get = AsyncMock(return_value=_mock_response({"remaining_tokens": 5000000}))
        client._client.get = mock_get

        result = await client.get_token_plan_remaining()

        assert result.remaining == 5000000

    @pytest.mark.asyncio
    async def test_parses_remaining_field(self) -> None:
        client = MiniMaxUsageClient(api_key="test-key")
        mock_get = AsyncMock(return_value=_mock_response({"remaining": 3000000}))
        client._client.get = mock_get

        result = await client.get_token_plan_remaining()

        assert result.remaining == 3000000

    @pytest.mark.asyncio
    async def test_parses_tokens_remaining_field(self) -> None:
        client = MiniMaxUsageClient(api_key="test-key")
        mock_get = AsyncMock(return_value=_mock_response({"tokens_remaining": 1000000}))
        client._client.get = mock_get

        result = await client.get_token_plan_remaining()

        assert result.remaining == 1000000

    @pytest.mark.asyncio
    async def test_parses_reset_at_variants(self) -> None:
        client = MiniMaxUsageClient(api_key="test-key")
        mock_get = AsyncMock(
            return_value=_mock_response(
                {"remaining_tokens": 9000000, "reset_time": "2026-04-20T18:00:00Z"}
            )
        )
        client._client.get = mock_get

        result = await client.get_token_plan_remaining()

        assert result.reset_at == "2026-04-20T18:00:00Z"

    @pytest.mark.asyncio
    async def test_returns_none_remaining_on_unknown_schema(self) -> None:
        client = MiniMaxUsageClient(api_key="test-key")
        mock_get = AsyncMock(return_value=_mock_response({"some_other_field": 42}))
        client._client.get = mock_get

        result = await client.get_token_plan_remaining()

        assert result.remaining is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        import httpx

        client = MiniMaxUsageClient(api_key="test-key")
        client._client.get = AsyncMock(
            side_effect=httpx.HTTPError("connection refused")
        )

        result = await client.get_token_plan_remaining()

        assert result.remaining is None
        assert result.limit is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self) -> None:
        client = MiniMaxUsageClient(api_key="")

        result = await client.get_token_plan_remaining()

        assert result.remaining is None

    @pytest.mark.asyncio
    async def test_uses_bearer_auth_header(self) -> None:
        client = MiniMaxUsageClient(api_key="sk_test_abc123")
        mock_get = AsyncMock(return_value=_mock_response({"remaining_tokens": 1}))
        client._client.get = mock_get

        await client.get_token_plan_remaining()

        call_kwargs = mock_get.call_args
        headers = (
            call_kwargs.kwargs.get("headers") or call_kwargs.args[1]
            if len(call_kwargs.args) > 1
            else {}
        )
        # Check via kwargs
        assert "Authorization" in mock_get.call_args.kwargs.get("headers", {})
        assert (
            mock_get.call_args.kwargs["headers"]["Authorization"]
            == "Bearer sk_test_abc123"
        )
