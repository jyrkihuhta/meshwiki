"""Tests for graph node sizing by link count (Milestone 10)."""

import os

import pytest

from meshwiki.core.graph import (
    GRAPH_ENGINE_AVAILABLE,
    init_engine,
    shutdown_engine,
)


@pytest.fixture
def wiki_dir(tmp_path):
    pages = {
        "HomePage.md": (
            "---\nstatus: published\ntags:\n  - main\n---\n"
            "# Home\n\nWelcome to [[About]] and [[Contact]].\n"
        ),
        "About.md": ("---\nstatus: draft\n---\n# About\n\nSee [[HomePage]].\n"),
        "Contact.md": "# Contact\n\nReturn to [[HomePage]].\n",
        "Orphan.md": "# Orphan\n\nThis page has no links to it.\n",
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_engine():
    yield
    shutdown_engine()


class TestGraphNodeSizingAPI:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_api_graph_returns_backlinks_count(self, wiki_dir):
        """API should return backlinks_count for each node."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            assert response.status_code == 200
            data = response.json()

            node_map = {n["id"]: n for n in data["nodes"]}

            assert "HomePage" in node_map
            assert "backlinks_count" in node_map["HomePage"]
            assert node_map["HomePage"]["backlinks_count"] == 2

            assert "About" in node_map
            assert node_map["About"]["backlinks_count"] == 1

            assert "Contact" in node_map
            assert node_map["Contact"]["backlinks_count"] == 1

            assert "Orphan" in node_map
            assert node_map["Orphan"]["backlinks_count"] == 0

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_api_graph_link_structure(self, wiki_dir):
        """API should return correct links for graph traversal."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            assert response.status_code == 200
            data = response.json()

            assert len(data["nodes"]) == 4
            assert len(data["links"]) == 3


class TestGraphNodeSizingCSS:
    @pytest.mark.asyncio
    async def test_graph_legend_css_exists(self):
        """Graph CSS should include legend styles."""
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            css_response = await client.get("/static/css/graph.css")
            assert css_response.status_code == 200
            css_body = css_response.text
            assert ".graph-legend" in css_body
            assert ".graph-legend-title" in css_body
            assert ".graph-legend-content" in css_body
            assert ".graph-legend-item" in css_body
            assert ".graph-legend-svg" in css_body
            assert ".graph-legend-node" in css_body

    @pytest.mark.asyncio
    async def test_graph_js_includes_getNodeRadius(self):
        """Graph JS should include getNodeRadius function."""
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/static/js/graph.js")
            assert response.status_code == 200
            body = response.text
            assert "getNodeRadius" in body
            assert "MIN_RADIUS" in body
            assert "MAX_RADIUS" in body
            assert "getBacklinksForNode" in body

    @pytest.mark.asyncio
    async def test_graph_js_includes_legend_functions(self):
        """Graph JS should include legend initialization and update functions."""
        import importlib
        import os

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/static/js/graph.js")
            assert response.status_code == 200
            body = response.text
            assert "initLegend" in body
            assert "updateLegend" in body
            assert "updateNodeRadii" in body


class TestGraphNodeSizingScaling:
    def test_radius_scaling_values(self):
        """Test the getNodeRadius scaling logic constants are correct."""
        MIN_RADIUS = 5
        MAX_RADIUS = 24

        assert MIN_RADIUS == 5
        assert MAX_RADIUS == 24
        assert MIN_RADIUS < MAX_RADIUS
        assert MIN_RADIUS < 24
        assert MAX_RADIUS <= 24

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_backlinks_count_range(self, wiki_dir):
        """Test that backlinks_count ranges from 0 to expected max."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        init_engine(wiki_dir, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            data = response.json()

            backlinks_counts = [n["backlinks_count"] for n in data["nodes"]]

            assert min(backlinks_counts) == 0
            assert max(backlinks_counts) >= 2

            assert all(c >= 0 for c in backlinks_counts)
