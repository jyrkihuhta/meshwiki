"""Tests for graph node sizing based on backlink count (Milestone 10)."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def wiki_dir(tmp_path):
    pages = {
        "HomePage.md": "# Home\n\nWelcome to [[About]] and [[Contact]].\n",
        "About.md": "# About\n\nSee [[HomePage]].\n",
        "Contact.md": "# Contact\n\nReturn to [[HomePage]].\n",
        "Popular.md": "# Popular\n\nLinks to [[HomePage]], [[About]], and [[Contact]].\n",
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


class TestGraphNodeSizing:
    @pytest.mark.asyncio
    async def test_api_graph_includes_backlinks_count(self, wiki_dir):
        from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE

        if not GRAPH_ENGINE_AVAILABLE:
            pytest.skip("graph_core not installed")

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from meshwiki.core.graph import init_engine, shutdown_engine

        init_engine(wiki_dir, watch=False)
        try:
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=meshwiki.main.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/api/graph")
                assert response.status_code == 200
                data = response.json()

                node_map = {n["id"]: n for n in data["nodes"]}
                assert "HomePage" in node_map
                assert "Popular" in node_map
                assert "backlinks_count" in node_map["HomePage"]
                assert (
                    node_map["Popular"]["backlinks_count"]
                    >= node_map["HomePage"]["backlinks_count"]
                )
        finally:
            shutdown_engine()

    @pytest.mark.asyncio
    async def test_graph_page_includes_graph_js(self):
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
            assert 'id="graph-svg"' in body
            assert 'id="graph-container"' in body

    @pytest.mark.asyncio
    async def test_graph_js_loads_legend(self):
        js_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "js" / "graph.js"
        )
        js_content = js_path.read_text()
        assert "initLegend" in js_content
        assert "graph-legend" in js_content

    def test_graph_css_has_legend_styles(self):
        css_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "css" / "graph.css"
        )
        css_content = css_path.read_text()
        assert ".graph-legend" in css_content
        assert ".graph-legend-header" in css_content
        assert ".graph-legend-body" in css_content
        assert ".graph-legend-item" in css_content
        assert ".graph-legend-toggle" in css_content
        assert ".graph-legend-title" in css_content
        assert ".graph-legend-label" in css_content

    def test_graph_css_has_node_size_legend_items(self):
        css_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "css" / "graph.css"
        )
        css_content = css_path.read_text()
        assert "links" in css_content

    def test_graph_js_exports_sizing_functions(self):
        js_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "js" / "graph.js"
        )
        js_content = js_path.read_text()
        assert "MIN_NODE_RADIUS" in js_content
        assert "MAX_NODE_RADIUS" in js_content
        assert "getNodeRadius" in js_content
        assert "getBacklinkTier" in js_content
        assert "initLegend" in js_content
        assert "graph-legend" in js_content

    def test_graph_js_uses_logarithmic_scaling(self):
        js_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "js" / "graph.js"
        )
        js_content = js_path.read_text()
        assert "Math.log" in js_content

    def test_graph_js_node_radius_uses_backlinks_count(self):
        js_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "js" / "graph.js"
        )
        js_content = js_path.read_text()
        assert "backlinks_count" in js_content
        assert "getNodeRadius(d.backlinks_count" in js_content

    def test_graph_js_legend_shows_size_reference(self):
        js_path = (
            Path(__file__).parent.parent / "meshwiki" / "static" / "js" / "graph.js"
        )
        js_content = js_path.read_text()
        assert "0-2 links" in js_content or "links" in js_content
        assert "graph-legend-label" in js_content
