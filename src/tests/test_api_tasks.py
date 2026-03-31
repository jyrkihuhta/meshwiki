"""Integration tests for the /api/v1/tasks endpoints."""

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.config as cfg
import meshwiki.main

_AUTH = {"Authorization": "Bearer test-key-123"}


@pytest.fixture
def factory_settings(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_api_key="test-key-123",
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def client(factory_settings):
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(factory_settings.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


# ---------------------------------------------------------------------------
# List tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_empty(client):
    resp = await client.get("/api/v1/tasks", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_tasks_returns_only_tasks(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskAlpha",
            "content": "---\ntype: task\nstatus: draft\n---\nA task",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={"name": "NormalPage", "content": "Not a task"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/tasks", headers=_AUTH)
    names = [t["name"] for t in resp.json()]
    assert "TaskAlpha" in names
    assert "NormalPage" not in names


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskDraft",
            "content": "---\ntype: task\nstatus: draft\n---\nDraft",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskPlanned",
            "content": "---\ntype: task\nstatus: planned\n---\nPlanned",
        },
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/tasks?status=draft", headers=_AUTH)
    names = [t["name"] for t in resp.json()]
    assert "TaskDraft" in names
    assert "TaskPlanned" not in names


@pytest.mark.asyncio
async def test_list_tasks_filter_by_assignee(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskAssigned",
            "content": "---\ntype: task\nstatus: draft\nassignee: grinder-1\n---\nAssigned",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskFree",
            "content": "---\ntype: task\nstatus: draft\n---\nUnassigned",
        },
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/tasks?assignee=grinder-1", headers=_AUTH)
    names = [t["name"] for t in resp.json()]
    assert "TaskAssigned" in names
    assert "TaskFree" not in names


# ---------------------------------------------------------------------------
# Transition endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_valid(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskTen",
            "content": "---\ntype: task\nstatus: draft\n---\nTest",
        },
        headers=_AUTH,
    )
    resp = await client.post(
        "/api/v1/tasks/TaskTen/transition",
        json={"status": "planned"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["metadata"]["status"] == "planned"


@pytest.mark.asyncio
async def test_transition_invalid_returns_422(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskEleven",
            "content": "---\ntype: task\nstatus: draft\n---\nTest",
        },
        headers=_AUTH,
    )
    resp = await client.post(
        "/api/v1/tasks/TaskEleven/transition",
        json={"status": "done"},
        headers=_AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_transition_missing_page_returns_404(client):
    resp = await client.post(
        "/api/v1/tasks/NonExistent/transition",
        json={"status": "planned"},
        headers=_AUTH,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transition_with_extra_fields(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "TaskTwelve",
            "content": "---\ntype: task\nstatus: approved\n---\nTest",
        },
        headers=_AUTH,
    )
    resp = await client.post(
        "/api/v1/tasks/TaskTwelve/transition",
        json={
            "status": "in_progress",
            "extra_fields": {"assignee": "grinder-1", "branch": "factory/task-012"},
        },
        headers=_AUTH,
    )
    assert resp.status_code == 200
    metadata = resp.json()["metadata"]
    assert metadata["status"] == "in_progress"
    assert metadata["assignee"] == "grinder-1"
    assert metadata["branch"] == "factory/task-012"
