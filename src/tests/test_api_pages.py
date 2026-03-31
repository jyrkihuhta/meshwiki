"""Integration tests for the /api/v1/pages endpoints."""

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
# List pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pages_empty(client):
    resp = await client.get("/api/v1/pages", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_pages_returns_created_pages(client):
    await client.post(
        "/api/v1/pages", json={"name": "TestPage", "content": "Hello"}, headers=_AUTH
    )
    resp = await client.get("/api/v1/pages", headers=_AUTH)
    names = [p["name"] for p in resp.json()]
    assert "TestPage" in names


@pytest.mark.asyncio
async def test_list_pages_filter_by_type(client):
    await client.post(
        "/api/v1/pages",
        json={"name": "Task_001", "content": "---\ntype: task\n---\nA task"},
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={"name": "NormalPage", "content": "No type frontmatter"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/pages?type=task", headers=_AUTH)
    names = [p["name"] for p in resp.json()]
    assert "Task 001" in names  # storage normalizes underscores to spaces
    assert "NormalPage" not in names


@pytest.mark.asyncio
async def test_list_pages_filter_by_tag(client):
    await client.post(
        "/api/v1/pages",
        json={"name": "Tagged", "content": "---\ntags:\n  - alpha\n---\nBody"},
        headers=_AUTH,
    )
    await client.post(
        "/api/v1/pages",
        json={"name": "Untagged", "content": "No tags"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/pages?tag=alpha", headers=_AUTH)
    names = [p["name"] for p in resp.json()]
    assert "Tagged" in names
    assert "Untagged" not in names


# ---------------------------------------------------------------------------
# Get page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_page(client):
    await client.post(
        "/api/v1/pages",
        json={"name": "MyPage", "content": "Hello world"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/pages/MyPage", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "MyPage"
    assert "Hello world" in data["content"]


@pytest.mark.asyncio
async def test_get_nonexistent_page_returns_404(client):
    resp = await client.get("/api/v1/pages/DoesNotExist", headers=_AUTH)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_returns_raw_content_with_frontmatter(client):
    content = "---\ntitle: My Title\n---\nBody text"
    await client.post(
        "/api/v1/pages", json={"name": "FMPage", "content": content}, headers=_AUTH
    )
    resp = await client.get("/api/v1/pages/FMPage", headers=_AUTH)
    assert "My Title" in resp.json()["content"]


# ---------------------------------------------------------------------------
# Create page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_page_returns_201(client):
    resp = await client.post(
        "/api/v1/pages",
        json={"name": "NewPage", "content": "Created content"},
        headers=_AUTH,
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "NewPage"


@pytest.mark.asyncio
async def test_create_page_persists(client):
    await client.post(
        "/api/v1/pages",
        json={"name": "Persisted", "content": "Stored content"},
        headers=_AUTH,
    )
    resp = await client.get("/api/v1/pages/Persisted", headers=_AUTH)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Update page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_existing_page(client):
    await client.post(
        "/api/v1/pages", json={"name": "UpdateMe", "content": "Old"}, headers=_AUTH
    )
    resp = await client.put(
        "/api/v1/pages/UpdateMe",
        json={"name": "UpdateMe", "content": "New"},
        headers=_AUTH,
    )
    assert resp.status_code == 200
    assert "New" in resp.json()["content"]


# ---------------------------------------------------------------------------
# Delete page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_existing_page_returns_204(client):
    await client.post(
        "/api/v1/pages", json={"name": "DeleteMe", "content": "Bye"}, headers=_AUTH
    )
    resp = await client.delete("/api/v1/pages/DeleteMe", headers=_AUTH)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_nonexistent_page_returns_404(client):
    resp = await client.delete("/api/v1/pages/Ghost", headers=_AUTH)
    assert resp.status_code == 404
