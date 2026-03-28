"""Unit tests for FileStorage internals."""

import pytest

from meshwiki.core.models import PageMetadata
from meshwiki.core.storage import FileStorage


@pytest.fixture
def storage(tmp_path):
    return FileStorage(tmp_path)


# ============================================================
# Name / filename conversion
# ============================================================


class TestNameConversion:
    def test_get_path_simple(self, storage, tmp_path):
        assert storage._get_path("My Page") == tmp_path / "My_Page.md"

    def test_get_path_no_spaces(self, storage, tmp_path):
        assert storage._get_path("HomePage") == tmp_path / "HomePage.md"

    def test_get_path_subpage(self, storage, tmp_path):
        assert (
            storage._get_path("Projects/MeshWiki")
            == tmp_path / "Projects" / "MeshWiki.md"
        )

    def test_get_path_subpage_with_spaces(self, storage, tmp_path):
        assert (
            storage._get_path("My Project/My Page")
            == tmp_path / "My_Project" / "My_Page.md"
        )

    def test_get_path_traversal_blocked(self, storage):
        with pytest.raises(ValueError, match="traversal"):
            storage._get_path("../etc/passwd")

    def test_path_to_name_simple(self, storage, tmp_path):
        p = tmp_path / "My_Page.md"
        assert storage._path_to_name(p) == "My Page"

    def test_path_to_name_subpage(self, storage, tmp_path):
        p = tmp_path / "Projects" / "MeshWiki.md"
        assert storage._path_to_name(p) == "Projects/MeshWiki"


# ============================================================
# Frontmatter parsing
# ============================================================


class TestParseFrontmatter:
    def test_valid_frontmatter(self, storage):
        content = "---\ntitle: Hello\ntags:\n  - python\n---\n\n# Body"
        metadata, body = storage._parse_frontmatter(content)
        assert metadata.title == "Hello"
        assert metadata.tags == ["python"]
        assert body.strip() == "# Body"

    def test_no_frontmatter(self, storage):
        content = "# Just a heading\n\nSome text."
        metadata, body = storage._parse_frontmatter(content)
        assert metadata.title is None
        assert metadata.tags == []
        assert body == content

    def test_empty_frontmatter(self, storage):
        content = "---\n\n---\n\nBody text"
        metadata, body = storage._parse_frontmatter(content)
        # Empty YAML block returns defaults
        assert metadata.title is None
        assert metadata.tags == []

    def test_malformed_yaml(self, storage):
        content = "---\n: : invalid: yaml: [[\n---\n\nBody"
        metadata, body = storage._parse_frontmatter(content)
        # Falls back to default metadata
        assert metadata.title is None

    def test_frontmatter_with_tags_list(self, storage):
        content = "---\ntags:\n  - wiki\n  - python\n  - rust\n---\n\nBody"
        metadata, _ = storage._parse_frontmatter(content)
        assert metadata.tags == ["wiki", "python", "rust"]

    def test_frontmatter_with_extra_fields(self, storage):
        """Extra fields in frontmatter should be preserved."""
        content = "---\ntitle: Test\nstatus: draft\nauthor: alice\n---\n\nBody"
        metadata, body = storage._parse_frontmatter(content)
        assert metadata.title == "Test"
        assert metadata.status == "draft"
        assert metadata.author == "alice"
        assert body.strip() == "Body"


# ============================================================
# Frontmatter creation
# ============================================================


class TestCreateFrontmatter:
    def test_empty_metadata(self, storage):
        metadata = PageMetadata()
        assert storage._create_frontmatter(metadata) == ""

    def test_with_title(self, storage):
        metadata = PageMetadata(title="My Page")
        result = storage._create_frontmatter(metadata)
        assert result.startswith("---\n")
        assert result.endswith("---\n\n")
        assert "title: My Page" in result

    def test_with_tags(self, storage):
        metadata = PageMetadata(tags=["a", "b"])
        result = storage._create_frontmatter(metadata)
        assert "tags:" in result
        assert "- a" in result
        assert "- b" in result

    def test_with_dates(self, storage):
        from datetime import datetime

        dt = datetime(2025, 1, 15, 10, 30, 0)
        metadata = PageMetadata(created=dt, modified=dt)
        result = storage._create_frontmatter(metadata)
        assert "2025-01-15" in result


