"""Tests for rendered-HTML sanitization and the no-inline-script guarantee.

Wiki content can come from untrusted sources (the agent factory builds pages from
external report text and LLM output), and the templates render it with ``| safe``.
These tests pin down three things:

1. the sanitizer neutralises known XSS vectors (checked as DOM invariants, not
   brittle string matches),
2. everything the app legitimately emits survives it, and
3. no page ships inline script or ``on*=`` handlers, which is what lets the CSP
   drop ``'unsafe-inline'`` for scripts.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.main
from meshwiki.core.parser import parse_wiki_content
from meshwiki.core.sanitize import sanitize_html

# ── DOM helpers ───────────────────────────────────────────────────────────────

_FORBIDDEN_TAGS = {
    "script", "iframe", "object", "embed", "style", "form", "svg", "math",
    "link", "meta", "base", "frame", "frameset", "applet", "template", "noscript",
}  # fmt: skip
_URL_ATTRS = {"href", "src", "action", "formaction", "data", "poster", "background"}


def elements(html: str) -> list[tuple[str, dict[str, str | None]]]:
    """Return ``(tag, attrs)`` for every start tag in *html*."""
    found: list[tuple[str, dict[str, str | None]]] = []

    class _Collector(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, dict(attrs)))

        handle_startendtag = handle_starttag

    _Collector().feed(html)
    return found


def browser_url(value: str) -> str:
    """Normalise a URL attribute the way the WHATWG URL parser does.

    Leading/trailing C0 controls and spaces are trimmed and ASCII tab/CR/LF are
    removed anywhere. Other whitespace is *not* removed, so ``java   script:x``
    is a relative path, not a script URL.
    """
    value = value.strip("".join(chr(c) for c in range(0x21)))
    return re.sub(r"[\t\r\n]", "", value).lower()


def assert_inert(html: str) -> None:
    """Assert *html* contains nothing that can execute script or act as the viewer."""
    for tag, attrs in elements(html):
        assert tag not in _FORBIDDEN_TAGS, f"forbidden <{tag}> survived: {html!r}"
        for name, value in attrs.items():
            assert not name.startswith("on"), f"handler {name} survived: {html!r}"
            if name.startswith("hx-"):
                assert tag == "a", f"{name} on <{tag}> survived: {html!r}"
            if name in _URL_ATTRS:
                url = browser_url(value or "")
                assert not url.startswith(("javascript:", "vbscript:")), html
                if url.startswith("data:"):
                    assert tag == "img" and url.startswith("data:image/"), html
            if name == "style":
                assert tag in ("div", "td", "th"), f"style on <{tag}>: {html!r}"


# ── 1. Known XSS vectors ──────────────────────────────────────────────────────

XSS_VECTORS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    '<img src=x onerror="alert(1)">',
    "<img src=x onerror=alert(1)//",
    '<IMG SRC="javascript:alert(1)">',
    '<img src="x" onload="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href="  javascript:alert(1)">x</a>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<a href="&#106;avascript:alert(1)">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
    '<a href="https://ok.example" onclick="alert(1)">x</a>',
    '<a href="/x" hx-post="/page/Home/delete" hx-trigger="load">x</a>',
    '<a href="/x" hx-get="/page/Home/delete" hx-trigger="load">x</a>',
    '<div hx-post="/page/Home/delete" hx-trigger="load"></div>',
    '<div hx-get="/api/v1/tasks" hx-trigger="load" hx-target="body"></div>',
    '<button onclick="alert(1)">x</button>',
    '<button formaction="//evil.example" type="submit">x</button>',
    '<form action="//evil.example"><input name="p"><button>go</button></form>',
    '<input type="image" src="x" onerror="alert(1)">',
    '<input autofocus onfocus="alert(1)">',
    '<iframe src="javascript:alert(1)"></iframe>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<object data="javascript:alert(1)"></object>',
    '<embed src="javascript:alert(1)">',
    "<svg onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    "<svg><a xlink:href='javascript:alert(1)'><text>x</text></a></svg>",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "<math href='javascript:alert(1)'>x</math>",
    "<style>body{display:none}</style>",
    "<link rel=stylesheet href=//evil.example/x.css>",
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',
    '<base href="//evil.example/">',
    "<details open ontoggle=alert(1)>x</details>",
    "<video><source onerror=alert(1)></video>",
    "<body onload=alert(1)>",
    '<div style="position:fixed;top:0;left:0;width:100%;height:100%">overlay</div>',
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<p style="x:expression(alert(1))">x</p>',
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "<template><script>alert(1)</script></template>",
    "<!--[if IE]><script>alert(1)</script><![endif]-->",
    "<![CDATA[<script>alert(1)</script>]]>",
    '<a href="x" target="_blank" rel="opener">x</a>',
    '<img src="data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+">',
]


@pytest.mark.parametrize("vector", XSS_VECTORS)
def test_sanitize_html_neutralises_vector(vector):
    assert_inert(sanitize_html(vector))


@pytest.mark.parametrize("vector", XSS_VECTORS)
def test_parse_wiki_content_neutralises_vector(vector):
    """Same vectors through the full Markdown pipeline, raw and inside Markdown."""
    assert_inert(parse_wiki_content(vector))
    assert_inert(parse_wiki_content(f"# Title\n\nSome *text*\n\n{vector}\n\nmore"))
    assert_inert(parse_wiki_content(f"- item\n- {vector}\n"))
    assert_inert(parse_wiki_content(f"| a | b |\n|---|---|\n| {vector} | x |\n"))


def test_markdown_attr_list_cannot_inject_handlers():
    """The attr_list extension turns ``{: onclick=... }`` into real attributes."""
    html = parse_wiki_content('Click me\n{: onclick="alert(1)" hx-post="/x" }')
    assert_inert(html)


def test_markdown_link_syntax_javascript_url():
    assert_inert(parse_wiki_content("[click](javascript:alert(1))"))
    assert_inert(parse_wiki_content("![img](javascript:alert(1))"))
    assert_inert(parse_wiki_content("<javascript:alert(1)>"))


def test_wiki_link_with_hostile_name_stays_inert():
    html = parse_wiki_content('[[x" onmouseover="alert(1)]]')
    assert_inert(html)
    html = parse_wiki_content("[[<img src=x onerror=alert(1)>|label]]")
    assert_inert(html)


def test_script_content_is_dropped_not_just_untagged():
    out = sanitize_html("before<script>alert('pwned')</script>after")
    assert "pwned" not in out
    assert "before" in out and "after" in out


def test_unknown_tags_keep_their_text():
    assert sanitize_html("<blink>hello</blink>") == "hello"


def test_comments_are_stripped():
    assert "script" not in sanitize_html("a<!-- <script>x</script> -->b")


# ── 2. Legitimate content survives ────────────────────────────────────────────


def test_plain_markdown_survives():
    html = parse_wiki_content(
        "# Heading\n\nA **bold** and *italic* and `code` line.\n\n"
        "1. one\n2. two\n\n> quote\n\n---\n\n[ext](https://example.com)"
    )
    for fragment in (
        "<h1",
        "<strong>bold</strong>",
        "<em>italic</em>",
        "<code>code</code>",
        "<ol>",
        "<blockquote>",
        "<hr",
        '<a href="https://example.com">ext</a>',
    ):
        assert fragment in html, fragment


def test_fenced_code_keeps_language_class():
    html = parse_wiki_content("```python\nprint('hi')\n```")
    assert "<pre>" in html and "language-python" in html


def test_tables_keep_alignment():
    html = parse_wiki_content("| a | b |\n|:-:|--:|\n| 1 | 2 |")
    assert "<table>" in html and "<thead>" in html and "<tbody>" in html
    assert "text-align" in html


def test_task_list_checkboxes_survive():
    html = parse_wiki_content("- [x] done\n- [ ] todo")
    boxes = [a for t, a in elements(html) if t == "input"]
    assert len(boxes) == 2
    assert all(a.get("type") == "checkbox" for a in boxes)
    assert any("checked" in a for a in boxes)


def test_footnotes_survive():
    html = parse_wiki_content("Text[^1].\n\n[^1]: The note.")
    assert 'class="footnote"' in html
    assert 'href="#fn:1"' in html


def test_images_survive_including_data_uri():
    html = parse_wiki_content("![alt](https://example.com/a.png)")
    assert '<img alt="alt" src="https://example.com/a.png">' in html
    data = "data:image/png;base64,iVBORw0KGgo="
    assert f'src="{data}"' in parse_wiki_content(f"![x]({data})")


def test_wiki_link_keeps_hover_card_attributes():
    html = parse_wiki_content("See [[Some Page]].")
    (anchor,) = [a for t, a in elements(html) if t == "a"]
    assert anchor["href"] == "/page/Some_Page"
    assert anchor["hx-get"] == "/api/pages/Some_Page/preview"
    assert anchor["hx-trigger"] == "mouseenter delay:250ms"
    assert anchor["hx-target"] == "#wiki-hover-card"
    assert anchor["hx-swap"] == "outerHTML"
    assert "wiki-link" in anchor["class"]


def test_only_the_exact_hover_card_hx_shape_is_kept():
    out = sanitize_html(
        '<a href="/x" hx-get="/api/pages/A/preview" hx-trigger="load" '
        'hx-target="body" hx-swap="beforeend">x</a>'
    )
    (anchor,) = [a for t, a in elements(out) if t == "a"]
    assert anchor.get("hx-get") == "/api/pages/A/preview"
    assert "hx-trigger" not in anchor
    assert "hx-target" not in anchor
    assert "hx-swap" not in anchor


def test_hx_get_path_traversal_is_dropped():
    out = sanitize_html('<a href="/x" hx-get="/api/pages/../factory/x/preview">x</a>')
    assert "hx-get" not in out


def test_external_link_keeps_target_and_rel():
    out = sanitize_html(
        '<a href="https://x.example" target="_blank" rel="noopener">x</a>'
    )
    assert 'target="_blank"' in out and 'rel="noopener"' in out


def test_target_other_than_blank_is_dropped():
    assert "target" not in sanitize_html('<a href="/x" target="_top">x</a>')


def test_epic_progress_style_survives_but_other_styles_do_not():
    ok = sanitize_html('<div class="epic-progress-fill" style="width:40%"></div>')
    assert 'style="width:40%"' in ok
    assert "style" not in sanitize_html('<div style="width:999%">x</div>')
    assert "style" not in sanitize_html('<div style="position:fixed;top:0">x</div>')
    assert "style" not in sanitize_html('<span style="width:10%">x</span>')


def test_macro_data_hooks_survive():
    table = sanitize_html(
        '<table><tr><td data-page="Home" data-field="status" data-editable="true" '
        'data-col="status">x</td></tr></table>'
    )
    (td,) = [a for t, a in elements(table) if t == "td"]
    assert td["data-page"] == "Home" and td["data-editable"] == "true"
    clock = sanitize_html('<span class="running-clock" data-clock data-timezone="UTC">')
    assert "data-clock" in clock and 'data-timezone="UTC"' in clock


def test_data_hooks_are_scoped_to_the_tags_that_use_them():
    # data-editable only means something on a table cell; elsewhere it's noise.
    assert "data-" not in sanitize_html('<p data-editable="true" data-page="x">t</p>')


def test_task_status_macro_output_survives():
    html = parse_wiki_content(
        "<<TaskStatus>>",
        page_name="Factory/Task_1",
        page_metadata={"type": "task", "status": "in_progress"},
    )
    (wrapper,) = [
        a for t, a in elements(html) if t == "div" and "data-terminal-page" in a
    ]
    assert wrapper["data-terminal-page"] == "Factory/Task_1"
    assert any(t == "button" for t, _ in elements(html))
    assert_inert(html)


def test_newpage_macro_output_survives():
    html = parse_wiki_content('<<NewPage(Tpl, "Make", Parent)>>')
    (button,) = [a for t, a in elements(html) if t == "button"]
    assert button["data-newpage-template"] == "Tpl"
    assert button["data-newpage-parent"] == "Parent"
    assert any(t == "input" for t, _ in elements(html))
    assert_inert(html)


def test_sanitize_is_idempotent():
    once = sanitize_html(parse_wiki_content("# T\n\n[[A]] **b** ![i](https://x/y.png)"))
    assert sanitize_html(once) == once


# ── 3. No inline script anywhere, and the CSP says so ─────────────────────────


@pytest.fixture(autouse=True)
def _patch_storage(tmp_path):
    original = meshwiki.main.storage.base_path
    meshwiki.main.storage.base_path = tmp_path
    yield
    meshwiki.main.storage.base_path = original


@pytest.fixture
async def client():
    transport = ASGITransport(app=meshwiki.main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _InlineScanner(HTMLParser):
    """Collect inline <script> bodies and ``on*=`` attributes."""

    def __init__(self):
        super().__init__()
        self.problems: list[str] = []
        self._in_inline_script = False

    def handle_starttag(self, tag, attrs):
        names = dict(attrs)
        if tag == "script" and "src" not in names:
            self._in_inline_script = True
        for name in names:
            if name.startswith("on"):
                self.problems.append(f"<{tag} {name}=…>")

    def handle_data(self, data):
        if self._in_inline_script and data.strip():
            self.problems.append(f"inline <script>: {data.strip()[:60]!r}")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_inline_script = False


def inline_code(html: str) -> list[str]:
    scanner = _InlineScanner()
    scanner.feed(html)
    return scanner.problems


HOSTILE_NAME = "x');alert(1);('"


@pytest.mark.asyncio
async def test_pages_contain_no_inline_script_or_handlers(client):
    storage = meshwiki.main.storage
    await storage.save_page("Plain", "# Plain\n\ntext [[Other]]")
    await storage.save_page(HOSTILE_NAME, "# Hostile name page")
    await storage.save_page(
        "TaskPage",
        "---\ntype: task\nstatus: in_progress\n---\n<<TaskStatus>>\n\n"
        '<<NewPage(Tpl, "Make")>>',
    )
    await storage.save_page(
        "Evil",
        "<script>alert(1)</script>\n\n"
        '<img src=x onerror="alert(1)">\n\n<button onclick="alert(1)">b</button>',
    )

    urls = [
        "/",
        "/page/Plain",
        "/page/TaskPage",
        "/page/Evil",
        "/page/Plain/edit",
        "/page/Plain/history",
        "/page/Plain/history/1",
        f"/page/{quote(HOSTILE_NAME)}",
        f"/page/{quote(HOSTILE_NAME)}/history",
        f"/page/{quote(HOSTILE_NAME)}/history/1",
        "/search?q=Plain",
        "/tags",
        "/graph",
        "/api/pages/TaskPage/fragment",
        "/api/pages/Evil/preview",
    ]
    for url in urls:
        resp = await client.get(url)
        assert resp.status_code == 200, url
        assert inline_code(resp.text) == [], url


@pytest.mark.asyncio
async def test_hostile_page_name_does_not_reach_a_js_context(client):
    """Names used in confirm() dialogs are data attributes, never evaluated JS."""
    await meshwiki.main.storage.save_page(HOSTILE_NAME, "body")
    for url in (
        f"/page/{quote(HOSTILE_NAME)}",
        f"/page/{quote(HOSTILE_NAME)}/history/1",
    ):
        resp = await client.get(url)
        assert "onsubmit" not in resp.text
        assert "data-confirm=" in resp.text


@pytest.mark.asyncio
async def test_evil_page_is_neutralised_in_every_render_path(client):
    await meshwiki.main.storage.save_page(
        "Evil",
        "# Evil\n\n<script>alert(1)</script>\n\n"
        '<img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))',
    )
    # Fragment and preview responses are user content only, so check them strictly.
    for url in ("/api/pages/Evil/fragment", "/api/pages/Evil/preview"):
        resp = await client.get(url)
        assert resp.status_code == 200
        assert_inert(resp.text)
    # The full page also contains the app's own wrapper markup (e.g. the page's
    # hx-get refresh element), so assert on the payloads rather than every tag.
    page = (await client.get("/page/Evil")).text
    assert "<script>alert(1)</script>" not in page
    assert "onerror" not in page
    assert 'href="javascript:' not in page
    resp = await client.post("/api/preview", data={"content": "<script>x</script>"})
    assert "<script" not in resp.text


@pytest.mark.asyncio
async def test_csp_forbids_inline_script(client):
    resp = await client.get("/health/live")
    csp = resp.headers["content-security-policy"]
    directives = {
        d.strip().split(" ", 1)[0]: d.strip() for d in csp.split(";") if d.strip()
    }
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert directives["object-src"] == "object-src 'none'"
    assert directives["base-uri"] == "base-uri 'self'"


def test_every_script_the_templates_reference_exists():
    """A renamed/missing static script would silently break page behaviour."""
    from pathlib import Path

    root = Path(meshwiki.__file__).parent
    referenced = set()
    for template in (root / "templates").rglob("*.html"):
        referenced.update(
            re.findall(r'<script[^>]+src="/static/(js/[^"]+)"', template.read_text())
        )
    assert referenced, "expected the templates to reference static scripts"
    for rel in sorted(referenced):
        assert (root / "static" / rel).is_file(), rel
