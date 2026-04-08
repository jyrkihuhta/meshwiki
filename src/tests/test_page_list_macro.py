"""Unit tests for the <<PageList>> macro."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from meshwiki.core.models import Page, PageMetadata
from meshwiki.core.parser import parse_wiki_content


def make_page(
    name: str,
    tags: list[str] | None = None,
    created_minutes_ago: int = 60,
    modified_minutes_ago: int = 60,
) -> Page:
    """Create a mock page."""
    created = datetime.now() - timedelta(minutes=created_minutes_ago)
    modified = datetime.now() - timedelta(minutes=modified_minutes_ago)
    metadata = PageMetadata(
        tags=tags or [],
        created=created,
        modified=modified,
    )
    return Page(
        name=name,
        content="# " + name,
        metadata=metadata,
        exists=True,
    )


def make_engine(pages: list) -> MagicMock:
    """Return a MagicMock engine whose list_pages_with_metadata() returns pages."""
    engine = MagicMock()
    engine.list_pages_with_metadata.return_value = pages
    return engine


def render(text: str, pages: list, **kwargs) -> str:
    """Render wiki text with a mocked engine supplying pages.

    Patch target is 'meshwiki.core.parser.get_engine' — the site where
    PageListPreprocessor imports/calls it (patch where used, not defined).
    Uses create=True because on some branches get_engine is not yet imported
    at that location.
    """
    engine = make_engine(pages)
    with patch("meshwiki.core.parser.get_engine", return_value=engine, create=True):
        return parse_wiki_content(text, **kwargs)


class TestPageListBasic:
    """Basic rendering tests."""

    def test_renders_all_pages(self):
        """<<PageList>> renders links to all pages."""
        pages = [
            make_page("Page A"),
            make_page("Page B"),
            make_page("Page C"),
        ]
        html = render("<<PageList>>", pages=pages)
        assert "page-list-wrapper" in html
        assert "page-list" in html
        assert "Page A" in html
        assert "Page B" in html
        assert "Page C" in html

    def test_wiki_link_for_each_page(self):
        """Each entry is a wiki link to the page."""
        pages = [make_page("TestPage")]
        html = render("<<PageList>>", pages=pages)
        assert 'href="/page/TestPage"' in html
        assert 'class="wiki-link">TestPage</a>' in html

    def test_page_name_with_spaces(self):
        """Page names with spaces are properly linked."""
        pages = [make_page("My Page Name")]
        html = render("<<PageList>>", pages=pages)
        assert 'href="/page/My_Page_Name"' in html


class TestPageListFiltering:
    """Tests for tag, prefix, and limit filtering."""

    def test_tag_filter(self):
        """<<PageList(tag=foo)>> only shows pages with tag 'foo'."""
        pages = [
            make_page("Page With Foo", tags=["foo"]),
            make_page("Page Without Tag", tags=[]),
            make_page("Another With Foo", tags=["foo", "bar"]),
        ]
        html = render("<<PageList(tag=foo)>>", pages=pages)
        assert "Page With Foo" in html
        assert "Another With Foo" in html
        assert "Page Without Tag" not in html

    def test_prefix_filter(self):
        """<<PageList(prefix=Docs/)>> only shows pages starting with 'Docs/'."""
        pages = [
            make_page("Docs/Introduction"),
            make_page("Docs/Getting Started"),
            make_page("Blog/First Post"),
            make_page("Other"),
        ]
        html = render("<<PageList(prefix=Docs/)>>", pages=pages)
        assert "Docs/Introduction" in html
        assert "Docs/Getting Started" in html
        assert "Blog/First Post" not in html
        assert "Other" not in html

    def test_limit(self):
        """<<PageList(limit=2)>> with 5 pages returns only 2."""
        pages = [make_page(f"Page {i}") for i in range(5)]
        html = render("<<PageList(limit=2)>>", pages=pages)
        count = html.count("page-list-item")
        assert count == 2

    def test_combined_args(self):
        """<<PageList(tag=foo, limit=1)>> with 3 tagged pages returns 1."""
        pages = [
            make_page("Page A", tags=["foo"]),
            make_page("Page B", tags=["foo"]),
            make_page("Page C", tags=["foo"]),
            make_page("Page D", tags=["bar"]),
        ]
        html = render("<<PageList(tag=foo, limit=1)>>", pages=pages)
        count = html.count("page-list-item")
        assert count == 1


class TestPageListSorting:
    """Tests for alphabetical sorting."""

    def test_alphabetical_order(self):
        """Pages are sorted case-insensitively by name."""
        pages = [
            make_page("Zebra"),
            make_page("apple"),
            make_page("Banana"),
        ]
        html = render("<<PageList>>", pages=pages)
        apple_idx = html.find("apple")
        banana_idx = html.find("Banana")
        zebra_idx = html.find("Zebra")
        assert apple_idx < banana_idx < zebra_idx


class TestPageListEdgeCases:
    """Edge case handling."""

    def test_empty_result(self):
        """<<PageList>> when storage returns [] shows empty message."""
        html = render("<<PageList>>", pages=[])
        assert "page-list-empty" in html
        assert "No pages found" in html

    def test_no_pages_shows_empty_not_unavailable(self):
        """<<PageList>> with no pages shows empty message, not unavailable."""
        html = render("<<PageList>>", pages=[])
        assert "page-list-empty" in html
        assert "No pages found" in html
        assert "page-list-unavailable" not in html

    def test_not_replaced_in_fenced_code_block(self):
        """<<PageList>> inside code blocks is escaped, not rendered."""
        content = "```\n<<PageList>>\n```"
        html = render(content, pages=[make_page("Test")])
        assert "page-list-wrapper" not in html
        assert "&lt;&lt;PageList&gt;&gt;" in html

    def test_not_replaced_in_tilde_code_block(self):
        """<<PageList>> inside ~~~ code blocks is escaped, not rendered."""
        content = "~~~\n<<PageList>>\n~~~"
        html = render(content, pages=[make_page("Test")])
        assert "page-list-wrapper" not in html


class TestPageListTags:
    """Tests for tag display."""

    def test_tags_displayed(self):
        """Page with tags shows tag pills as links."""
        pages = [make_page("TaggedPage", tags=["foo", "bar"])]
        html = render("<<PageList>>", pages=pages)
        assert "page-list-tags" in html
        assert 'href="/search?tag=foo"' in html
        assert 'href="/search?tag=bar"' in html
        assert "tag-pill" in html

    def test_no_tags_no_span(self):
        """Page without tags has no .page-list-tags span."""
        pages = [make_page("NoTagsPage", tags=[])]
        html = render("<<PageList>>", pages=pages)
        assert "page-list-tags" not in html


class TestPageListEngineUnavailable:
    """Tests for graceful degradation when the graph engine is unavailable."""

    def test_page_list_engine_unavailable(self):
        """When get_engine() returns None, PageList should not raise."""
        with patch(
            "meshwiki.core.parser.get_engine", return_value=None, create=True
        ):
            html = parse_wiki_content("<<PageList>>")
        assert html is not None
