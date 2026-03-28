"""E2E tests for search, tags, TOC sidebar, and breadcrumbs."""

import re

from playwright.sync_api import Page, expect


class TestHeaderSearch:
    def test_search_shows_results(self, page: Page, base_url: str, create_page):
        name = create_page("SearchTarget", "# Unique searchable content here")
        page.goto(base_url)
        search_input = page.locator("#header-search-input")
        search_input.fill(name)
        # Wait for HTMX search results to render
        expect(page.locator("#header-search-results")).to_contain_text(
            name, timeout=10000
        )

    def test_search_no_results(self, page: Page, base_url: str):
        page.goto(base_url)
        search_input = page.locator("#header-search-input")
        search_input.fill("xyznonexistent123")
        page.wait_for_timeout(2000)
        results = page.locator("#header-search-results")
        expect(results).not_to_contain_text("xyznonexistent123")


class TestSearchPage:
    def test_full_search_page(self, page: Page, base_url: str, create_page):
        name = create_page("Findable", "# Findable page with unique content")
        page.goto(f"{base_url}/search?q={name}")
        expect(page.locator("body")).to_contain_text(name)

    def test_search_by_tag(self, page: Page, base_url: str, create_page):
        name = create_page(
            "TaggedPage", "---\ntags:\n  - specialtag\n---\n\n# Tagged content"
        )
        page.goto(f"{base_url}/search?tag=specialtag")
        expect(page.locator("body")).to_contain_text(name)


class TestTagsPage:
    def test_tags_page_shows_tags(self, page: Page, base_url: str, create_page):
        create_page("T1", "---\ntags:\n  - alpha\n  - beta\n---\n\ncontent")
        create_page("T2", "---\ntags:\n  - alpha\n---\n\ncontent")
        page.goto(f"{base_url}/tags")
        expect(page.locator("body")).to_contain_text("alpha")
        expect(page.locator("body")).to_contain_text("beta")

    def test_tag_links_to_search(self, page: Page, base_url: str, create_page):
        create_page("T3", "---\ntags:\n  - clicktag\n---\n\ncontent")
        page.goto(f"{base_url}/tags")
        page.locator("a:has-text('clicktag')").click()
        expect(page).to_have_url(re.compile(r"search.*tag=clicktag"))


class TestTocSidebar:
    def test_page_tree_visible(self, page: Page, base_url: str, create_page):
        name = create_page("TocPage", "## Second\n\n## Third")
        page.goto(f"{base_url}/page/{name}")
        sidebar = page.locator(".toc-sidebar")
        expect(sidebar).to_be_visible(timeout=5000)
        expect(sidebar).to_contain_text("Pages")

    def test_page_tree_always_visible_when_tree_exists(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page("NoTocPage", "Just plain text, no headings.")
        page.goto(f"{base_url}/page/{name}")
        expect(page.locator(".toc-sidebar")).to_be_visible(timeout=10000)


class TestBreadcrumbs:
    def test_breadcrumb_shows_path(self, page: Page, base_url: str, create_page):
        name = create_page("BreadPage", "# Bread content")
        page.goto(f"{base_url}/page/{name}")
        breadcrumb = page.locator(".breadcrumb")
        expect(breadcrumb).to_contain_text("Home")
        # Assert the leaf page name appears in the breadcrumb
        expect(breadcrumb).to_contain_text(name.split("/")[-1])

    def test_breadcrumb_home_link(self, page: Page, base_url: str, create_page):
        name = create_page("NavPage", "# Nav")
        page.goto(f"{base_url}/page/{name}")
        page.locator(".breadcrumb a:has-text('Home')").click()
        expect(page).to_have_url(base_url + "/")
