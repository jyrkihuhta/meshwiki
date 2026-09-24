"""Tests for hover card HTMX wiring and wiki link attributes."""

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main
from meshwiki.core.parser import parse_wiki_content


@pytest.fixture(autouse=True)
def _patch_storage(tmp_path):
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
# Wiki link HTMX attributes
# ============================================================


class TestWikiLinkHtmxAttrs:
    def test_wiki_link_has_hx_get(self):
        html = parse_wiki_content("[[TestPage]]", page_exists=lambda x: True)
        assert 'hx-get="/api/pages/TestPage/preview"' in html

    def test_wiki_link_has_hx_trigger(self):
        html = parse_wiki_content("[[TestPage]]", page_exists=lambda x: True)
        assert 'hx-trigger="mouseenter delay:250ms"' in html

    def test_wiki_link_has_hx_target(self):
        html = parse_wiki_content("[[TestPage]]", page_exists=lambda x: True)
        assert 'hx-target="#wiki-hover-card"' in html

    def test_wiki_link_has_hx_swap(self):
        html = parse_wiki_content("[[TestPage]]", page_exists=lambda x: True)
        assert 'hx-swap="outerHTML"' in html

    def test_wiki_link_all_htmx_attrs_present(self):
        html = parse_wiki_content("[[TestPage]]", page_exists=lambda x: True)
        assert 'hx-get="/api/pages/TestPage/preview"' in html
        assert 'hx-trigger="mouseenter delay:250ms"' in html
        assert 'hx-target="#wiki-hover-card"' in html
        assert 'hx-swap="outerHTML"' in html

    def test_wiki_link_with_spaces_has_correct_hx_get(self):
        html = parse_wiki_content("[[My Test Page]]", page_exists=lambda x: True)
        assert 'hx-get="/api/pages/My_Test_Page/preview"' in html

    def test_missing_page_link_has_htmx_attrs(self):
        html = parse_wiki_content("[[MissingPage]]", page_exists=lambda x: False)
        assert 'hx-get="/api/pages/MissingPage/preview"' in html
        assert 'hx-trigger="mouseenter delay:250ms"' in html


# ============================================================
# Page preview API endpoint
# ============================================================


class TestPagePreviewEndpoint:
    @pytest.mark.asyncio
    async def test_preview_endpoint_returns_page_content(self, client):
        await meshwiki.main.storage.save_page("HoverTest", "# Test Content")
        resp = await client.get("/api/pages/HoverTest/preview")
        assert resp.status_code == 200
        assert "# Test Content" in resp.text

    @pytest.mark.asyncio
    async def test_preview_endpoint_missing_page_returns_empty(self, client):
        resp = await client.get("/api/pages/NonExistentPage/preview")
        assert resp.status_code == 200
        assert resp.text == ""

    @pytest.mark.asyncio
    async def test_preview_endpoint_with_frontmatter(self, client):
        content = """---
title: Test Page
---
# Hello World
"""
        await meshwiki.main.storage.save_page("FrontmatterPage", content)
        resp = await client.get("/api/pages/FrontmatterPage/preview")
        assert resp.status_code == 200
        assert "# Hello World" in resp.text
