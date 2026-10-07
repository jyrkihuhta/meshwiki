"""Tests for Milestone 9: Visual Polish & Responsiveness."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main

_STATIC_JS = Path(meshwiki.__file__).parent / "static" / "js"


def _static_js(name: str) -> str:
    """Source of a file in static/js (page behaviour no longer lives inline)."""
    return (_STATIC_JS / name).read_text()


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
# Dark Mode
# ============================================================


class TestDarkMode:
    @pytest.mark.asyncio
    async def test_base_has_theme_toggle(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'id="theme-toggle"' in resp.text

    @pytest.mark.asyncio
    async def test_base_has_theme_script(self, client):
        # Loaded as an external script (the CSP forbids inline script).
        resp = await client.get("/")
        assert '<script src="/static/js/theme-init.js"></script>' in resp.text
        assert "meshwiki-theme" in _static_js("theme-init.js")

    @pytest.mark.asyncio
    async def test_theme_toggle_in_nav(self, client):
        resp = await client.get("/")
        assert "btn-theme" in resp.text


# ============================================================
# Syntax Highlighting
# ============================================================


class TestSyntaxHighlighting:
    @pytest.mark.asyncio
    async def test_base_includes_highlightjs_script(self, client):
        resp = await client.get("/")
        assert "highlight.js" in resp.text or "highlight.min.js" in resp.text

    @pytest.mark.asyncio
    async def test_base_includes_highlightjs_theme(self, client):
        resp = await client.get("/")
        assert 'id="hljs-theme"' in resp.text

    @pytest.mark.asyncio
    async def test_hljs_highlight_all_called(self, client):
        resp = await client.get("/")
        assert '<script src="/static/js/app.js"></script>' in resp.text
        assert "hljs.highlightAll()" in _static_js("app.js")


# ============================================================
# Toast Notifications
# ============================================================


class TestToastNotifications:
    @pytest.mark.asyncio
    async def test_base_has_toast_container(self, client):
        resp = await client.get("/")
        assert 'id="toast-container"' in resp.text

    @pytest.mark.asyncio
    async def test_save_redirect_includes_toast(self, client):
        await meshwiki.main.storage.save_page("TestPage", "content")
        resp = await client.post(
            "/page/TestPage",
            data={"content": "updated"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "toast=saved" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_delete_redirect_includes_toast(self, client):
        await meshwiki.main.storage.save_page("ToDelete", "content")
        resp = await client.post(
            "/page/ToDelete/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "toast=deleted" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_htmx_save_has_toast_trigger(self, client):
        await meshwiki.main.storage.save_page("HtmxPage", "content")
        resp = await client.post(
            "/page/HtmxPage",
            data={"content": "updated via htmx"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "HX-Trigger" in resp.headers
        assert "showToast" in resp.headers["HX-Trigger"]

    @pytest.mark.asyncio
    async def test_toast_js_present(self, client):
        resp = await client.get("/")
        assert '<script src="/static/js/app.js"></script>' in resp.text
        assert "showToast" in _static_js("app.js")


# ============================================================
# Page List with Metadata
# ============================================================


class TestPageListMetadata:
    @pytest.mark.asyncio
    async def test_page_list_has_table(self, client):
        await meshwiki.main.storage.save_page("MyPage", "Hello world content")
        resp = await client.get("/")
        assert "page-list-table" in resp.text

    @pytest.mark.asyncio
    async def test_page_list_shows_word_count(self, client):
        await meshwiki.main.storage.save_page("WordPage", "one two three four five")
        resp = await client.get("/")
        assert resp.status_code == 200
        # Word count of 5 should appear in the table
        assert "page-list-words" in resp.text

    @pytest.mark.asyncio
    async def test_page_list_shows_tags(self, client):
        content = "---\ntags:\n  - python\n  - wiki\n---\n\nTagged content"
        await meshwiki.main.storage.save_page("TagPage", content)
        resp = await client.get("/")
        assert "python" in resp.text
        assert "tag-link" in resp.text

    @pytest.mark.asyncio
    async def test_page_list_shows_modified_date(self, client):
        await meshwiki.main.storage.save_page("DatePage", "some content")
        resp = await client.get("/")
        assert "page-list-date" in resp.text

    @pytest.mark.asyncio
    async def test_page_list_table_headers(self, client):
        await meshwiki.main.storage.save_page("AnyPage", "content")
        resp = await client.get("/")
        assert "<th>Page</th>" in resp.text
        assert "<th>Modified</th>" in resp.text
        assert "<th>Tags</th>" in resp.text
        assert "<th>Words</th>" in resp.text

    @pytest.mark.asyncio
    async def test_empty_page_list_shows_empty_state(self, client):
        resp = await client.get("/")
        assert "empty-state" in resp.text

    @pytest.mark.asyncio
    async def test_word_count_property(self):
        from meshwiki.core.models import Page

        page = Page(name="Test", content="one two three")
        assert page.word_count == 3

    @pytest.mark.asyncio
    async def test_word_count_empty(self):
        from meshwiki.core.models import Page

        page = Page(name="Test", content="")
        assert page.word_count == 0


# ============================================================
# Timeago Filter
# ============================================================


class TestTimeagoFilter:
    def test_none_returns_empty(self):
        from meshwiki.main import timeago_filter

        assert timeago_filter(None) == ""

    def test_recent_returns_just_now(self):
        from datetime import datetime

        from meshwiki.main import timeago_filter

        result = timeago_filter(datetime.now())
        assert result == "just now"

    def test_old_date_returns_formatted(self):
        from datetime import datetime

        from meshwiki.main import timeago_filter

        old = datetime(2020, 1, 1)
        result = timeago_filter(old)
        assert result == "2020-01-01"


# ============================================================
# Responsive
# ============================================================


class TestResponsive:
    @pytest.mark.asyncio
    async def test_header_has_hamburger(self, client):
        resp = await client.get("/")
        assert "nav-toggle" in resp.text

    @pytest.mark.asyncio
    async def test_viewport_meta_tag(self, client):
        resp = await client.get("/")
        assert 'name="viewport"' in resp.text


# ============================================================
# Loading States
# ============================================================


class TestLoadingStates:
    @pytest.mark.asyncio
    async def test_base_has_loading_bar(self, client):
        resp = await client.get("/")
        assert 'id="loading-bar"' in resp.text

    @pytest.mark.asyncio
    async def test_loading_bar_js(self, client):
        resp = await client.get("/")
        assert '<script src="/static/js/app.js"></script>' in resp.text
        js = _static_js("app.js")
        assert "htmx:beforeRequest" in js
        assert "htmx:afterRequest" in js

    @pytest.mark.asyncio
    async def test_editor_has_spinner_indicator(self, client):
        await meshwiki.main.storage.save_page("SpinPage", "content")
        resp = await client.get("/page/SpinPage/edit")
        assert "htmx-indicator" in resp.text
        assert "spinner" in resp.text
