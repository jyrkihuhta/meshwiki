"""Tests for editor experience: preview endpoint, autocomplete, editor template."""

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
# Preview endpoint
# ============================================================


class TestPreviewEndpoint:
    @pytest.mark.asyncio
    async def test_preview_returns_html(self, client):
        resp = await client.post("/api/preview", data={"content": "# Hello"})
        assert resp.status_code == 200
        assert "<h1" in resp.text
        assert "Hello" in resp.text

    @pytest.mark.asyncio
    async def test_preview_empty_content(self, client):
        resp = await client.post("/api/preview", data={"content": ""})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preview_with_wiki_link(self, client):
        resp = await client.post("/api/preview", data={"content": "See [[TestPage]]"})
        assert resp.status_code == 200
        assert "wiki-link" in resp.text

    @pytest.mark.asyncio
    async def test_preview_with_bold(self, client):
        resp = await client.post("/api/preview", data={"content": "**bold text**"})
        assert resp.status_code == 200
        assert "<strong>bold text</strong>" in resp.text

    @pytest.mark.asyncio
    async def test_preview_with_strikethrough(self, client):
        resp = await client.post("/api/preview", data={"content": "~~deleted~~"})
        assert resp.status_code == 200
        assert "<del>deleted</del>" in resp.text


# ============================================================
# Autocomplete endpoint
# ============================================================


class TestAutocompleteEndpoint:
    @pytest.mark.asyncio
    async def test_autocomplete_empty_query(self, client):
        resp = await client.get("/api/autocomplete?q=")
        assert resp.status_code == 200
        assert resp.text == ""

    @pytest.mark.asyncio
    async def test_autocomplete_no_match(self, client):
        await meshwiki.main.storage.save_page("Alpha", "content")
        resp = await client.get("/api/autocomplete?q=zzz")
        assert resp.status_code == 200
        assert resp.text == ""

    @pytest.mark.asyncio
    async def test_autocomplete_matches(self, client):
        await meshwiki.main.storage.save_page("Python Guide", "content")
        await meshwiki.main.storage.save_page("Python Tips", "content")
        await meshwiki.main.storage.save_page("Rust Guide", "content")
        resp = await client.get("/api/autocomplete?q=Python")
        assert resp.status_code == 200
        assert "Python Guide" in resp.text
        assert "Python Tips" in resp.text
        assert "Rust Guide" not in resp.text

    @pytest.mark.asyncio
    async def test_autocomplete_case_insensitive(self, client):
        await meshwiki.main.storage.save_page("TestPage", "content")
        resp = await client.get("/api/autocomplete?q=test")
        assert resp.status_code == 200
        assert "TestPage" in resp.text

    @pytest.mark.asyncio
    async def test_autocomplete_max_10(self, client):
        for i in range(15):
            await meshwiki.main.storage.save_page(f"Page{i:02d}", "content")
        resp = await client.get("/api/autocomplete?q=Page")
        assert resp.status_code == 200
        assert resp.text.count("autocomplete-item") <= 10

    @pytest.mark.asyncio
    async def test_autocomplete_returns_html_list(self, client):
        await meshwiki.main.storage.save_page("WikiPage", "content")
        resp = await client.get("/api/autocomplete?q=Wiki")
        assert "autocomplete-list" in resp.text
        assert "autocomplete-item" in resp.text
        assert 'data-value="WikiPage"' in resp.text


# ============================================================
# Editor template
# ============================================================


class TestEditorTemplate:
    @pytest.mark.asyncio
    async def test_edit_page_has_toolbar(self, client):
        await meshwiki.main.storage.save_page("EditMe", "# Content")
        resp = await client.get("/page/EditMe/edit")
        assert resp.status_code == 200
        assert "editor-toolbar" in resp.text

    @pytest.mark.asyncio
    async def test_edit_page_has_split_pane(self, client):
        await meshwiki.main.storage.save_page("EditMe", "# Content")
        resp = await client.get("/page/EditMe/edit")
        assert "editor-split" in resp.text
        assert "preview-pane" in resp.text

    @pytest.mark.asyncio
    async def test_edit_page_has_preview_toggle(self, client):
        await meshwiki.main.storage.save_page("EditMe", "# Content")
        resp = await client.get("/page/EditMe/edit")
        assert "toggle-preview" in resp.text
        assert "preview-pane" in resp.text

    @pytest.mark.asyncio
    async def test_edit_page_loads_editor_js(self, client):
        await meshwiki.main.storage.save_page("EditMe", "# Content")
        resp = await client.get("/page/EditMe/edit")
        assert "editor.js" in resp.text

    @pytest.mark.asyncio
    async def test_new_page_edit_has_toolbar(self, client):
        resp = await client.get("/page/NewPage/edit")
        assert resp.status_code == 200
        assert "editor-toolbar" in resp.text
        assert "Create" in resp.text

    @pytest.mark.asyncio
    async def test_edit_has_toolbar_buttons(self, client):
        resp = await client.get("/page/TestPage/edit")
        assert 'data-action="bold"' in resp.text
        assert 'data-action="italic"' in resp.text
        assert 'data-action="link"' in resp.text
        assert 'data-action="wikilink"' in resp.text

    @pytest.mark.asyncio
    async def test_edit_shows_frontmatter(self, client):
        """Edit page should show raw content including frontmatter."""
        content = "---\ntitle: My Page\ntags:\n  - test\n---\n\n# Content"
        await meshwiki.main.storage.save_page("FMPage", content)
        resp = await client.get("/page/FMPage/edit")
        assert resp.status_code == 200
        assert "title: My Page" in resp.text
        assert "# Content" in resp.text

    @pytest.mark.asyncio
    async def test_edit_with_template_prepopulates_content(self, client):
        """GET /page/NewPage/edit?template=TemplateName pre-populates with template content."""
        template_content = "---\ntitle: Task Template\ntags:\n  - task\n---\n\n# Task\n\nDescription here."
        await meshwiki.main.storage.save_page("TaskTemplate", template_content)

        resp = await client.get("/page/NewPage/edit?template=TaskTemplate")
        assert resp.status_code == 200
        assert "# Task" in resp.text
        assert "Description here." in resp.text
        assert "title: Task Template" not in resp.text

    @pytest.mark.asyncio
    async def test_edit_with_nonexistent_template_opens_empty(self, client):
        """Template not found → editor opens empty (no error)."""
        resp = await client.get("/page/NewPage/edit?template=NonexistentTemplate")
        assert resp.status_code == 200
        assert "<textarea" in resp.text

    @pytest.mark.asyncio
    async def test_edit_with_existing_page_ignores_template(self, client):
        """When page exists, template param is ignored."""
        await meshwiki.main.storage.save_page("ExistingPage", "# Existing content")
        template_content = "---\ntitle: Template\n---\n\n# Template content"
        await meshwiki.main.storage.save_page("SomeTemplate", template_content)

        resp = await client.get("/page/ExistingPage/edit?template=SomeTemplate")
        assert resp.status_code == 200
        assert "# Existing content" in resp.text
        assert "# Template content" not in resp.text
