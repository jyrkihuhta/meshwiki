"""E2E tests for the editor: toolbar, keyboard shortcuts, preview, autocomplete."""

import re

from playwright.sync_api import Page, expect


class TestEditorToolbar:
    def _open_editor(self, page: Page, base_url: str) -> None:
        page.goto(f"{base_url}/page/ToolbarTest/edit")
        page.locator("#content").fill("")

    def test_bold_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        page.locator("[data-action='bold']").click()
        assert "**hello**" in textarea.input_value()

    def test_italic_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        page.locator("[data-action='italic']").click()
        assert "*hello*" in textarea.input_value()

    def test_strikethrough_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        page.locator("[data-action='strikethrough']").click()
        assert "~~hello~~" in textarea.input_value()

    def test_heading_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        page.locator("[data-action='heading']").click()
        assert "## " in page.locator("#content").input_value()

    def test_code_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        page.locator("[data-action='code']").click()
        assert "`hello`" in textarea.input_value()

    def test_wikilink_button(self, page: Page, base_url: str):
        self._open_editor(page, base_url)
        page.locator("[data-action='wikilink']").click()
        value = page.locator("#content").input_value()
        assert "[[" in value and "]]" in value


class TestKeyboardShortcuts:
    def test_ctrl_b_bold(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/ShortcutTest/edit")
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        textarea.press("Control+b")
        assert "**hello**" in textarea.input_value()

    def test_ctrl_i_italic(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/ShortcutTest/edit")
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        textarea.press("Control+i")
        assert "*hello*" in textarea.input_value()

    def test_ctrl_k_link(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/ShortcutTest/edit")
        textarea = page.locator("#content")
        textarea.fill("hello")
        textarea.evaluate("el => { el.selectionStart = 0; el.selectionEnd = 5; }")
        textarea.press("Control+k")
        assert "[hello](url)" in textarea.input_value()

    def test_ctrl_s_saves(self, page: Page, base_url: str, live_prefix: str):
        name = f"{live_prefix}SaveShortcut"
        page.goto(f"{base_url}/page/{name}/edit")
        textarea = page.locator("#content")
        textarea.fill("# Saved via shortcut")
        textarea.press("Control+s")
        page.wait_for_url(f"{base_url}/page/{name}*")
        expect(page.locator(".page-content")).to_contain_text("Saved via shortcut")


class TestLivePreview:
    def test_preview_renders_markdown(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/PreviewTest/edit")
        page.evaluate("localStorage.setItem('meshwiki-preview', 'true')")
        page.reload()
        textarea = page.locator("#content")
        textarea.fill("# Preview Heading")
        # fill() doesn't trigger keyup, which HTMX needs for preview
        textarea.dispatch_event("keyup")
        expect(page.locator("#preview-pane h1")).to_have_text(
            "Preview Heading", timeout=10000
        )

    def test_preview_toggle_hides_pane(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/ToggleTest/edit")
        page.evaluate("localStorage.setItem('meshwiki-preview', 'true')")
        page.reload()
        page.locator("#toggle-preview").click()
        expect(page.locator("#editor-split")).to_have_class(
            re.compile(r"editor-split--no-preview")
        )

    def test_preview_toggle_persists(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/PersistTest/edit")
        page.evaluate("localStorage.setItem('meshwiki-preview', 'true')")
        page.reload()
        page.locator("#toggle-preview").click()
        page.reload()
        expect(page.locator("#editor-split")).to_have_class(
            re.compile(r"editor-split--no-preview")
        )

    def test_ctrl_p_toggles_preview(self, page: Page, base_url: str):
        page.goto(f"{base_url}/page/CtrlPTest/edit")
        page.evaluate("localStorage.setItem('meshwiki-preview', 'true')")
        page.reload()
        page.locator("#content").press("Control+p")
        expect(page.locator("#editor-split")).to_have_class(
            re.compile(r"editor-split--no-preview")
        )


class TestWikiLinkAutocomplete:
    def test_autocomplete_shows_on_bracket(
        self, page: Page, base_url: str, create_page, live_prefix: str
    ):
        create_page("Python", "# Python")
        create_page("PythonGuide", "# Python Guide")
        create_page("Rust", "# Rust")
        page.goto(f"{base_url}/page/ACTest/edit")
        textarea = page.locator("#content")
        textarea.fill("")
        textarea.type(f"[[{live_prefix}Py")
        dropdown = page.locator("#autocomplete-dropdown")
        expect(dropdown.locator(".autocomplete-item").first).to_be_visible(
            timeout=10000
        )

    def test_click_inserts_page_name(
        self, page: Page, base_url: str, create_page, live_prefix: str
    ):
        name = create_page("ClickTarget", "# Click Target")
        page.goto(f"{base_url}/page/ACClick/edit")
        textarea = page.locator("#content")
        textarea.fill("")
        textarea.type(f"[[{live_prefix}Click")
        # Wait for autocomplete dropdown to appear
        item = page.locator(".autocomplete-item").first
        expect(item).to_be_visible(timeout=10000)
        item.click()
        value = textarea.input_value()
        assert f"[[{name}]]" in value

    def test_escape_closes_autocomplete(
        self, page: Page, base_url: str, create_page, live_prefix: str
    ):
        create_page("EscPage", "# Esc")
        page.goto(f"{base_url}/page/ACEsc/edit")
        textarea = page.locator("#content")
        textarea.fill("")
        textarea.type(f"[[{live_prefix}Esc")
        # Wait for dropdown to appear first
        expect(
            page.locator("#autocomplete-dropdown .autocomplete-item").first
        ).to_be_visible(timeout=10000)
        textarea.press("Escape")
        expect(page.locator("#autocomplete-dropdown")).to_be_empty()
