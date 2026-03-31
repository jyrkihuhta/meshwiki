"""Tests for the query engine functionality."""

import pytest

from graph_core import Filter, GraphEngine, MetaTableResult, MetaTableRow


@pytest.fixture
def wiki_with_metadata(tmp_path):
    """Create wiki files with various metadata for testing."""
    files = {
        "ProjectA.md": """---
status: active
tags:
  - rust
  - backend
priority: high
---
# Project A

This is Project A, a backend project using Rust.

Links to [[ProjectB]] and [[Docs]].
""",
        "ProjectB.md": """---
status: draft
tags:
  - python
  - frontend
priority: low
---
# Project B

This is Project B, a frontend project.

Links to [[ProjectA]].
""",
        "Docs.md": """---
status: published
tags:
  - documentation
---
# Documentation

Main documentation page.
""",
        "Archive.md": """---
status: archived
---
# Archive

Old content without tags or priority.
""",
    }

    for name, content in files.items():
        (tmp_path / name).write_text(content)

    engine = GraphEngine(str(tmp_path))
    engine.rebuild()
    return engine


class TestFilterCreation:
    """Test Filter factory methods."""

    def test_filter_equals(self):
        f = Filter.equals("status", "draft")
        assert "equals" in repr(f)
        assert "status" in repr(f)
        assert "draft" in repr(f)

    def test_filter_has_key(self):
        f = Filter.has_key("tags")
        assert "has_key" in repr(f)
        assert "tags" in repr(f)

    def test_filter_contains(self):
        f = Filter.contains("tags", "rust")
        assert "contains" in repr(f)
        assert "tags" in repr(f)
        assert "rust" in repr(f)

    def test_filter_matches(self):
        f = Filter.matches("version", r"v\d+")
        assert "matches" in repr(f)
        assert "version" in repr(f)

    def test_filter_links_to(self):
        f = Filter.links_to("HomePage")
        assert "links_to" in repr(f)
        assert "HomePage" in repr(f)

    def test_filter_linked_from(self):
        f = Filter.linked_from("Index")
        assert "linked_from" in repr(f)
        assert "Index" in repr(f)


class TestQueryEquals:
    """Test Equals filter functionality."""

    def test_query_equals_single_match(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.equals("status", "draft")])
        assert len(results) == 1
        assert results[0].name == "ProjectB"

    def test_query_equals_multiple_matches(self, wiki_with_metadata):
        # Both ProjectA and ProjectB have priority
        results = wiki_with_metadata.query([Filter.equals("priority", "high")])
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_equals_no_match(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.equals("status", "nonexistent")])
        assert len(results) == 0

    def test_query_equals_multi_value_field(self, wiki_with_metadata):
        """Test that Equals matches any value in a multi-value field."""
        results = wiki_with_metadata.query([Filter.equals("tags", "rust")])
        assert len(results) == 1
        assert results[0].name == "ProjectA"

        results = wiki_with_metadata.query([Filter.equals("tags", "frontend")])
        assert len(results) == 1
        assert results[0].name == "ProjectB"


class TestQueryHasKey:
    """Test HasKey filter functionality."""

    def test_query_has_key_present(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.has_key("priority")])
        names = [r.name for r in results]
        assert "ProjectA" in names
        assert "ProjectB" in names
        assert "Docs" not in names
        assert "Archive" not in names

    def test_query_has_key_tags(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.has_key("tags")])
        names = [r.name for r in results]
        assert "ProjectA" in names
        assert "ProjectB" in names
        assert "Docs" in names
        assert "Archive" not in names

    def test_query_has_key_missing(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.has_key("nonexistent_key")])
        assert len(results) == 0


class TestQueryContains:
    """Test Contains filter functionality."""

    def test_query_contains_substring(self, wiki_with_metadata):
        # "backend" and "frontend" both contain "end"
        results = wiki_with_metadata.query([Filter.contains("tags", "end")])
        names = [r.name for r in results]
        assert "ProjectA" in names  # has "backend"
        assert "ProjectB" in names  # has "frontend"
        assert "Docs" not in names

    def test_query_contains_exact_word(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.contains("tags", "rust")])
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_contains_no_match(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.contains("tags", "java")])
        assert len(results) == 0


class TestQueryMatches:
    """Test Matches (regex) filter functionality."""

    def test_query_matches_simple_regex(self, wiki_with_metadata):
        # Match tags starting with 'r'
        results = wiki_with_metadata.query([Filter.matches("tags", r"^r")])
        names = [r.name for r in results]
        assert "ProjectA" in names  # has "rust"
        assert "ProjectB" not in names

    def test_query_matches_pattern(self, wiki_with_metadata):
        # Match status ending with 'ed'
        results = wiki_with_metadata.query([Filter.matches("status", r".*ed$")])
        names = [r.name for r in results]
        assert "Docs" in names  # "published"
        assert "Archive" in names  # "archived"
        assert "ProjectA" not in names  # "active"
        assert "ProjectB" not in names  # "draft"

    def test_query_matches_invalid_regex(self, wiki_with_metadata):
        # Invalid regex should return no matches (not raise error)
        results = wiki_with_metadata.query([Filter.matches("tags", r"[invalid")])
        assert len(results) == 0


