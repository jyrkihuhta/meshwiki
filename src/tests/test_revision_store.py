"""Unit tests for the RevisionStore."""

import pytest

from meshwiki.core.revision_store import RevisionStore


@pytest.fixture
def store(tmp_path):
    s = RevisionStore(tmp_path / ".revisions.db")
    yield s
    s.close()


class TestRecord:
    def test_first_revision_is_one(self, store):
        rev = store.record("HomePage", "# Hello", operation="create")
        assert rev == 1

    def test_second_revision_increments(self, store):
        store.record("HomePage", "# Hello", operation="create")
        rev = store.record("HomePage", "# Hello updated", operation="edit")
        assert rev == 2

    def test_different_pages_have_independent_numbering(self, store):
        store.record("PageA", "content a", operation="create")
        store.record("PageA", "content a v2", operation="edit")
        rev = store.record("PageB", "content b", operation="create")
        assert rev == 1

    def test_message_stored(self, store):
        store.record("Page", "content", message="Initial version")
        rev = store.get_revision("Page", 1)
        assert rev is not None
        assert rev.message == "Initial version"

    def test_operation_stored(self, store):
        store.record("Page", "content", operation="frontmatter_update")
        rev = store.get_revision("Page", 1)
        assert rev is not None
        assert rev.operation == "frontmatter_update"

    def test_author_stored(self, store):
        store.record("Page", "content", author="alice")
        rev = store.get_revision("Page", 1)
        assert rev is not None
        assert rev.author == "alice"


class TestGetRevision:
    def test_get_existing_revision(self, store):
        store.record("Page", "v1 content", operation="create")
        store.record("Page", "v2 content", operation="edit")
        rev = store.get_revision("Page", 1)
        assert rev is not None
        assert rev.content == "v1 content"
        assert rev.revision == 1

    def test_get_nonexistent_revision(self, store):
        assert store.get_revision("Page", 999) is None

    def test_get_revision_for_unknown_page(self, store):
        assert store.get_revision("NonExistent", 1) is None


class TestGetLatestRevision:
    def test_returns_most_recent(self, store):
        store.record("Page", "v1", operation="create")
        store.record("Page", "v2", operation="edit")
        store.record("Page", "v3", operation="edit")
        latest = store.get_latest_revision("Page")
        assert latest is not None
        assert latest.revision == 3
        assert latest.content == "v3"

    def test_returns_none_for_unknown_page(self, store):
        assert store.get_latest_revision("NonExistent") is None


class TestListRevisions:
    def test_newest_first(self, store):
        for i in range(1, 6):
            store.record("Page", f"v{i}", operation="edit")
        revisions = store.list_revisions("Page")
        numbers = [r.revision for r in revisions]
        assert numbers == [5, 4, 3, 2, 1]

    def test_limit(self, store):
        for i in range(10):
            store.record("Page", f"v{i}", operation="edit")
        revisions = store.list_revisions("Page", limit=3)
        assert len(revisions) == 3

    def test_offset(self, store):
        for i in range(1, 6):
            store.record("Page", f"v{i}", operation="edit")
        revisions = store.list_revisions("Page", limit=2, offset=2)
        numbers = [r.revision for r in revisions]
        assert numbers == [3, 2]

    def test_empty_for_unknown_page(self, store):
        assert store.list_revisions("NonExistent") == []


class TestRevisionCount:
    def test_count_matches_records(self, store):
        for i in range(5):
            store.record("Page", f"v{i}", operation="edit")
        assert store.revision_count("Page") == 5

    def test_zero_for_unknown_page(self, store):
        assert store.revision_count("NonExistent") == 0


class TestDeletePageHistory:
    def test_removes_all_revisions(self, store):
        store.record("Page", "v1")
        store.record("Page", "v2")
        store.delete_page_history("Page")
        assert store.revision_count("Page") == 0

    def test_does_not_affect_other_pages(self, store):
        store.record("PageA", "v1")
        store.record("PageB", "v1")
        store.delete_page_history("PageA")
        assert store.revision_count("PageB") == 1

    def test_no_error_on_unknown_page(self, store):
        store.delete_page_history("NonExistent")  # should not raise


class TestRenameHistory:
    def test_revisions_moved_to_new_name(self, store):
        store.record("OldName", "v1")
        store.record("OldName", "v2")
        store.rename_history("OldName", "NewName")
        assert store.revision_count("OldName") == 0
        assert store.revision_count("NewName") == 2

    def test_revision_numbers_preserved(self, store):
        store.record("OldName", "v1")
        store.record("OldName", "v2")
        store.rename_history("OldName", "NewName")
        revisions = store.list_revisions("NewName")
        assert [r.revision for r in revisions] == [2, 1]


class TestDiffRevisions:
    def test_identical_content_all_equal(self, store):
        store.record("Page", "line one\nline two\n")
        store.record("Page", "line one\nline two\n")
        diff = store.diff_revisions("Page", 1, 2)
        assert all(d["tag"] == "equal" for d in diff)

    def test_added_line_appears_as_insert(self, store):
        store.record("Page", "line one\n")
        store.record("Page", "line one\nline two\n")
        diff = store.diff_revisions("Page", 1, 2)
        tags = [d["tag"] for d in diff]
        assert "insert" in tags
        assert "delete" not in tags

    def test_removed_line_appears_as_delete(self, store):
        store.record("Page", "line one\nline two\n")
        store.record("Page", "line one\n")
        diff = store.diff_revisions("Page", 1, 2)
        tags = [d["tag"] for d in diff]
        assert "delete" in tags
        assert "insert" not in tags

    def test_changed_line_appears_as_delete_then_insert(self, store):
        store.record("Page", "old line\n")
        store.record("Page", "new line\n")
        diff = store.diff_revisions("Page", 1, 2)
        tags = [d["tag"] for d in diff]
        assert "delete" in tags
        assert "insert" in tags

    def test_missing_revision_returns_empty(self, store):
        store.record("Page", "content")
        diff = store.diff_revisions("Page", 1, 99)
        assert diff == []

    def test_diff_contains_correct_content(self, store):
        store.record("Page", "hello\n")
        store.record("Page", "world\n")
        diff = store.diff_revisions("Page", 1, 2)
        deleted = [d for d in diff if d["tag"] == "delete"]
        inserted = [d for d in diff if d["tag"] == "insert"]
        assert deleted[0]["content"] == "hello"
        assert inserted[0]["content"] == "world"
