"""Tests for search, tags, TOC, breadcrumbs, and recent pages."""

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main


@pytest.fixture(autouse=True)
def _patch_storage(tmp_path):
    """Use a temporary directory for storage in all tests."""
    original = meshwiki.main.storage.base_path
    meshwiki.main.storage.base_path = tmp_path
    yield
    meshwiki.main.storage.base_path = original


@pytest.fixture
async def client():
    transport = ASGITransport(app=meshwiki.main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ============================================================
# Search page
# ============================================================


class TestSearchRoute:
    @pytest.mark.asyncio
    async def test_search_page_renders(self, client):
        resp = await client.get("/search")
        assert resp.status_code == 200
        assert "Search" in resp.text

    @pytest.mark.asyncio
    async def test_search_by_query(self, client):
        await meshwiki.main.storage.save_page(
            "Python Guide", "Learn Python programming"
        )
        resp = await client.get("/search?q=Python")
        assert resp.status_code == 200
        assert "Python Guide" in resp.text

    @pytest.mark.asyncio
    async def test_search_no_results(self, client):
        resp = await client.get("/search?q=nonexistent")
        assert resp.status_code == 200
        assert "No pages found" in resp.text

    @pytest.mark.asyncio
    async def test_search_by_tag(self, client):
        await meshwiki.main.storage.save_page(
            "TaggedPage", "---\ntags:\n  - python\n---\n\n# Tagged"
        )
        resp = await client.get("/search?tag=python")
        assert resp.status_code == 200
        assert "TaggedPage" in resp.text

    @pytest.mark.asyncio
    async def test_search_htmx_returns_partial(self, client):
        await meshwiki.main.storage.save_page("HtmxPage", "content")
        resp = await client.get(
            "/search?q=Htmx",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "HtmxPage" in resp.text
        # Should be partial (no full page layout)
        assert "<!DOCTYPE html>" not in resp.text

    @pytest.mark.asyncio
    async def test_search_full_page_without_htmx(self, client):
        resp = await client.get("/search?q=test")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text


# ============================================================
# Tags page
# ============================================================


class TestTagsRoute:
    @pytest.mark.asyncio
    async def test_tags_page_renders(self, client):
        resp = await client.get("/tags")
        assert resp.status_code == 200
        assert "Tags" in resp.text

    @pytest.mark.asyncio
    async def test_tags_page_shows_tags(self, client):
        await meshwiki.main.storage.save_page(
            "Page1", "---\ntags:\n  - python\n  - wiki\n---\n\ncontent"
        )
        await meshwiki.main.storage.save_page(
            "Page2", "---\ntags:\n  - python\n---\n\ncontent"
        )
        resp = await client.get("/tags")
        assert resp.status_code == 200
        assert "python" in resp.text
        assert "wiki" in resp.text

    @pytest.mark.asyncio
    async def test_tags_page_shows_counts(self, client):
        await meshwiki.main.storage.save_page(
            "Page1", "---\ntags:\n  - python\n---\n\ncontent"
        )
        await meshwiki.main.storage.save_page(
            "Page2", "---\ntags:\n  - python\n---\n\ncontent"
        )
        resp = await client.get("/tags")
        assert resp.status_code == 200
        # Count of 2 should appear
        assert ">2<" in resp.text

    @pytest.mark.asyncio
    async def test_tags_page_empty(self, client):
        resp = await client.get("/tags")
        assert resp.status_code == 200
        assert "No tags found" in resp.text

    @pytest.mark.asyncio
    async def test_tags_link_to_search(self, client):
        await meshwiki.main.storage.save_page(
            "Page1", "---\ntags:\n  - python\n---\n\ncontent"
        )
        resp = await client.get("/tags")
        assert "/search?tag=python" in resp.text


# ============================================================
# TOC sidebar
# ============================================================


class TestTocSidebar:
    @pytest.mark.asyncio
    async def test_page_with_headings_has_toc(self, client):
        await meshwiki.main.storage.save_page(
            "TocPage", "# Main\n\n## Section 1\n\n## Section 2"
        )
        resp = await client.get("/page/TocPage")
        assert resp.status_code == 200
        assert "toc-sidebar" in resp.text
        assert "Pages" in resp.text

    @pytest.mark.asyncio
    async def test_page_without_headings_no_toc(self, client):
        await meshwiki.main.storage.save_page("NoTocPage", "Just a paragraph of text.")
        resp = await client.get("/page/NoTocPage")
        assert resp.status_code == 200
        assert "toc-sidebar" in resp.text  # Sidebar always shows when page_tree exists

    @pytest.mark.asyncio
    async def test_toc_contains_heading_text(self, client):
        await meshwiki.main.storage.save_page(
            "TocPage", "# Main Title\n\n## Getting Started\n\n## Advanced"
        )
        resp = await client.get("/page/TocPage")
        assert "Getting Started" in resp.text
        assert "Advanced" in resp.text


# ============================================================
# Breadcrumb
# ============================================================


class TestBreadcrumb:
    @pytest.mark.asyncio
    async def test_breadcrumb_on_page_view(self, client):
        await meshwiki.main.storage.save_page("MyPage", "content")
        resp = await client.get("/page/MyPage")
        assert resp.status_code == 200
        assert "breadcrumb" in resp.text
        assert ">Home</a>" in resp.text

    @pytest.mark.asyncio
    async def test_breadcrumb_shows_page_title(self, client):
        await meshwiki.main.storage.save_page("MyPage", "content")
        resp = await client.get("/page/MyPage")
        assert "MyPage" in resp.text


# ============================================================
# Clickable tags
# ============================================================


class TestClickableTags:
    @pytest.mark.asyncio
    async def test_tags_are_links(self, client):
        await meshwiki.main.storage.save_page(
            "TaggedPage", "---\ntags:\n  - python\n---\n\ncontent"
        )
        resp = await client.get("/page/TaggedPage")
        assert resp.status_code == 200
        assert "/search?tag=python" in resp.text
        assert "tag-link" in resp.text


# ============================================================
# Recent pages on home
# ============================================================


class TestRecentPages:
    @pytest.mark.asyncio
    async def test_home_shows_recent_pages(self, client):
        await meshwiki.main.storage.save_page("Page1", "content 1")
        await meshwiki.main.storage.save_page("Page2", "content 2")
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Recently Modified" in resp.text
        assert "Page1" in resp.text
        assert "Page2" in resp.text

    @pytest.mark.asyncio
    async def test_home_empty_no_recent(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        # No recent section when no pages
        assert "Recently Modified" not in resp.text


# ============================================================
# Header search box
# ============================================================


class TestHeaderSearch:
    @pytest.mark.asyncio
    async def test_header_has_search_box(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "header-search" in resp.text
        assert 'id="header-search-input"' in resp.text

    @pytest.mark.asyncio
    async def test_header_has_tags_nav(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "/tags" in resp.text
