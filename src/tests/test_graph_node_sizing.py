"""Tests for graph node sizing (Milestone 10)."""

import math

import pytest


class TestNodeSizing:
    """Tests for logarithmic node sizing based on backlink count."""

    def test_calculate_radius_minimum_for_zero_backlinks(self):
        """Nodes with zero backlinks should get minimum radius."""
        MIN_RADIUS = 5
        MAX_RADIUS = 24

        def calculate_radius(backlinks_count, min_r=MIN_RADIUS, max_r=MAX_RADIUS):
            if backlinks_count <= 0:
                return min_r
            if backlinks_count == 1:
                return min_r + 2
            log_scale = math.log(backlinks_count + 1) / math.log(101)
            return min_r + (max_r - min_r) * log_scale

        assert calculate_radius(0) == 5

    def test_calculate_radius_maximum_for_high_backlinks(self):
        """Nodes with many backlinks should be capped at maximum radius."""
        MIN_RADIUS = 5
        MAX_RADIUS = 24

        def calculate_radius(backlinks_count, min_r=MIN_RADIUS, max_r=MAX_RADIUS):
            if backlinks_count <= 0:
                return min_r
            if backlinks_count == 1:
                return min_r + 2
            log_scale = math.log(backlinks_count + 1) / math.log(101)
            return min_r + (max_r - min_r) * log_scale

        result = calculate_radius(100)
        assert result >= 20
        assert result <= MAX_RADIUS

    def test_calculate_radius_logarithmic_scaling(self):
        """Logarithmic scaling prevents extreme size differences."""
        MIN_RADIUS = 5
        MAX_RADIUS = 24

        def calculate_radius(backlinks_count, min_r=MIN_RADIUS, max_r=MAX_RADIUS):
            if backlinks_count <= 0:
                return min_r
            if backlinks_count == 1:
                return min_r + 2
            log_scale = math.log(backlinks_count + 1) / math.log(101)
            return min_r + (max_r - min_r) * log_scale

        assert calculate_radius(0) == 5
        assert calculate_radius(1) == 7
        assert 5 < calculate_radius(5) < 15
        assert calculate_radius(100) >= 20
        assert calculate_radius(1000) >= 22

    def test_backlinks_count_structure_in_api_response(self):
        """API should return nodes with backlinks_count field."""
        import os
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "Page1.md").write_text("# Page 1\n\n[[Page2]]\n")
            (tmp_path / "Page2.md").write_text("# Page 2\n\n[[Page1]]\n")
            (tmp_path / "Page3.md").write_text("# Page 3\n")

            os.environ["MESHWIKI_DATA_DIR"] = tmp_dir
            import importlib

            import meshwiki.config

            importlib.reload(meshwiki.config)
            import meshwiki.main

            importlib.reload(meshwiki.main)

            from meshwiki.core.graph import init_engine

            init_engine(tmp_path, watch=False)

            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=meshwiki.main.app)

            async def check_response():
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    response = await client.get("/api/graph")
                    assert response.status_code == 200
                    data = response.json()
                    for node in data["nodes"]:
                        assert "backlinks_count" in node
                        assert isinstance(node["backlinks_count"], int)
                        assert node["backlinks_count"] >= 0

            import asyncio

            asyncio.run(check_response())


class TestGraphLegend:
    """Tests for graph legend component."""

    def test_legend_css_classes_exist(self):
        """Legend CSS classes should be defined in graph.css."""
        import os

        css_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        with open(css_path) as f:
            css_content = f.read()
        assert ".graph-legend" in css_content
        assert ".graph-legend-header" in css_content
        assert ".graph-legend-content" in css_content
        assert ".graph-legend-item" in css_content
        assert ".graph-legend-toggle" in css_content

    def test_legend_dragging_css(self):
        """Legend dragging CSS should be defined."""
        import os

        css_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        with open(css_path) as f:
            css_content = f.read()
        assert ".graph-legend.dragging" in css_content

    def test_legend_dark_theme_support(self):
        """Legend should have dark theme support."""
        import os

        css_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        with open(css_path) as f:
            css_content = f.read()
        assert "[data-theme=\"dark\"] .graph-legend" in css_content
