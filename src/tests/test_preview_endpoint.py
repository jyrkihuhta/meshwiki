"""Integration tests for GET /api/pages/{page_name}/preview."""

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main


@pytest.fixture
async def client(tmp_path):
    """Async HTTP client pointing at a fresh temp wiki."""
    import meshwiki.config

    meshwiki.config.settings.data_dir = tmp_path
    meshwiki.config.settings.factory_enabled = False
    meshwiki.config.settings.graph_watch = False
    meshwiki.config.settings.auth_enabled = False

    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(tmp_path, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


@pytest.mark.asyncio
async def test_preview_existing_page(client):
    """Returns exists:true and a non-empty excerpt for an existing page."""
    await client.post(
        "/page/TestPage",
        data={
            "content": "# Hello\n\nThis is the first paragraph.\n\nAnd this is the second."
        },
    )
    resp = await client.get("/api/pages/TestPage/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["page_name"] == "TestPage"
    assert body["excerpt"]
    assert len(body["excerpt"]) <= 300


@pytest.mark.asyncio
async def test_preview_missing_page(client):
    """Returns exists:false and empty excerpt for a missing page."""
    resp = await client.get("/api/pages/NonExistentPage/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["page_name"] == "NonExistentPage"
    assert body["excerpt"] == ""


@pytest.mark.asyncio
async def test_preview_excerpt_truncated_at_300(client):
    """Excerpt is truncated to 300 characters."""
    long_content = "# Title\n\n" + ("x" * 400)
    await client.post("/page/LongPage", data={"content": long_content})
    resp = await client.get("/api/pages/LongPage/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert len(body["excerpt"]) <= 300
