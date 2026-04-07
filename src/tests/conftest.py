"""Shared fixtures for integration tests."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture()
def wiki_app(tmp_path):
    """Create a fresh app instance pointing at a temp directory.

    Reloads config and main modules so the app picks up the temp
    data_dir. Yields the FastAPI app object.
    """
    import importlib

    os.environ["MESHWIKI_DATA_DIR"] = str(tmp_path)

    import meshwiki.config

    importlib.reload(meshwiki.config)
    import meshwiki.main

    importlib.reload(meshwiki.main)

    yield meshwiki.main.app

    os.environ.pop("MESHWIKI_DATA_DIR", None)


@pytest_asyncio.fixture()
async def client(wiki_app):
    """Async HTTP client wired to the app (no lifespan)."""
    transport = ASGITransport(app=wiki_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=True,
    ) as c:
        yield c