class TestQueryLinksTo:
    """Test LinksTo filter functionality."""

    def test_query_links_to_existing(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.links_to("ProjectB")])
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_links_to_multiple(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.links_to("ProjectA")])
        assert len(results) == 1
        assert results[0].name == "ProjectB"

    def test_query_links_to_docs(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.links_to("Docs")])
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_links_to_no_links(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.links_to("Archive")])
        assert len(results) == 0


class TestQueryLinkedFrom:
    """Test LinkedFrom filter functionality."""

    def test_query_linked_from(self, wiki_with_metadata):
        # Pages that have backlinks from ProjectA
        results = wiki_with_metadata.query([Filter.linked_from("ProjectA")])
        names = [r.name for r in results]
        assert "ProjectB" in names
        assert "Docs" in names

    def test_query_linked_from_no_backlinks(self, wiki_with_metadata):
        results = wiki_with_metadata.query([Filter.linked_from("Archive")])
        assert len(results) == 0


class TestQueryMultipleFilters:
    """Test combining multiple filters with AND logic."""

    def test_query_multiple_filters_and(self, wiki_with_metadata):
        # status=active AND has priority
        results = wiki_with_metadata.query(
            [
                Filter.equals("status", "active"),
                Filter.has_key("priority"),
            ]
        )
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_multiple_filters_no_match(self, wiki_with_metadata):
        # No page has both status=draft AND tags=rust
        results = wiki_with_metadata.query(
            [
                Filter.equals("status", "draft"),
                Filter.equals("tags", "rust"),
            ]
        )
        assert len(results) == 0

    def test_query_three_filters(self, wiki_with_metadata):
        results = wiki_with_metadata.query(
            [
                Filter.has_key("tags"),
                Filter.has_key("priority"),
                Filter.equals("status", "active"),
            ]
        )
        assert len(results) == 1
        assert results[0].name == "ProjectA"

    def test_query_empty_filters(self, wiki_with_metadata):
        # No filters = all pages
        results = wiki_with_metadata.query([])
        assert len(results) == 4


class TestMetaTable:
    """Test metatable functionality."""

    def test_metatable_basic(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "active")], ["name", "status", "priority"]
        )
        assert isinstance(result, MetaTableResult)
        assert len(result) == 1
        assert result.columns == ["name", "status", "priority"]

        row = result.rows[0]
        assert isinstance(row, MetaTableRow)
        assert row.page_name == "ProjectA"
        assert row.get("status") == ["active"]
        assert row.get("priority") == ["high"]
        assert row.get("name") == ["ProjectA"]

    def test_metatable_multiple_rows(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.has_key("tags")], ["name", "status"]
        )
        assert len(result) == 3  # ProjectA, ProjectB, Docs

        names = [row.page_name for row in result.rows]
        assert "ProjectA" in names
        assert "ProjectB" in names
        assert "Docs" in names

    def test_metatable_iteration(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.has_key("tags")], ["name", "tags"]
        )

        names = []
        for row in result:
            names.append(row.page_name)

        assert "ProjectA" in names
        assert "ProjectB" in names
        assert "Docs" in names

    def test_metatable_missing_column(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "archived")],
            ["name", "priority"],  # Archive has no priority
        )

        assert len(result) == 1
        row = result.rows[0]
        assert row.page_name == "Archive"
        assert row.get("name") == ["Archive"]
        assert row.get("priority") == []  # Missing columns return empty list

    def test_metatable_file_path_column(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "draft")], ["name", "file_path"]
        )

        row = result.rows[0]
        assert row.get("file_path") == ["ProjectB.md"]

    def test_metatable_is_empty(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "nonexistent")], ["name"]
        )
        assert result.is_empty()
        assert len(result) == 0

    def test_metatable_repr(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "active")], ["name", "status"]
        )
        repr_str = repr(result)
        assert "MetaTableResult" in repr_str
        assert "rows=1" in repr_str


class TestMetaTableRow:
    """Test MetaTableRow functionality."""

    def test_row_get_existing_column(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "active")], ["name", "tags"]
        )
        row = result.rows[0]

        tags = row.get("tags")
        assert "rust" in tags
        assert "backend" in tags

    def test_row_get_missing_column(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "active")],
            ["name"],  # Only name column
        )
        row = result.rows[0]

        # Asking for a column not in the result
        assert row.get("nonexistent") == []

    def test_row_repr(self, wiki_with_metadata):
        result = wiki_with_metadata.metatable(
            [Filter.equals("status", "active")], ["name"]
        )
        row = result.rows[0]
        repr_str = repr(row)
        assert "MetaTableRow" in repr_str
        assert "ProjectA" in repr_str
