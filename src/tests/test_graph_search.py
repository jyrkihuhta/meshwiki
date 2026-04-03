"""Tests for graph search and highlight functionality."""

import os

import pytest


@pytest.fixture
def wiki_dir(tmp_path):
    """Create a temporary wiki directory with test pages."""
    pages = {
        "HomePage.md": (
            "---\nstatus: published\ntags:\n  - main\n---\n"
            "# Home\n\nWelcome to [[About]] and [[Contact]].\n"
        ),
        "About.md": ("---\nstatus: draft\n---\n# About\n\nSee [[HomePage]].\n"),
        "Contact.md": "# Contact\n\nReturn to [[HomePage]].\n",
        "SearchablePage.md": "# SearchablePage\n\nA page for testing search.\n",
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_engine():
    """Ensure engine is shut down after each test."""
    from meshwiki.core.graph import shutdown_engine

    yield
    shutdown_engine()


class TestGraphSearchAPI:
    @pytest.mark.asyncio
    async def test_graph_page_includes_search_css(self):
        """Graph page should include the search CSS file."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            assert "graph.css" in response.text

    @pytest.mark.asyncio
    async def test_graph_page_includes_search_container(self):
        """Graph page should have search container element in JS."""
        import importlib

        os.environ["MESHWIKI_DATA_DIR"] = "/tmp/nonexistent"
        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/graph")
            assert response.status_code == 200
            assert "graph.js" in response.text
            assert "graph-container" in response.text


class TestGraphSearchJS:
    def test_search_highlight_class_exists(self):
        """CSS should define .node.match and .node.dimmed styles."""
        css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        if os.path.exists(css_path):
            css_content = open(css_path).read()
            assert ".node.match" in css_content
            assert ".node.dimmed" in css_content
            assert "graph-search-container" in css_content
            assert "graph-search-input" in css_content

    def test_search_functions_defined(self):
        """JS should define search-related functions."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "initSearchUI" in js_content
        assert "handleSearchInput" in js_content
        assert "handleSearchKeydown" in js_content
        assert "performSearch" in js_content
        assert "highlightMatch" in js_content
        assert "navigateResults" in js_content
        assert "selectResult" in js_content
        assert "panToNode" in js_content
        assert "updateHighlights" in js_content
        assert "clearSearch" in js_content
        assert "searchQuery" in js_content
        assert "matchedNodes" in js_content

    def test_search_initialization_after_render(self):
        """initSearchUI should be called after initial data load."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "initSearchUI();" in js_content

    def test_search_keyboard_navigation(self):
        """JS should handle arrow keys and Enter for navigation."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert 'e.key === "Enter"' in js_content
        assert 'e.key === "Escape"' in js_content
        assert 'e.key === "ArrowDown"' in js_content
        assert 'e.key === "ArrowUp"' in js_content

    def test_pan_to_node_transform(self):
        """JS should implement panToNode with zoom transform."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "d3.zoomIdentity" in js_content
        assert "translate(tx, ty)" in js_content
        assert "svg.transition()" in js_content
        assert ".duration(500)" in js_content

    def test_dimmed_links_have_lower_opacity(self):
        """Non-matching links should have reduced opacity during search."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "stroke-opacity" in js_content
        assert "0.15" in js_content or "0.1" in js_content

    def test_search_clear_button(self):
        """Clear button should reset search state."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "clearSearch" in js_content
        assert "searchClear" in js_content

    def test_search_results_dropdown(self):
        """Search should show dropdown results."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "searchResults" in js_content
        assert "graph-search-results" in js_content
        assert "graph-search-result-item" in js_content

    def test_highlight_match_function(self):
        """highlightMatch should wrap matching text in <mark>."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert "highlightMatch" in js_content
        assert "<mark>" in js_content

    def test_match_and_dimmed_class_toggling(self):
        """updateHighlights should toggle match/dimmed classes."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "js",
            "graph.js",
        )
        js_content = open(js_path).read()
        assert 'classed("match"' in js_content
        assert 'classed("dimmed"' in js_content


class TestGraphSearchTheme:
    def test_search_works_in_dark_mode(self):
        """Search CSS should work with dark theme."""
        css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        if os.path.exists(css_path):
            css_content = open(css_path).read()
            assert '[data-theme="dark"]' in css_content or "dark" in css_content.lower()

    def test_search_input_theme_variable_usage(self):
        """Search should use CSS variables for theming."""
        css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "graph.css",
        )
        if os.path.exists(css_path):
            css_content = open(css_path).read()
            assert "var(--color-" in css_content
