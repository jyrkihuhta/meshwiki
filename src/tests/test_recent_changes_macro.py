"""Unit tests for the <<RecentChanges>> macro."""

from datetime import datetime, timedelta
from unittest.mock import patch

from meshwiki.core.models import Page, PageMetadata
from meshwiki.core.parser import parse_wiki_content


def render(content: str) -> str:
    """Helper: render wiki content with RecentChanges context."""
    return parse_wiki_content(content)


class MockStorage:
    """Mock FileStorage for testing RecentChanges macro."""

    def __init__(self, pages: list[Page]):
        self._pages = pages

    async def list_pages_with_metadata(self) -> list[Page]:
        return self._pages


def make_page(name: str, minutes_ago: int = 0) -> Page:
    """Create a mock page with modified time set to N minutes ago."""
    modified = datetime.now() - timedelta(minutes=minutes_ago)
    return Page(
        name=name,
        content="# " + name,
        metadata=PageMetadata(modified=modified),
        exists=True,
    )


class TestRecentChangesBasic:
    """Basic rendering tests."""

    def test_renders_recent_changes_list(self):
        """<<RecentChanges>> renders a list of recent pages."""
        pages = [
            make_page("Page A", minutes_ago=5),
            make_page("Page B", minutes_ago=10),
            make_page("Page C", minutes_ago=15),
        ]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            assert "recent-changes-wrapper" in html
            assert "recent-changes-list" in html
            assert "Page A" in html
            assert "Page B" in html
            assert "Page C" in html

    def test_default_limit_is_10(self):
        """<<RecentChanges>> defaults to showing 10 pages."""
        pages = [make_page(f"Page {i}", minutes_ago=i) for i in range(15)]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            count = html.count("recent-changes-item")
            assert count == 10

    def test_custom_n_limit(self):
        """<<RecentChanges(5)>> shows exactly 5 pages."""
        pages = [make_page(f"Page {i}", minutes_ago=i) for i in range(10)]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges(5)>>")
            count = html.count("recent-changes-item")
            assert count == 5

    def test_wiki_link_for_each_page(self):
        """Each entry is a wiki link to the page."""
        pages = [make_page("TestPage", minutes_ago=5)]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            assert 'href="/page/TestPage"' in html
            assert 'class="wiki-link">TestPage</a>' in html

    def test_relative_time_displayed(self):
        """Each entry shows relative modification time."""
        pages = [make_page("RecentPage", minutes_ago=30)]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            assert "recent-changes-time" in html
            assert "30m ago" in html


class TestRecentChangesFiltering:
    """Tests for Factory/ page filtering."""

    def test_skips_factory_pages(self):
        """Pages starting with Factory/ are excluded."""
        pages = [
            make_page("RegularPage", minutes_ago=5),
            make_page("Factory/Task_001", minutes_ago=1),
            make_page("AnotherPage", minutes_ago=10),
        ]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            assert "RegularPage" in html
            assert "AnotherPage" in html
            assert "Factory/Task_001" not in html
            assert "recent-changes-item" in html
            count = html.count("recent-changes-item")
            assert count == 2


class TestRecentChangesEdgeCases:
    """Edge case handling."""

    def test_empty_wiki(self):
        """<<RecentChanges>> on empty wiki shows empty message."""
        with patch("meshwiki.core.dependencies._storage", MockStorage([])):
            html = render("<<RecentChanges>>")
            assert "recent-changes-empty" in html
            assert "No pages found" in html

    def test_storage_not_initialized(self):
        """<<RecentChanges>> handles uninitialized storage gracefully."""
        with patch("meshwiki.core.dependencies._storage", None):
            html = render("<<RecentChanges>>")
            assert "recent-changes-unavailable" in html
            assert "storage not available" in html

    def test_not_replaced_in_fenced_code_block(self):
        """<<RecentChanges>> inside code blocks is escaped, not rendered."""
        content = "```\n<<RecentChanges>>\n```"
        html = render(content)
        assert "recent-changes-wrapper" not in html
        assert "&lt;&lt;RecentChanges&gt;&gt;" in html

    def test_not_replaced_in_tilde_code_block(self):
        """<<RecentChanges>> inside ~~~ code blocks is escaped, not rendered."""
        content = "~~~\n<<RecentChanges>>\n~~~"
        html = render(content)
        assert "recent-changes-wrapper" not in html

    def test_custom_n_with_no_pages(self):
        """<<RecentChanges(5)>> with no pages shows empty message."""
        with patch("meshwiki.core.dependencies._storage", MockStorage([])):
            html = render("<<RecentChanges(5)>>")
            assert "recent-changes-empty" in html

    def test_page_name_with_spaces(self):
        """Page names with spaces are properly linked."""
        pages = [make_page("My Page Name", minutes_ago=5)]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            assert 'href="/page/My_Page_Name"' in html
            assert 'class="wiki-link">My Page Name</a>' in html


class TestRecentChangesSorting:
    """Tests for proper sorting by modification time."""

    def test_sorted_by_modified_descending(self):
        """Pages are sorted by modification time, newest first."""
        pages = [
            make_page("OldPage", minutes_ago=100),
            make_page("NewPage", minutes_ago=1),
            make_page("MidPage", minutes_ago=50),
        ]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            new_idx = html.find("NewPage")
            mid_idx = html.find("MidPage")
            old_idx = html.find("OldPage")
            assert new_idx < mid_idx < old_idx

    def test_pages_without_modified_time_last(self):
        """Pages with no modified time appear at the end."""
        page_with_time = Page(
            name="TimedPage",
            content="# Timed",
            metadata=PageMetadata(modified=datetime.now() - timedelta(minutes=5)),
            exists=True,
        )
        page_without_time = Page(
            name="NoTimePage",
            content="# No Time",
            metadata=PageMetadata(modified=None),
            exists=True,
        )
        pages = [page_without_time, page_with_time]
        with patch("meshwiki.core.dependencies._storage", MockStorage(pages)):
            html = render("<<RecentChanges>>")
            timed_idx = html.find("TimedPage")
            notime_idx = html.find("NoTimePage")
            assert timed_idx < notime_idx
