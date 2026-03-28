"""Tests for graph engine integration with the FastAPI application."""

from pathlib import Path
from unittest.mock import patch

import pytest

from meshwiki.core.graph import (
    GRAPH_ENGINE_AVAILABLE,
    get_engine,
    init_engine,
    shutdown_engine,
)

# ============================================================
# Fixtures
# ============================================================


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
    }
    for name, content in pages.items():
        (tmp_path / name).write_text(content)
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_engine():
    """Ensure engine is shut down after each test."""
    yield
    shutdown_engine()


# ============================================================
# Graph wrapper module tests
# ============================================================


class TestGraphModule:
    def test_get_engine_returns_none_before_init(self):
        assert get_engine() is None

    def test_init_engine_without_graph_core(self):
        """When graph_core is not importable, init returns None gracefully."""
        with patch("meshwiki.core.graph.GRAPH_ENGINE_AVAILABLE", False):
            result = init_engine(Path("/nonexistent"))
            assert result is None
            assert get_engine() is None

    def test_shutdown_engine_when_none(self):
        """Shutdown is a no-op when no engine is initialized."""
        shutdown_engine()  # Should not raise

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_init_engine_with_files(self, wiki_dir):
        engine = init_engine(wiki_dir, watch=False)
        assert engine is not None
        assert get_engine() is engine
        assert engine.page_count() == 3

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_init_engine_with_watching(self, wiki_dir):
        engine = init_engine(wiki_dir, watch=True)
        assert engine is not None
        assert engine.is_watching()
        shutdown_engine()
        assert get_engine() is None

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_shutdown_stops_watching(self, wiki_dir):
        engine = init_engine(wiki_dir, watch=True)
        assert engine.is_watching()
        shutdown_engine()
        assert get_engine() is None

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_init_engine_empty_dir(self, tmp_path):
        engine = init_engine(tmp_path, watch=False)
        assert engine is not None
        assert engine.page_count() == 0


# ============================================================
# Backlinks route tests
# ============================================================


class TestBacklinksRoute:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_view_page_with_backlinks(self, wiki_dir):
        """Backlinks should appear in the page view response."""
        import os

        from httpx import ASGITransport, AsyncClient

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        # Re-import to pick up new settings
        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        # Manually init engine (ASGITransport doesn't trigger lifespan)
        init_engine(wiki_dir, watch=False)

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/page/HomePage")
            assert response.status_code == 200
            body = response.text
            # About and Contact both link to HomePage
            assert "Pages linking here" in body
            assert "About" in body
            assert "Contact" in body

    @pytest.mark.asyncio
    async def test_view_page_without_engine(self, wiki_dir):
        """Page should render without backlinks when engine is unavailable."""
        import os

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        # Ensure no engine
        with patch("meshwiki.main.get_engine", return_value=None):
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=meshwiki.main.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/page/HomePage")
                assert response.status_code == 200
                assert "Pages linking here" not in response.text


# ============================================================
# Frontmatter display tests
# ============================================================


