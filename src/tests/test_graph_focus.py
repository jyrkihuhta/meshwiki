"""Tests for graph focus mode (Milestone 10)."""

import pytest

from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE


@pytest.fixture
def wiki_dir(tmp_path):
    pages = {
        "HomePage.md": (
            "---\nstatus: published\ntags:\n  - main\n---\n"
            "# Home\n\nWelcome to [[About]] and [[Contact]].\n"
        ),
        "About.md": ("---\nstatus: draft\n---\n# About\n\nSee [[HomePage]].\n"),
        "Contact.md": "# Contact\n\nReturn to [[HomePage]].\n",
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_engine():
    from meshwiki.core.graph import shutdown_engine

    yield
    shutdown_engine()


class TestGraphFocusMode:
    @pytest.mark.asyncio
    async def test_graph_page_has_focus_mode_css(self):
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            body = response.text
            assert "graph.css" in body

    @pytest.mark.asyncio
    async def test_graph_page_has_graph_js(self):
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            body = response.text
            assert "graph.js" in body

    @pytest.mark.asyncio
    async def test_graph_page_has_svg_container(self):
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            body = response.text
            assert 'id="graph-svg"' in body
            assert 'id="graph-container"' in body

    @pytest.mark.asyncio
    async def test_graph_page_has_toolbar_elements(self):
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            body = response.text
            assert 'id="graph-stats"' in body
            assert 'id="ws-status"' in body
            assert 'class="graph-toolbar"' in body

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_graph_api_returns_nodes_and_links(self, wiki_dir):
        import os

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from meshwiki.core.graph import init_engine

        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            assert response.status_code == 200
            data = response.json()
            assert "nodes" in data
            assert "links" in data
            node_ids = [n["id"] for n in data["nodes"]]
            assert "HomePage" in node_ids
            assert "About" in node_ids
            assert "Contact" in node_ids
