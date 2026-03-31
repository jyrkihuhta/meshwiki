"""Integration tests for factory API key authentication."""

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.config as cfg
import meshwiki.main


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


@pytest.fixture
def disabled_settings(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=False,
        graph_watch=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def disabled_client(disabled_settings):
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(disabled_settings.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


@pytest.fixture
def open_settings(tmp_path):
    """Factory enabled but no API key set — open access."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_api_key="",
        graph_watch=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def open_client(open_settings):
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(open_settings.data_dir, watch=False)
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
async def test_api_503_when_factory_disabled(disabled_client):
    resp = await disabled_client.get("/api/v1/pages")
    assert resp.status_code == 404  # router not mounted when factory disabled


@pytest.mark.asyncio
async def test_api_401_without_key(client):
    resp = await client.get("/api/v1/pages")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_401_with_wrong_key(client):
    resp = await client.get(
        "/api/v1/pages", headers={"Authorization": "Bearer wrong-key"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_200_with_correct_key(client):
    resp = await client.get(
        "/api/v1/pages", headers={"Authorization": "Bearer test-key-123"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_open_when_no_key_configured(open_client):
    resp = await open_client.get("/api/v1/pages")
    assert resp.status_code == 200