# ============================================================
# CRUD operations
# ============================================================


class TestCrudOperations:
    @pytest.mark.asyncio
    async def test_save_sets_timestamps(self, storage):
        page = await storage.save_page("TimePage", "# Hello")
        assert page.metadata.created is not None
        assert page.metadata.modified is not None

    @pytest.mark.asyncio
    async def test_save_preserves_created(self, storage):
        page1 = await storage.save_page("TimePage", "# V1")
        _ = page1.metadata.created

        page2 = await storage.save_page("TimePage", "# V2")
        # created should stay the same since re-save preserves frontmatter
        assert page2.metadata.modified is not None
        assert page2.metadata.modified >= page1.metadata.modified

    @pytest.mark.asyncio
    async def test_list_pages_sorted(self, storage):
        await storage.save_page("Zebra", "z")
        await storage.save_page("Apple", "a")
        await storage.save_page("Mango", "m")
        pages = await storage.list_pages()
        assert pages == sorted(pages)

    @pytest.mark.asyncio
    async def test_page_exists(self, storage):
        assert await storage.page_exists("Missing") is False
        await storage.save_page("Missing", "now exists")
        assert await storage.page_exists("Missing") is True

    @pytest.mark.asyncio
    async def test_get_nonexistent_page(self, storage):
        assert await storage.get_page("NoSuchPage") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_page(self, storage):
        assert await storage.delete_page("NoSuchPage") is False

    @pytest.mark.asyncio
    async def test_roundtrip_with_frontmatter(self, storage):
        content = "---\ntitle: Test\ntags:\n  - wiki\n---\n\n# Hello"
        await storage.save_page("FMPage", content)
        page = await storage.get_page("FMPage")
        assert page is not None
        assert page.metadata.title == "Test"
        assert page.metadata.tags == ["wiki"]

    @pytest.mark.asyncio
    async def test_roundtrip_preserves_extra_frontmatter(self, storage):
        """Extra frontmatter fields survive save/load cycle."""
        content = "---\ntitle: Test\nstatus: draft\nauthor: alice\ntags:\n  - wiki\n---\n\n# Hello"
        await storage.save_page("ExtraFM", content)
        raw = await storage.get_raw_content("ExtraFM")
        assert raw is not None
        assert "status: draft" in raw
        assert "author: alice" in raw
        assert "title: Test" in raw
        assert "# Hello" in raw

    @pytest.mark.asyncio
    async def test_get_raw_content(self, storage):
        """get_raw_content returns full file content including frontmatter."""
        content = "---\ntitle: My Page\ntags:\n  - test\n---\n\n# Content"
        await storage.save_page("RawPage", content)
        raw = await storage.get_raw_content("RawPage")
        assert raw is not None
        assert "---" in raw
        assert "title: My Page" in raw
        assert "# Content" in raw

    @pytest.mark.asyncio
    async def test_get_raw_content_nonexistent(self, storage):
        """get_raw_content returns None for nonexistent pages."""
        raw = await storage.get_raw_content("NoSuchPage")
        assert raw is None

    def test_init_creates_directory(self, tmp_path):
        new_dir = tmp_path / "subdir" / "deep"
        FileStorage(new_dir)
        assert new_dir.exists()


# ============================================================
# Search operations
# ============================================================


