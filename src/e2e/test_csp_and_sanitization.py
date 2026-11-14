"""E2E tests for the strict CSP and sanitized page content.

Chromium enforces the app's Content-Security-Policy here, which no longer allows
inline script. These tests check that the pages still work under it (behaviour
moved to static JS) and that hostile page content stays inert in a real browser.
"""

import re
import time

import pytest
from playwright.sync_api import Page, expect

HOSTILE_CONTENT = """\
# Hostile page

<script>window.__xss = 'script'</script>

<img src=x onerror="window.__xss = 'img'">

<svg onload="window.__xss = 'svg'"></svg>

<a href="javascript:window.__xss = 'href'" id="evil-link">click</a>

<div hx-get="/api/pages/Home/preview" hx-trigger="load" id="evil-hx">hx</div>

<button onclick="window.__xss = 'button'" id="evil-btn">button</button>

Harmless trailing text.
"""


def _violations(messages: list[str]) -> list[str]:
    return [m for m in messages if "Content Security Policy" in m or "Refused to" in m]


class TestNoCspViolations:
    def test_main_pages_load_without_csp_violations(
        self, page: Page, base_url: str, create_page
    ):
        task = create_page(
            "CspTask",
            "---\ntype: task\nstatus: in_progress\n---\n"
            '<<TaskStatus>>\n\n<<NewPage(Tpl, "Make a page")>>\n\n[[CspPlain]]',
        )
        plain = create_page("CspPlain", "# Plain\n\nSome text with `code`.")

        console: list[str] = []
        errors: list[str] = []
        page.on("console", lambda m: console.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))

        for url in (
            "/",
            f"/page/{task}",
            f"/page/{plain}",
            f"/page/{plain}/edit",
            f"/page/{plain}/history",
            "/tags",
            "/search?q=Csp",
            "/graph",
        ):
            page.goto(f"{base_url}{url}")
            page.wait_for_load_state("networkidle")

        assert _violations(console) == []
        assert errors == []

    def test_boosted_navigation_still_runs_page_scripts(
        self, page: Page, base_url: str, create_page
    ):
        """hx-boost swaps the body; app.js must re-run for the new page."""
        a = create_page("BoostA", "# A\n\nlink to [[BoostB]]")
        create_page("BoostB", "# B\n\n```python\nprint('hi')\n```")
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text))

        page.goto(f"{base_url}/page/{a}")
        page.locator("a.wiki-link", has_text="BoostB").first.click()
        expect(page.locator(".page-header h1")).to_contain_text("BoostB")
        # Per-page setup ran after the swap: the theme toggle is wired up.
        page.locator("#theme-toggle").click()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        assert _violations(console) == []


