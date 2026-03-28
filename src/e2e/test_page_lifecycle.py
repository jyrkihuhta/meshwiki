"""E2E tests for page create, view, edit, delete flows."""

from playwright.sync_api import Page, expect


class TestPageCreation:
    def test_create_page_via_missing_page(
        self, page: Page, base_url: str, live_prefix: str
    ):
        """Visiting a nonexistent page redirects to the editor."""
        name = f"{live_prefix}NewTestPage"
        page.goto(f"{base_url}/page/{name}")
        expect(page).to_have_url(f"{base_url}/page/{name}/edit")
        expect(page.locator("#content")).to_be_visible()

    def test_create_and_save_page(self, page: Page, base_url: str, live_prefix: str):
        """Create a page via the editor and verify it renders."""
        name = f"{live_prefix}HelloWorld"
        page.goto(f"{base_url}/page/{name}/edit")
        page.locator("#content").fill("# Hello World\n\nThis is a test page.")
        page.locator("button[form='edit-form']").click()
        page.wait_for_url(f"{base_url}/page/{name}*")
        expect(page.locator(".page-content")).to_contain_text("Hello World")
        expect(page.locator(".page-content")).to_contain_text("This is a test page.")

    def test_page_appears_in_list(self, page: Page, base_url: str, create_page):
        """Created pages appear on the home page list."""
        name = create_page("ListTest", "# List Test")
        page.goto(base_url)
        expect(page.locator(".page-list-table")).to_contain_text(name)


class TestPageViewing:
    def test_renders_markdown(self, page: Page, base_url: str, create_page):
        name = create_page("MarkdownPage", "# Title\n\n**bold** and *italic*")
        page.goto(f"{base_url}/page/{name}")
        expect(page.locator(".page-content h1")).to_have_text("Title")
        expect(page.locator(".page-content strong")).to_have_text("bold")
        expect(page.locator(".page-content em")).to_have_text("italic")

    def test_wiki_link_to_existing_page(self, page: Page, base_url: str, create_page):
        target = create_page("Target", "# Target page")
        source = create_page("Source", f"Link to [[{target}]]")
        page.goto(f"{base_url}/page/{source}")
        link = page.locator("a.wiki-link")
        expect(link).to_have_text(target)
        expect(link).to_have_attribute("href", f"/page/{target}")

    def test_missing_wiki_link_styled(self, page: Page, base_url: str, create_page):
        source = create_page("Source", "Link to [[NonExistent]]")
        page.goto(f"{base_url}/page/{source}")
        expect(page.locator("a.wiki-link-missing")).to_be_visible()

    def test_tags_displayed(self, page: Page, base_url: str, create_page):
        name = create_page("Tagged", "---\ntags:\n  - python\n  - wiki\n---\n\ncontent")
        page.goto(f"{base_url}/page/{name}")
        expect(page.locator(".tag-link").first).to_be_visible()

    def test_code_block_highlighted(self, page: Page, base_url: str, create_page):
        name = create_page("CodePage", '```python\nprint("hello")\n```')
        page.goto(f"{base_url}/page/{name}")
        expect(page.locator("pre code")).to_be_visible()


class TestPageEditing:
    def test_edit_existing_page(self, page: Page, base_url: str, create_page):
        name = create_page("EditMe", "# Original content")
        page.goto(f"{base_url}/page/{name}")
        page.locator("a.btn:has-text('Edit')").click()
        expect(page.locator("#content")).to_contain_text("Original content")
        page.locator("#content").fill("# Updated content")
        page.locator("button[form='edit-form']").click()
        page.wait_for_url(f"{base_url}/page/{name}*")
        expect(page.locator(".page-content")).to_contain_text("Updated content")

    def test_edit_preserves_frontmatter(self, page: Page, base_url: str, create_page):
        name = create_page(
            "FrontTest", "---\nstatus: draft\ntags:\n  - test\n---\n\n# Content"
        )
        page.goto(f"{base_url}/page/{name}/edit")
        content = page.locator("#content").input_value()
        assert "status: draft" in content
        assert "- test" in content


class TestPageDeletion:
    def test_delete_page(self, page: Page, base_url: str, create_page):
        name = create_page("DeleteMe", "# To be deleted")
        page.goto(f"{base_url}/page/{name}")
        page.on("dialog", lambda d: d.accept())
        page.locator("button.btn-danger").click()
        page.wait_for_url(f"{base_url}/*")
        page.goto(f"{base_url}/page/{name}")
        expect(page).to_have_url(f"{base_url}/page/{name}/edit")

    def test_cancel_delete(self, page: Page, base_url: str, create_page):
        name = create_page("KeepMe", "# Keep this")
        page.goto(f"{base_url}/page/{name}")
        page.on("dialog", lambda d: d.dismiss())
        page.locator("button.btn-danger").click()
        expect(page.locator(".page-header h1")).to_contain_text(name)


class TestPageList:
    def test_empty_wiki(self, page: Page, base_url: str):
        page.goto(base_url)
        expect(page.locator(".page-list")).to_be_visible()

    def test_page_list_table(self, page: Page, base_url: str, create_page):
        alpha = create_page("Alpha", "# Alpha page")
        beta = create_page("Beta", "# Beta page")
        page.goto(base_url)
        expect(page.locator(".page-list-table")).to_contain_text(alpha)
        expect(page.locator(".page-list-table")).to_contain_text(beta)