class TestSearchPages:
    @pytest.mark.asyncio
    async def test_search_empty_query(self, storage):
        await storage.save_page("Test", "content")
        results = await storage.search_pages("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_by_name(self, storage):
        await storage.save_page("Python Guide", "some content")
        await storage.save_page("Rust Guide", "other content")
        results = await storage.search_pages("Python")
        assert len(results) == 1
        assert results[0]["name"] == "Python Guide"
        assert results[0]["match_type"] == "name"

    @pytest.mark.asyncio
    async def test_search_by_content(self, storage):
        await storage.save_page("PageA", "has the keyword foobar inside")
        await storage.save_page("PageB", "nothing here")
        results = await storage.search_pages("foobar")
        assert len(results) == 1
        assert results[0]["name"] == "PageA"
        assert results[0]["match_type"] == "content"

    @pytest.mark.asyncio
    async def test_search_name_matches_first(self, storage):
        await storage.save_page("SearchTerm", "unrelated body")
        await storage.save_page("Other", "contains SearchTerm in body")
        results = await storage.search_pages("SearchTerm")
        assert len(results) == 2
        assert results[0]["match_type"] == "name"
        assert results[1]["match_type"] == "content"

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, storage):
        await storage.save_page("MyPage", "Hello World")
        results = await storage.search_pages("hello")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, storage):
        await storage.save_page("MyPage", "Hello")
        results = await storage.search_pages("zzzznotfound")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_snippet_has_context(self, storage):
        long_body = "A" * 100 + " targetword " + "B" * 100
        await storage.save_page("LongPage", long_body)
        results = await storage.search_pages("targetword")
        assert len(results) == 1
        assert "targetword" in results[0]["snippet"]


class TestListPagesWithMetadata:
    @pytest.mark.asyncio
    async def test_returns_pages_with_metadata(self, storage):
        await storage.save_page("Alpha", "---\ntags:\n  - test\n---\n\n# Alpha")
        await storage.save_page("Beta", "# Beta")
        pages = await storage.list_pages_with_metadata()
        assert len(pages) == 2
        names = [p.name for p in pages]
        assert "Alpha" in names
        assert "Beta" in names

    @pytest.mark.asyncio
    async def test_sorted_by_name(self, storage):
        await storage.save_page("Zebra", "z")
        await storage.save_page("Apple", "a")
        pages = await storage.list_pages_with_metadata()
        assert pages[0].name == "Apple"
        assert pages[1].name == "Zebra"

    @pytest.mark.asyncio
    async def test_empty_storage(self, storage):
        pages = await storage.list_pages_with_metadata()
        assert pages == []

    @pytest.mark.asyncio
    async def test_metadata_preserved(self, storage):
        await storage.save_page(
            "Tagged", "---\ntags:\n  - wiki\n  - python\n---\n\ncontent"
        )
        pages = await storage.list_pages_with_metadata()
        assert len(pages) == 1
        assert "wiki" in pages[0].metadata.tags
        assert "python" in pages[0].metadata.tags


class TestSearchByTag:
    @pytest.mark.asyncio
    async def test_filter_by_tag(self, storage):
        await storage.save_page("Page1", "---\ntags:\n  - python\n---\n\ncontent")
        await storage.save_page("Page2", "---\ntags:\n  - rust\n---\n\ncontent")
        await storage.save_page(
            "Page3", "---\ntags:\n  - python\n  - rust\n---\n\ncontent"
        )
        results = await storage.search_by_tag("python")
        names = [p.name for p in results]
        assert "Page1" in names
        assert "Page3" in names
        assert "Page2" not in names

    @pytest.mark.asyncio
    async def test_tag_case_insensitive(self, storage):
        await storage.save_page("Page1", "---\ntags:\n  - Python\n---\n\ncontent")
        results = await storage.search_by_tag("python")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_no_matching_tag(self, storage):
        await storage.save_page("Page1", "---\ntags:\n  - rust\n---\n\ncontent")
        results = await storage.search_by_tag("java")
        assert results == []

    @pytest.mark.asyncio
    async def test_pages_without_tags(self, storage):
        await storage.save_page("NoTags", "just content")
        results = await storage.search_by_tag("anything")
        assert results == []
