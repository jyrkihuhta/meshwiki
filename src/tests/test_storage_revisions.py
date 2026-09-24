"""Tests verifying that all FileStorage write paths record revisions."""

import pytest

from meshwiki.core.revision_store import RevisionStore
from meshwiki.core.storage import FileStorage


@pytest.fixture
def rev_store(tmp_path):
    store = RevisionStore(tmp_path / ".revisions.db")
    yield store
    store.close()


@pytest.fixture
def storage(tmp_path, rev_store):
    return FileStorage(tmp_path / "pages", revision_store=rev_store)


@pytest.fixture
def storage_no_revisions(tmp_path):
    """FileStorage without a revision store — existing behaviour unchanged."""
    return FileStorage(tmp_path / "pages")


class TestSavePage:
    @pytest.mark.asyncio
    async def test_first_save_creates_revision(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        assert rev_store.revision_count("TestPage") == 1

    @pytest.mark.asyncio
    async def test_first_save_operation_is_create(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        rev = rev_store.get_revision("TestPage", 1)
        assert rev is not None
        assert rev.operation == "create"

    @pytest.mark.asyncio
    async def test_second_save_operation_is_edit(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        await storage.save_page("TestPage", "# Hello updated")
        rev = rev_store.get_revision("TestPage", 2)
        assert rev is not None
        assert rev.operation == "edit"

    @pytest.mark.asyncio
    async def test_revision_content_includes_frontmatter(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        rev = rev_store.get_revision("TestPage", 1)
        assert rev is not None
        assert "---" in rev.content  # frontmatter present

    @pytest.mark.asyncio
    async def test_no_revision_store_does_not_raise(self, storage_no_revisions):
        page = await storage_no_revisions.save_page("TestPage", "# Hello")
        assert page is not None


class TestDeletePage:
    @pytest.mark.asyncio
    async def test_delete_removes_revision_history(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        await storage.save_page("TestPage", "# Hello v2")
        assert rev_store.revision_count("TestPage") == 2

        await storage.delete_page("TestPage")
        assert rev_store.revision_count("TestPage") == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_page_no_error(self, storage, rev_store):
        result = await storage.delete_page("NonExistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_pages(self, storage, rev_store):
        await storage.save_page("PageA", "content a")
        await storage.save_page("PageB", "content b")
        await storage.delete_page("PageA")
        assert rev_store.revision_count("PageB") == 1


class TestUpdateFrontmatterField:
    @pytest.mark.asyncio
    async def test_frontmatter_update_records_revision(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        await storage.update_frontmatter_field("TestPage", "status", "draft")
        assert rev_store.revision_count("TestPage") == 2

    @pytest.mark.asyncio
    async def test_frontmatter_update_operation(self, storage, rev_store):
        await storage.save_page("TestPage", "# Hello")
        await storage.update_frontmatter_field("TestPage", "status", "draft")
        rev = rev_store.get_revision("TestPage", 2)
        assert rev is not None
        assert rev.operation == "frontmatter_update"
        assert rev.message == "Updated status"

    @pytest.mark.asyncio
    async def test_frontmatter_update_nonexistent_page(self, storage, rev_store):
        result = await storage.update_frontmatter_field("NonExistent", "status", "x")
        assert result is None
        assert rev_store.revision_count("NonExistent") == 0


class TestRenamePage:
    @pytest.mark.asyncio
    async def test_rename_moves_revision_history(self, storage, rev_store):
        await storage.save_page("OldName", "# Old")
        await storage.rename_page("OldName", "NewName")
        assert rev_store.revision_count("OldName") == 0
        assert rev_store.revision_count("NewName") == 1

    @pytest.mark.asyncio
    async def test_rename_nonexistent_page(self, storage, rev_store):
        result = await storage.rename_page("NonExistent", "NewName")
        assert result is None
