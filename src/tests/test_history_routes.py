"""Integration tests for version history HTML routes."""

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main


@pytest.fixture
def wiki_app(tmp_path):
    importlib.reload(meshwiki.main)
    from meshwiki.core.dependencies import set_revision_store, set_storage
    from meshwiki.core.revision_store import RevisionStore
    from meshwiki.core.storage import FileStorage

    store = RevisionStore(tmp_path / ".revisions.db")
    storage = FileStorage(tmp_path / "pages", revision_store=store)
    meshwiki.main.storage = storage
    meshwiki.main._revision_store = store
    set_storage(storage)
    set_revision_store(store)
    yield meshwiki.main.app
    # Clear global references before closing so other tests don't hit a closed connection
    meshwiki.main.storage._revisions = None
    store.close()


@pytest.fixture
async def client(wiki_app):
    async with AsyncClient(
        transport=ASGITransport(app=wiki_app), base_url="http://test"
    ) as c:
        yield c


class TestHistoryList:
    @pytest.mark.asyncio
    async def test_history_page_200(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.get("/page/TestPage/history")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_history_shows_revision(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.get("/page/TestPage/history")
        assert "Revision" in resp.text or "revision" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_history_empty_state(self, client):
        resp = await client.get("/page/NoRevisions/history")
        assert resp.status_code == 200
        assert "No revision history" in resp.text

    @pytest.mark.asyncio
    async def test_history_multiple_revisions(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        await client.post("/page/TestPage", data={"content": "# v2"})
        await client.post("/page/TestPage", data={"content": "# v3"})
        resp = await client.get("/page/TestPage/history")
        assert resp.status_code == 200
        # Should show revision numbers
        assert "1" in resp.text
        assert "3" in resp.text


class TestRevisionView:
    @pytest.mark.asyncio
    async def test_view_revision_200(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello v1"})
        resp = await client.get("/page/TestPage/history/1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_view_revision_contains_content(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello v1"})
        resp = await client.get("/page/TestPage/history/1")
        assert "Hello v1" in resp.text

    @pytest.mark.asyncio
    async def test_view_nonexistent_revision_404(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.get("/page/TestPage/history/999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_view_revision_has_restore_button(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.get("/page/TestPage/history/1")
        assert "Restore" in resp.text


class TestRestorePage:
    @pytest.mark.asyncio
    async def test_restore_redirects(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        await client.post("/page/TestPage", data={"content": "# v2"})
        resp = await client.post("/page/TestPage/restore/1", follow_redirects=False)
        assert resp.status_code == 302
        assert "toast=restored" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_restore_reverts_content(self, client):
        await client.post("/page/TestPage", data={"content": "# Original content"})
        await client.post("/page/TestPage", data={"content": "# Changed content"})
        await client.post("/page/TestPage/restore/1")
        resp = await client.get("/page/TestPage")
        assert "Original content" in resp.text

    @pytest.mark.asyncio
    async def test_restore_creates_new_revision(self, client, wiki_app):
        from meshwiki.core.dependencies import get_revision_store

        await client.post("/page/TestPage", data={"content": "# v1"})
        await client.post("/page/TestPage", data={"content": "# v2"})
        await client.post("/page/TestPage/restore/1")
        store = get_revision_store()
        assert store.revision_count("TestPage") == 3

    @pytest.mark.asyncio
    async def test_restore_nonexistent_revision_404(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.post("/page/TestPage/restore/999")
        assert resp.status_code == 404


class TestDiffView:
    @pytest.mark.asyncio
    async def test_diff_200(self, client):
        await client.post("/page/TestPage", data={"content": "# v1\nline one"})
        await client.post("/page/TestPage", data={"content": "# v2\nline two"})
        resp = await client.get("/page/TestPage/diff/1..2")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_diff_shows_deleted_and_inserted(self, client):
        await client.post("/page/TestPage", data={"content": "old line"})
        await client.post("/page/TestPage", data={"content": "new line"})
        resp = await client.get("/page/TestPage/diff/1..2")
        assert "diff-line--delete" in resp.text
        assert "diff-line--insert" in resp.text

    @pytest.mark.asyncio
    async def test_diff_single_rev_shorthand(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        await client.post("/page/TestPage", data={"content": "# v2"})
        resp = await client.get("/page/TestPage/diff/2")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_diff_invalid_range_400(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        resp = await client.get("/page/TestPage/diff/abc")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_diff_rev_one_shorthand_400(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        resp = await client.get("/page/TestPage/diff/1")
        assert resp.status_code == 400  # no rev 0

    @pytest.mark.asyncio
    async def test_diff_missing_revision_404(self, client):
        await client.post("/page/TestPage", data={"content": "# v1"})
        resp = await client.get("/page/TestPage/diff/1..99")
        assert resp.status_code == 404


class TestHistoryButton:
    @pytest.mark.asyncio
    async def test_view_page_has_history_link(self, client):
        await client.post("/page/TestPage", data={"content": "# Hello"})
        resp = await client.get("/page/TestPage")
        assert "/history" in resp.text
