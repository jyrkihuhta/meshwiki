"""Tests for the MeshWiki async HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

from factory.integrations.meshwiki_client import MeshWikiClient

BASE_URL = "http://testserver"


@pytest.fixture
def client() -> MeshWikiClient:
    """Return a client pointed at the test base URL."""
    return MeshWikiClient(base_url=BASE_URL, api_key="test-key")


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_page_found(client: MeshWikiClient) -> None:
    """get_page returns the page dict on 200."""
    page_data = {"name": "Task_0001", "content": "# Task", "metadata": {}}
    respx.get(f"{BASE_URL}/api/v1/pages/Task_0001").mock(
        return_value=httpx.Response(200, json=page_data)
    )

    result = await client.get_page("Task_0001")
    assert result == page_data


@pytest.mark.asyncio
@respx.mock
async def test_get_page_not_found(client: MeshWikiClient) -> None:
    """get_page returns None on 404."""
    respx.get(f"{BASE_URL}/api/v1/pages/Missing").mock(return_value=httpx.Response(404))

    result = await client.get_page("Missing")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_get_page_sends_api_key(client: MeshWikiClient) -> None:
    """get_page sends the Authorization: Bearer header."""
    page_data = {"name": "Task_0001", "content": "", "metadata": {}}
    route = respx.get(f"{BASE_URL}/api/v1/pages/Task_0001").mock(
        return_value=httpx.Response(200, json=page_data)
    )

    await client.get_page("Task_0001")
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer test-key"


# ---------------------------------------------------------------------------
# transition_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_transition_task(client: MeshWikiClient) -> None:
    """transition_task POSTs the correct payload and returns the response."""
    updated = {
        "name": "Task_0001",
        "content": "",
        "metadata": {"status": "in_progress"},
    }
    route = respx.post(f"{BASE_URL}/api/v1/tasks/Task_0001/transition").mock(
        return_value=httpx.Response(200, json=updated)
    )

    result = await client.transition_task("Task_0001", "in_progress")
    assert result == updated
    body = route.calls[0].request.read()
    import json

    payload = json.loads(body)
    assert payload["status"] == "in_progress"


@pytest.mark.asyncio
@respx.mock
async def test_transition_task_with_extra_fields(client: MeshWikiClient) -> None:
    """transition_task merges extra_fields into the request body."""
    respx.post(f"{BASE_URL}/api/v1/tasks/Task_0001/transition").mock(
        return_value=httpx.Response(200, json={})
    )

    await client.transition_task(
        "Task_0001", "review", extra_fields={"pr_url": "https://github.com/x/y/pull/1"}
    )
    route = respx.calls.last
    import json

    payload = json.loads(route.request.read())
    assert payload["pr_url"] == "https://github.com/x/y/pull/1"


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_tasks_all(client: MeshWikiClient) -> None:
    """list_tasks with no filter returns all tasks."""
    tasks = [{"name": "Task_0001"}, {"name": "Task_0002"}]
    respx.get(f"{BASE_URL}/api/v1/tasks").mock(
        return_value=httpx.Response(200, json=tasks)
    )

    result = await client.list_tasks()
    assert result == tasks


@pytest.mark.asyncio
@respx.mock
async def test_list_tasks_filtered_by_status(client: MeshWikiClient) -> None:
    """list_tasks passes the status query parameter."""
    tasks = [{"name": "Task_0001"}]
    route = respx.get(f"{BASE_URL}/api/v1/tasks").mock(
        return_value=httpx.Response(200, json=tasks)
    )

    result = await client.list_tasks(status="in_progress")
    assert result == tasks
    assert "status=in_progress" in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# update_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_patches_frontmatter(client: MeshWikiClient) -> None:
    """update_metadata reads the page, patches frontmatter, writes back."""
    current = (
        "---\n"
        "status: in_progress\n"
        "assignee: factory\n"
        "---\n"
        "Body text remains.\n"
    )
    respx.get(f"{BASE_URL}/api/v1/pages/Task_HB").mock(
        return_value=httpx.Response(
            200, json={"name": "Task_HB", "content": current}
        )
    )
    respx.put(f"{BASE_URL}/api/v1/pages/Task_HB").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.update_metadata(
        "Task_HB",
        {"worker_id": "abc-123", "last_heartbeat": "2026-05-15T10:00:00Z"},
    )
    last = respx.calls.last
    import json

    payload = json.loads(last.request.read())
    assert payload["name"] == "Task_HB"
    body = payload["content"]
    assert "worker_id: abc-123" in body
    assert "last_heartbeat:" in body
    # Pre-existing fields preserved
    assert "status: in_progress" in body
    assert "assignee: factory" in body
    # Body content preserved
    assert "Body text remains." in body


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_missing_page(client: MeshWikiClient) -> None:
    """update_metadata raises ValueError when the page doesn't exist."""
    respx.get(f"{BASE_URL}/api/v1/pages/Missing").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(ValueError, match="Page not found"):
        await client.update_metadata("Missing", {"worker_id": "x"})


@pytest.mark.asyncio
@respx.mock
async def test_update_metadata_skips_write_when_unchanged(
    client: MeshWikiClient,
) -> None:
    """If fields already match, no POST is issued (saves a round-trip)."""
    current = "---\nworker_id: abc-123\n---\n"
    respx.get(f"{BASE_URL}/api/v1/pages/T").mock(
        return_value=httpx.Response(200, json={"name": "T", "content": current})
    )
    # No POST mock — would fail if a write was attempted
    await client.update_metadata("T", {"worker_id": "abc-123"})
