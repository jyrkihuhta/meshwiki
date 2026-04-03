"""Tests for graph node sizing (Milestone 10)."""

import math
import os

import pytest

from meshwiki.core.graph import GRAPH_ENGINE_AVAILABLE, init_engine, shutdown_engine

MIN_RADIUS = 5
MAX_RADIUS = 24


def calculate_radius(backlinks_count):
    if backlinks_count <= 0:
        return MIN_RADIUS
    if backlinks_count == 1:
        return MIN_RADIUS + 2
    log_scale = min(1.0, math.log(backlinks_count + 1) / math.log(101))
    return MIN_RADIUS + (MAX_RADIUS - MIN_RADIUS) * log_scale


@pytest.fixture(autouse=True)
def cleanup_engine():
    yield
    shutdown_engine()


class TestNodeSizing:
    """Tests for logarithmic node sizing based on backlink count."""

    def test_minimum_radius_for_zero_backlinks(self):
        assert calculate_radius(0) == MIN_RADIUS

    def test_minimum_plus_two_for_one_backlink(self):
        assert calculate_radius(1) == MIN_RADIUS + 2

    def test_logarithmic_scaling(self):
        assert calculate_radius(0) == MIN_RADIUS
        assert calculate_radius(1) == MIN_RADIUS + 2
        assert MIN_RADIUS < calculate_radius(5) < 15
        assert calculate_radius(100) >= 20
        assert calculate_radius(1000) >= 22

    def test_radius_increases_monotonically(self):
        prev = calculate_radius(0)
        for n in [1, 2, 5, 10, 25, 50, 100]:
            r = calculate_radius(n)
            assert r >= prev
            prev = r

    def test_radius_never_exceeds_maximum(self):
        for n in [100, 500, 1000, 10000]:
            assert calculate_radius(n) <= MAX_RADIUS + 1  # allow tiny float margin


class TestNodeSizingAPI:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_api_graph_returns_backlinks_count(self, tmp_path):
        import importlib

        (tmp_path / "Page1.md").write_text("# Page 1\n\n[[Page2]]\n")
        (tmp_path / "Page2.md").write_text("# Page 2\n\n[[Page1]]\n")
        (tmp_path / "Page3.md").write_text("# Page 3\n")

        os.environ["MESHWIKI_DATA_DIR"] = str(tmp_path)
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)
        init_engine(tmp_path, watch=False)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/graph")
            assert response.status_code == 200
            data = response.json()
            for node in data["nodes"]:
                assert "backlinks_count" in node
                assert isinstance(node["backlinks_count"], int)
                assert node["backlinks_count"] >= 0


class TestGraphLegend:
    """Tests for graph legend CSS."""

    def _read_css(self):
        css_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        with open(css_path) as f:
            return f.read()

    def test_legend_css_classes_exist(self):
        css = self._read_css()
        assert ".graph-legend" in css
        assert ".graph-legend-header" in css
        assert ".graph-legend-content" in css
        assert ".graph-legend-item" in css
        assert ".graph-legend-toggle" in css

    def test_legend_dragging_css(self):
        assert ".graph-legend.dragging" in self._read_css()

    def test_legend_dark_theme_support(self):
        assert '[data-theme="dark"] .graph-legend' in self._read_css()