class TestFrontmatterDisplay:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_view_page_shows_frontmatter(self, wiki_dir):
        """Frontmatter metadata should appear in the page view."""
        import os

        from httpx import ASGITransport, AsyncClient

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        init_engine(wiki_dir, watch=False)

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/page/HomePage")
            assert response.status_code == 200
            body = response.text
            assert "frontmatter-card" in body
            assert "status" in body
            assert "published" in body

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    @pytest.mark.asyncio
    async def test_frontmatter_not_shown_when_empty(self, wiki_dir):
        """Pages without frontmatter should not show the panel."""
        import os

        from httpx import ASGITransport, AsyncClient

        # Create a page with no frontmatter
        (wiki_dir / "Plain.md").write_text("# Plain page\n\nNo metadata here.\n")

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        init_engine(wiki_dir, watch=False)

        transport = ASGITransport(app=meshwiki.main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/page/Plain")
            assert response.status_code == 200
            assert "frontmatter-card" not in response.text

    @pytest.mark.asyncio
    async def test_frontmatter_not_shown_without_engine(self, wiki_dir):
        """Frontmatter panel should not appear when engine is unavailable."""
        import os

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        with patch("meshwiki.main.get_engine", return_value=None):
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=meshwiki.main.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/page/HomePage")
                assert response.status_code == 200
                assert "frontmatter-card" not in response.text


# ============================================================
# MetaTable preprocessor tests
# ============================================================


class TestMetaTableParser:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_parse_metatable_args_equals(self):
        from meshwiki.core.parser import _parse_metatable_args

        filters, columns = _parse_metatable_args("status=draft, ||name||status||")
        assert len(filters) == 1
        assert columns == ["name", "status"]

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_parse_metatable_args_contains(self):
        from meshwiki.core.parser import _parse_metatable_args

        filters, columns = _parse_metatable_args("tags~=rust, ||name||tags||")
        assert len(filters) == 1
        assert columns == ["name", "tags"]

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_parse_metatable_args_matches(self):
        from meshwiki.core.parser import _parse_metatable_args

        filters, columns = _parse_metatable_args(r"version/=v\d+, ||name||")
        assert len(filters) == 1
        assert columns == ["name"]

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_parse_metatable_args_multiple_filters(self):
        from meshwiki.core.parser import _parse_metatable_args

        filters, columns = _parse_metatable_args(
            "status=draft, tags~=rust, ||name||status||tags||"
        )
        assert len(filters) == 2
        assert columns == ["name", "status", "tags"]

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_parse_metatable_args_columns_only(self):
        from meshwiki.core.parser import _parse_metatable_args

        filters, columns = _parse_metatable_args("||name||status||")
        assert len(filters) == 0
        assert columns == ["name", "status"]

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_metatable_renders_html_table(self, wiki_dir):
        from graph_core import Filter

        from meshwiki.core.parser import _render_metatable

        init_engine(wiki_dir, watch=False)
        html = _render_metatable([Filter.equals("status", "draft")], ["name", "status"])
        assert "<table" in html
        assert "About" in html
        assert "draft" in html

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_metatable_no_matches(self, wiki_dir):
        from graph_core import Filter

        from meshwiki.core.parser import _render_metatable

        init_engine(wiki_dir, watch=False)
        html = _render_metatable([Filter.equals("status", "nonexistent")], ["name"])
        assert "No matching pages found" in html

    def test_metatable_without_engine(self):
        from meshwiki.core.parser import _render_metatable

        # No engine initialized
        html = _render_metatable([], ["name"])
        assert "graph engine not available" in html

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_metatable_name_column_links(self, wiki_dir):
        from meshwiki.core.parser import _render_metatable

        init_engine(wiki_dir, watch=False)
        html = _render_metatable([], ["name", "status"])
        # Name column should have wiki-link anchors
        assert 'class="wiki-link"' in html
        assert 'href="/page/' in html

    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_metatable_macro_in_content(self, wiki_dir):
        from meshwiki.core.parser import parse_wiki_content

        init_engine(wiki_dir, watch=False)
        content = "# My Page\n\n<<MetaTable(status=draft, ||name||status||)>>\n"
        html = parse_wiki_content(content)
        assert "<table" in html
        assert "About" in html


# ============================================================
# page_exists_sync tests
# ============================================================


class TestPageExistsSync:
    @pytest.mark.skipif(not GRAPH_ENGINE_AVAILABLE, reason="graph_core not installed")
    def test_page_exists_via_engine(self, wiki_dir):
        init_engine(wiki_dir, watch=False)

        import os

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        assert meshwiki.main.page_exists_sync("HomePage") is True
        assert meshwiki.main.page_exists_sync("NonExistent") is False

    def test_page_exists_filesystem_fallback(self, wiki_dir):
        """Without engine, falls back to filesystem check."""
        import os

        os.environ["MESHWIKI_DATA_DIR"] = str(wiki_dir)

        import importlib

        import meshwiki.config

        importlib.reload(meshwiki.config)
        import meshwiki.main

        importlib.reload(meshwiki.main)

        with patch("meshwiki.main.get_engine", return_value=None):
            assert meshwiki.main.page_exists_sync("HomePage") is True
            assert meshwiki.main.page_exists_sync("NonExistent") is False
