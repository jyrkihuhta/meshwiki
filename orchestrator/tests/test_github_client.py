"""Tests for GitHubClient and merge_check_node."""

from __future__ import annotations

import httpx
import pytest
import respx

from factory.integrations.github_client import GitHubClient, _extract_pr_number

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO = "owner/testrepo"
BASE = "https://api.github.com"


def _make_client() -> GitHubClient:
    return GitHubClient(token="test-token", repo=REPO)


# ---------------------------------------------------------------------------
# _extract_pr_number
# ---------------------------------------------------------------------------


def test_extract_pr_number_standard_url() -> None:
    url = "https://github.com/owner/repo/pull/42"
    assert _extract_pr_number(url) == 42


def test_extract_pr_number_trailing_slash() -> None:
    url = "https://github.com/owner/repo/pull/7/"
    assert _extract_pr_number(url) == 7


def test_extract_pr_number_invalid_url() -> None:
    assert _extract_pr_number("https://github.com/owner/repo/issues/5") is None


def test_extract_pr_number_empty_string() -> None:
    assert _extract_pr_number("") is None


# ---------------------------------------------------------------------------
# GitHubClient.get_pr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_success() -> None:
    """get_pr returns the parsed JSON PR object on 200."""
    pr_payload = {"number": 10, "state": "open", "merged": False, "title": "Fix bug"}
    respx.get(f"{BASE}/repos/{REPO}/pulls/10").mock(
        return_value=httpx.Response(200, json=pr_payload)
    )

    client = _make_client()
    result = await client.get_pr(10)

    assert result["number"] == 10
    assert result["state"] == "open"
    assert result["merged"] is False


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_not_found() -> None:
    """get_pr raises HTTPStatusError on 404."""
    respx.get(f"{BASE}/repos/{REPO}/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_pr(999)


# ---------------------------------------------------------------------------
# GitHubClient.get_pr_diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_diff_success() -> None:
    """get_pr_diff returns the diff text on 200."""
    diff_text = "diff --git a/foo.py b/foo.py\n+++ b/foo.py\n+new line\n"
    respx.get(f"{BASE}/repos/{REPO}/pulls/5").mock(
        return_value=httpx.Response(200, text=diff_text)
    )

    client = _make_client()
    result = await client.get_pr_diff(5)

    assert result == diff_text


@pytest.mark.asyncio
@respx.mock
async def test_get_pr_diff_error() -> None:
    """get_pr_diff raises HTTPStatusError on 422."""
    respx.get(f"{BASE}/repos/{REPO}/pulls/5").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_pr_diff(5)


# ---------------------------------------------------------------------------
# GitHubClient.create_pr_comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_create_pr_comment_success() -> None:
    """create_pr_comment posts to the issues comments endpoint and returns the response."""
    comment_payload = {"id": 1, "body": "LGTM!"}
    respx.post(f"{BASE}/repos/{REPO}/issues/10/comments").mock(
        return_value=httpx.Response(201, json=comment_payload)
    )

    client = _make_client()
    result = await client.create_pr_comment(10, "LGTM!")

    assert result["id"] == 1
    assert result["body"] == "LGTM!"


@pytest.mark.asyncio
@respx.mock
async def test_create_pr_comment_error() -> None:
    """create_pr_comment raises HTTPStatusError on 403."""
    respx.post(f"{BASE}/repos/{REPO}/issues/10/comments").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_pr_comment(10, "hello")


# ---------------------------------------------------------------------------
# GitHubClient.request_changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_request_changes_success() -> None:
    """request_changes posts a REQUEST_CHANGES review and returns the response."""
    review_payload = {"id": 55, "state": "CHANGES_REQUESTED", "body": "Fix tests"}
    respx.post(f"{BASE}/repos/{REPO}/pulls/10/reviews").mock(
        return_value=httpx.Response(200, json=review_payload)
    )

    client = _make_client()
    result = await client.request_changes(10, "Fix tests")

    assert result["state"] == "CHANGES_REQUESTED"


@pytest.mark.asyncio
@respx.mock
async def test_request_changes_error() -> None:
    """request_changes raises HTTPStatusError on 422."""
    respx.post(f"{BASE}/repos/{REPO}/pulls/10/reviews").mock(
        return_value=httpx.Response(422, json={"message": "Unprocessable"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.request_changes(10, "bad")


# ---------------------------------------------------------------------------
# GitHubClient.approve_pr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_approve_pr_success() -> None:
    """approve_pr posts an APPROVE review and returns the response."""
    review_payload = {"id": 56, "state": "APPROVED", "body": ""}
    respx.post(f"{BASE}/repos/{REPO}/pulls/10/reviews").mock(
        return_value=httpx.Response(200, json=review_payload)
    )

    client = _make_client()
    result = await client.approve_pr(10)

    assert result["state"] == "APPROVED"


@pytest.mark.asyncio
@respx.mock
async def test_approve_pr_with_body() -> None:
    """approve_pr passes the body in the request payload."""
    review_payload = {"id": 57, "state": "APPROVED", "body": "Nice work"}

    route = respx.post(f"{BASE}/repos/{REPO}/pulls/10/reviews").mock(
        return_value=httpx.Response(200, json=review_payload)
    )

    client = _make_client()
    await client.approve_pr(10, body="Nice work")

    # Verify payload contains the body
    request = route.calls.last.request
    import json

    payload = json.loads(request.content)
    assert payload["event"] == "APPROVE"
    assert payload["body"] == "Nice work"


@pytest.mark.asyncio
@respx.mock
async def test_approve_pr_error() -> None:
    """approve_pr raises HTTPStatusError on 404."""
    respx.post(f"{BASE}/repos/{REPO}/pulls/99/reviews").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.approve_pr(99)


# ---------------------------------------------------------------------------
# GitHubClient.close_pr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_close_pr_success() -> None:
    """close_pr patches the PR state to 'closed' and returns the updated object."""
    pr_payload = {"number": 10, "state": "closed", "merged": False}
    respx.patch(f"{BASE}/repos/{REPO}/pulls/10").mock(
        return_value=httpx.Response(200, json=pr_payload)
    )

    client = _make_client()
    result = await client.close_pr(10)

    assert result["state"] == "closed"
    assert result["merged"] is False


@pytest.mark.asyncio
@respx.mock
async def test_close_pr_error() -> None:
    """close_pr raises HTTPStatusError on 404."""
    respx.patch(f"{BASE}/repos/{REPO}/pulls/999").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    client = _make_client()
    with pytest.raises(httpx.HTTPStatusError):
        await client.close_pr(999)


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_auth_header_sent() -> None:
    """GitHubClient sends the Bearer token in Authorization header."""
    pr_payload = {"number": 1, "state": "open", "merged": False}
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/1").mock(
        return_value=httpx.Response(200, json=pr_payload)
    )

    client = _make_client()
    await client.get_pr(1)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


@pytest.mark.asyncio
@respx.mock
async def test_no_auth_header_when_token_empty() -> None:
    """GitHubClient omits Authorization header when token is empty."""
    pr_payload = {"number": 1, "state": "open", "merged": False}
    route = respx.get(f"{BASE}/repos/{REPO}/pulls/1").mock(
        return_value=httpx.Response(200, json=pr_payload)
    )

    client = GitHubClient(token="", repo=REPO)
    await client.get_pr(1)

    request = route.calls.last.request
    assert "Authorization" not in request.headers
