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
    """The preview is a hover card (title + plain-text excerpt), not raw source."""

    @pytest.mark.asyncio
    async def test_preview_returns_card_with_title_and_excerpt(self, client):
        await meshwiki.main.storage.save_page(
            "Hover Test", "# Test Content\n\nSome **bold** words here."
        )
        resp = await client.get("/api/pages/Hover_Test/preview")
        assert resp.status_code == 200
        # outerHTML swap target: the card must keep its id
        assert 'id="wiki-hover-card"' in resp.text
        assert "Hover Test" in resp.text
        # Markdown reduced to words, not shown as source
        assert "Test Content Some bold words here." in resp.text
        assert "# Test" not in resp.text and "**" not in resp.text

    @pytest.mark.asyncio
    async def test_preview_missing_page_returns_not_found_card(self, client):
        # Still a card (with the swap id), so the next hover has a target to swap.
        resp = await client.get("/api/pages/NonExistentPage/preview")
        assert resp.status_code == 200
        assert 'id="wiki-hover-card"' in resp.text
        assert "Page not found" in resp.text

    @pytest.mark.asyncio
    async def test_preview_excludes_frontmatter(self, client):
        content = """---
title: Test Page
secret: hunter2
---
# Hello World
"""
        await meshwiki.main.storage.save_page("FrontmatterPage", content)
        resp = await client.get("/api/pages/FrontmatterPage/preview")
        assert resp.status_code == 200
        assert "Hello World" in resp.text
        assert "hunter2" not in resp.text

    @pytest.mark.asyncio
    async def test_preview_does_not_execute_page_markup(self, client):
        """Regression: the endpoint used to return raw page source as HTML, so
        hovering a link to a page containing markup ran it."""
        await meshwiki.main.storage.save_page(
            "Evil",
            '<script>alert(1)</script><img src=x onerror="alert(2)">'
            "[x](javascript:alert(3)) harmless text",
        )
        resp = await client.get("/api/pages/Evil/preview")
        assert resp.status_code == 200
        assert "<script" not in resp.text
        assert "<img" not in resp.text
        assert "onerror" not in resp.text
        assert "harmless text" in resp.text

    @pytest.mark.asyncio
    async def test_preview_escapes_page_name(self, client):
        resp = await client.get(
            "/api/pages/%3Cimg%20src=x%20onerror=alert(1)%3E/preview"
        )
        assert resp.status_code == 200
        assert "<img" not in resp.text

    @pytest.mark.asyncio
    async def test_preview_excerpt_is_truncated(self, client):
        await meshwiki.main.storage.save_page("LongPage", "word " * 500)
        resp = await client.get("/api/pages/LongPage/preview")
        assert "\u2026" in resp.text
        assert len(resp.text) < 600