class TestMovedBehaviours:
    def test_new_page_macro_opens_editor_with_template(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page("NewPageHost", '<<NewPage(MyTpl, "Make it")>>')
        page.goto(f"{base_url}/page/{name}")
        page.locator(".new-page-input").fill("Brand New")
        page.locator(".new-page-button").click()
        page.wait_for_url("**/edit?template=MyTpl")
        assert "/page/Brand%20New/edit" in page.url

    def test_new_page_macro_with_empty_name_does_nothing(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page("NewPageEmpty", '<<NewPage(MyTpl, "Make it")>>')
        page.goto(f"{base_url}/page/{name}")
        page.locator(".new-page-button").click()
        assert page.url.endswith(f"/page/{name}")

    def test_task_terminal_expand_button_toggles(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page(
            "TermHost",
            "---\ntype: task\nstatus: in_progress\n---\n<<TaskStatus>>",
        )
        page.goto(f"{base_url}/page/{name}")
        wrapper = page.locator(".task-status-terminal")
        expect(wrapper).to_have_count(1)
        expect(wrapper).to_have_attribute("data-terminal-page", name)
        button = page.locator(".task-terminal-expand-btn")
        button.click()
        expect(wrapper).to_have_class(re.compile("terminal-expanded"))
        expect(button).to_have_attribute("title", "Exit fullscreen")
        button.click()
        expect(wrapper).not_to_have_class(re.compile("terminal-expanded"))

    def test_restore_confirm_uses_data_attribute(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page("ConfirmHost", "# v1")
        page.goto(f"{base_url}/page/{name}/edit")
        page.locator("#content").fill("# v2")
        page.locator("button[type=submit]").first.click()
        page.wait_for_url(f"{base_url}/page/{name}*")
        page.goto(f"{base_url}/page/{name}/history")
        form = page.locator("form[data-confirm]").first
        assert "Restore" in form.get_attribute("data-confirm")
        messages: list[str] = []

        def on_dialog(dialog):
            messages.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        form.locator("button").click()
        assert messages and "Restore" in messages[0]
        # Dismissed: still on the history page, nothing was posted.
        assert page.url.endswith("/history")


class TestHostileContent:
    def _watch(self, page: Page) -> list[str]:
        dialogs: list[str] = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        return dialogs

    def test_hostile_page_content_is_inert(
        self, page: Page, base_url: str, create_page
    ):
        name = create_page("HostilePage", HOSTILE_CONTENT)
        dialogs = self._watch(page)
        page.goto(f"{base_url}/page/{name}")
        page.wait_for_load_state("networkidle")

        expect(page.locator("#page-content")).to_contain_text("Harmless trailing text.")
        assert page.evaluate("window.__xss") is None
        assert dialogs == []
        # The dangerous markup was removed from the DOM, not merely not-run. The
        # harmless elements stay (a plain <button>/<div>), minus their handlers.
        expect(page.locator("#page-content script")).to_have_count(0)
        expect(page.locator("#evil-btn")).not_to_have_attribute(
            "onclick", re.compile(".")
        )
        expect(page.locator("#evil-hx")).not_to_have_attribute(
            "hx-get", re.compile(".")
        )
        expect(page.locator("#evil-hx")).not_to_have_attribute(
            "hx-trigger", re.compile(".")
        )
        expect(page.locator("#evil-link")).not_to_have_attribute(
            "href", re.compile("javascript")
        )
        # Clicking the neutralised link must not run anything.
        page.locator("#evil-link").click()
        assert page.evaluate("window.__xss") is None

    def test_editor_preview_is_inert(self, page: Page, base_url: str, create_page):
        name = create_page("PreviewHost", "# Preview")
        dialogs = self._watch(page)
        page.goto(f"{base_url}/page/{name}/edit")
        textarea = page.locator("#content")
        textarea.fill(HOSTILE_CONTENT)
        textarea.dispatch_event("keyup")

        # The live preview rendered (so the checks below mean something)...
        pane = page.locator("#preview-pane")
        expect(pane).to_contain_text("Harmless trailing text.", timeout=5000)
        # ...and the dangerous markup was removed from it, not merely blocked by
        # the CSP: the sanitizer and the CSP are independent layers.
        expect(pane.locator("script")).to_have_count(0)
        expect(pane.locator("[onerror], [onclick], [onload]")).to_have_count(0)
        expect(pane.locator("[hx-get], [hx-post]")).to_have_count(0)
        assert page.evaluate("window.__xss") is None
        assert dialogs == []

    def test_hover_card_shows_excerpt_and_is_inert(
        self, page: Page, base_url: str, create_page
    ):
        target = create_page(
            "HoverTarget",
            '<script>window.__xss = "hover"</script>'
            "<img src=x onerror=\"window.__xss = 'hover-img'\">"
            "A short summary about the target page.",
        )
        host = create_page("HoverHost", f"See [[{target}]] for details.")
        dialogs = self._watch(page)
        page.goto(f"{base_url}/page/{host}")

        page.locator("a.wiki-link").first.hover()
        card = page.locator("#wiki-hover-card")
        expect(card).to_be_visible(timeout=5000)
        expect(card).to_contain_text("A short summary about the target page.")
        expect(card.locator("script, img")).to_have_count(0)
        assert page.evaluate("window.__xss") is None
        assert dialogs == []

        # Moving away hides it again.
        page.mouse.move(0, 0)
        expect(card).to_be_hidden()

    def test_graph_search_escapes_page_names(
        self, page: Page, base_url: str, create_page
    ):
        hostile = create_page("<img src=x onerror=window.__xss=1> graphname", "# x")
        create_page("GraphNeighbour", f"link to [[{hostile}]]")
        dialogs = self._watch(page)

        # The file watcher indexes the new pages shortly after they are written.
        deadline = time.time() + 15
        while time.time() < deadline:
            nodes = page.request.get(f"{base_url}/api/graph").json().get("nodes", [])
            if any("graphname" in n["id"] for n in nodes):
                break
            time.sleep(0.3)
        else:
            pytest.skip("graph engine did not index the pages in time")

        page.goto(f"{base_url}/graph")
        search = page.locator(".graph-search-input")
        try:
            search.wait_for(state="visible", timeout=10000)
        except Exception:
            pytest.skip("graph search UI did not load (D3/CDN unavailable)")
        search.fill("graphname")
        results = page.locator(".graph-search-result-item")
        expect(results.first).to_be_visible()
        # The page is both a file node and a link-target node, so there are
        # several results; none may have turned into markup.
        expect(page.locator(".graph-search-results img")).to_have_count(0)
        expect(page.locator(".graph-search-results [onerror]")).to_have_count(0)
        texts = results.all_inner_texts()
        assert any("<img" in text for text in texts), texts
        names = [r.get_attribute("data-name") or "" for r in results.all()]
        assert all("onerror" in name for name in names), names
        assert page.evaluate("window.__xss") is None
        assert dialogs == []
