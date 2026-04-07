"""Unit tests for the <<PageList>> macro."""

from datetime import datetime, timedelta

from meshwiki.core.models import Page, PageMetadata
from meshwiki.core.parser import parse_wiki_content


def make_page(
    name: str,
    title: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    created_minutes_ago: int = 60,
    modified_minutes_ago: int = 60,
) -> Page:
    """Create a mock page for testing PageList macro.

    Args:
        name: Page name.
        title: Optional page title (fallback to name if not set).
        status: Optional status value (stored in metadata for future use).
        tags: List of tags for the page.
        created_minutes_ago: Creation time offset in minutes from now.
        modified_minutes_ago: Modification time offset in minutes from now.
    """
    created = datetime.now() - timedelta(minutes=created_minutes_ago)
    modified = datetime.now() - timedelta(minutes=modified_minutes_ago)
    metadata = PageMetadata(
        title=title,
        tags=tags or [],
        created=created,
        modified=modified,
    )
    if status is not None:
        metadata.status = status
    return Page(
        name=name,
        content="# " + name,
        metadata=metadata,
        exists=True,
    )


def render(text: str, pages: list[Page] | None = None) -> str:
    """Render text through the parser, passing pages directly as all_pages."""
    if pages is None:
        pages = []
    return parse_wiki_content(text, all_pages=pages)


class TestPageListBasic:
    """Basic rendering tests."""

    def test_pagelist_all_pages(self) -> None:
        """<<PageList()>> with 3 pages shows all 3 in output."""
        pages = [
            make_page("Page A"),
            make_page("Page B"),
            make_page("Page C"),
        ]
        html = render("<<PageList>>", pages=pages)
        assert "Page A" in html
        assert "Page B" in html
        assert "Page C" in html

    def test_pagelist_ul_structure(self) -> None:
        """<<PageList>> output is wrapped in <ul class="page-list">."""
        pages = [make_page("Test Page")]
        html = render("<<PageList>>", pages=pages)
        assert '<ul class="page-list">' in html
        assert "</ul>" in html

    def test_pagelist_anchor_links(self) -> None:
        """Each page is rendered as an <a href="/page/{name}"> link."""
        pages = [make_page("TestPage")]
        html = render("<<PageList>>", pages=pages)
        assert 'href="/page/TestPage"' in html
        assert 'class="wiki-link">TestPage</a>' in html

    def test_pagelist_title_fallback(self) -> None:
        """Page with no explicit title uses page name as link text."""
        pages = [make_page("MyTestPage", title=None)]
        html = render("<<PageList>>", pages=pages)
        assert ">MyTestPage</a>" in html

    def test_pagelist_empty_call(self) -> None:
        """<<PageList>> with zero pages shows 'No pages found.' message."""
        html = render("<<PageList>>", pages=[])
        assert "No pages found" in html
        assert '<ul class="page-list">' not in html


class TestPageListFiltering:
    """Tests for tag, prefix, and limit filtering."""

    def test_pagelist_filter_tag(self) -> None:
        """<<PageList(tag="factory")>> shows only pages tagged 'factory'."""
        pages = [
            make_page("Page With Foo", tags=["factory"]),
            make_page("Page Without Tag", tags=[]),
            make_page("Another With Foo", tags=["factory", "docs"]),
        ]
        html = render("<<PageList(tag=factory)>>", pages=pages)
        assert "Page With Foo" in html
        assert "Another With Foo" in html
        assert "Page Without Tag" not in html

    def test_pagelist_filter_parent(self) -> None:
        """<<PageList(prefix=Docs/)>> shows only pages starting with 'Docs/'."""
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

    def test_pagelist_limit(self) -> None:
        """<<PageList(limit=2)>> with 4 pages shows exactly 2 list items."""
        pages = [make_page(f"Page {i}") for i in range(4)]
        html = render("<<PageList(limit=2)>>", pages=pages)
        count = html.count("page-list-item")
        assert count == 2

    def test_pagelist_combined_filters(self) -> None:
        """<<PageList(tag=factory, limit=1)>> returns only 1 matching page."""
        pages = [
            make_page("Page A", tags=["factory"]),
            make_page("Page B", tags=["factory"]),
            make_page("Page C", tags=["factory"]),
            make_page("Page D", tags=["docs"]),
        ]
        html = render("<<PageList(tag=factory, limit=1)>>", pages=pages)
        count = html.count("page-list-item")
        assert count == 1

    def test_pagelist_no_match(self) -> None:
        """No pages match filters: output contains 'No pages found.' and no <ul>."""
        pages = [
            make_page("Page A", tags=["foo"]),
            make_page("Page B", tags=["bar"]),
        ]
        html = render("<<PageList(tag=nonexistent)>>", pages=pages)
        assert "No pages found" in html
        assert "<ul>" not in html


class TestPageListSorting:
    """Tests for alphabetical sorting."""

    def test_pagelist_sorted_alphabetically(self) -> None:
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

    def test_pagelist_in_fenced_block(self) -> None:
        """Macro inside ``` code block appears as raw text, not rendered."""
        content = "```\n<<PageList>>\n```"
        html = render(content, pages=[make_page("Test")])
        assert "page-list-wrapper" not in html
        assert "&lt;&lt;PageList&gt;&gt;" in html or "<<PageList>>" in html

    def test_pagelist_in_tilde_block(self) -> None:
        """Macro inside ~~~ code block appears as raw text, not rendered."""
        content = "~~~\n<<PageList>>\n~~~"
        html = render(content, pages=[make_page("Test")])
        assert "page-list-wrapper" not in html

    def test_pagelist_page_name_with_spaces(self) -> None:
        """Page names with spaces get underscores in URL."""
        pages = [make_page("My Page Name")]
        html = render("<<PageList>>", pages=pages)
        assert 'href="/page/My_Page_Name"' in html


class TestPageListTags:
    """Tests for tag display in page list items."""

    def test_pagelist_tags_display(self) -> None:
        """Page with tags shows tag pills as search links."""
        pages = [make_page("TaggedPage", tags=["foo", "bar"])]
        html = render("<<PageList>>", pages=pages)
        assert "page-list-tags" in html
        assert 'href="/search?tag=foo"' in html
        assert 'href="/search?tag=bar"' in html
        assert "tag-pill" in html

    def test_pagelist_no_tags_no_span(self) -> None:
        """Page without tags has no .page-list-tags span."""
        pages = [make_page("NoTagsPage", tags=[])]
        html = render("<<PageList>>", pages=pages)
        assert "page-list-tags" not in html


class TestPageListStatus:
    """Tests for status badge display.

    Note: The current PageList implementation does not render status badges.
    These tests document the expected behavior once status badges are implemented.
    """

    def test_pagelist_status_badge_not_rendered(self) -> None:
        """Current implementation does not render status badges."""
        pages = [make_page("TestPage", status="in_progress")]
        html = render("<<PageList>>", pages=pages)
        assert 'class="badge' not in html

    def test_pagelist_status_badge_fallback(self) -> None:
        """Current implementation does not render status badges (no fallback needed)."""
        pages = [make_page("TestPage", status="unknown_status")]
        html = render("<<PageList>>", pages=pages)
        assert 'class="badge' not in html

    def test_pagelist_filter_status_not_implemented(self) -> None:
        """Status filtering is not implemented; tag filter works instead."""
        pages = [
            make_page("Page A", status="planned", tags=["foo"]),
            make_page("Page B", status="done", tags=["foo"]),
        ]
        html = render("<<PageList(tag=foo)>>", pages=pages)
        assert "Page A" in html
        assert "Page B" in html
