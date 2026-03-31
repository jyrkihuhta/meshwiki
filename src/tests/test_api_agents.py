"""Integration tests for the /api/v1/agents endpoints."""

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
# List agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_empty(client):
    resp = await client.get("/api/v1/agents", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_agents_returns_only_agents(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentPlanner",
            "content": "---\ntype: agent\nagent_role: planner\n---\nA planner agent",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={"name": "NormalPage", "content": "Not an agent"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/agents", headers=_AUTH)
    names = [a["name"] for a in resp.json()]
    assert "AgentPlanner" in names
    assert "NormalPage" not in names


@pytest.mark.asyncio
async def test_list_agents_filter_by_role(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentPlannerTwo",
            "content": "---\ntype: agent\nagent_role: planner\n---\nPlanner",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentGrinder",
            "content": "---\ntype: agent\nagent_role: grinder\n---\nGrinder",
        },
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/agents?agent_role=planner", headers=_AUTH)
    names = [a["name"] for a in resp.json()]
    assert "AgentPlannerTwo" in names
    assert "AgentGrinder" not in names


@pytest.mark.asyncio
async def test_list_agents_filter_by_status(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentActive",
            "content": "---\ntype: agent\nstatus: active\n---\nActive agent",
        },
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentIdle",
            "content": "---\ntype: agent\nstatus: idle\n---\nIdle agent",
        },
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/agents?status=active", headers=_AUTH)
    names = [a["name"] for a in resp.json()]
    assert "AgentActive" in names
    assert "AgentIdle" not in names


# ---------------------------------------------------------------------------
# Get single agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_agent(client):
    await client.post(
        "/api/v1/pages",
        json={
            "name": "AgentSolo",
            "content": "---\ntype: agent\nagent_role: reviewer\n---\nReviewer agent",
        },
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/agents/AgentSolo", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "AgentSolo"
    assert "Reviewer agent" in data["content"]


@pytest.mark.asyncio
async def test_get_nonexistent_agent_returns_404(client):
    resp = await client.get("/api/v1/agents/GhostAgent", headers=_AUTH)
    assert resp.status_code == 404
