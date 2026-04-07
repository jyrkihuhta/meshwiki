"""Tests for CalloutBlockPreprocessor and CalloutExtension."""

import pytest

from meshwiki.core.parser import (
    CALLOUT_ICONS,
    CALLOUT_TYPES,
    parse_wiki_content,
)


class TestCalloutBlockPreprocessor:
    """Unit tests for CalloutBlockPreprocessor using full Markdown conversion."""

    @pytest.mark.parametrize("callout_type", CALLOUT_TYPES)
    def test_each_type_renders_correct_class_and_icon(self, callout_type):
        """Each of the 5 types renders correct class and icon."""
        content = f"""```{callout_type}
This is the callout body
with multiple lines
```"""
        html = parse_wiki_content(content)
        assert f'class="callout callout--{callout_type}"' in html
        assert (
            f'<span class="callout__icon">{CALLOUT_ICONS[callout_type]}</span>' in html
        )
        assert "This is the callout body" in html

    def test_regular_code_block_untouched(self):
        """A regular code block (e.g. ```python) is not affected."""
        content = """```python
def hello():
    print("world")
```"""
        html = parse_wiki_content(content)
        assert '<code class="language-python"' in html or "<code>" in html
        assert "def hello():" in html
        assert "print" in html
        assert 'class="callout' not in html

    def test_tilde_fence_callout(self):
        """Callout blocks using ~~~ fence are also detected."""
        content = """~~~info
This is a tilde callout
~~~"""
        html = parse_wiki_content(content)
        assert 'class="callout callout--info"' in html
        assert '<span class="callout__icon">ℹ️</span>' in html

    def test_multiple_callouts_same_page(self):
        """Multiple callouts on the same page all render correctly."""
        content = """```info
First callout
```

Some text between

```warning
Second callout
```

```tip
Third callout
```"""
        html = parse_wiki_content(content)
        assert 'class="callout callout--info"' in html
        assert "First callout" in html
        assert 'class="callout callout--warning"' in html
        assert "Second callout" in html
        assert 'class="callout callout--tip"' in html
        assert "Third callout" in html

    def test_unclosed_fence_passed_through(self):
        """An unclosed callout fence is passed through unchanged."""
        content = """```info
This callout is never closed"""
        html = parse_wiki_content(content)
        assert 'class="callout' not in html

    def test_html_escaping_in_body(self):
        """HTML special characters in callout body are escaped."""
        content = """```info
Use <script>alert('xss')</script> and &amp;
```"""
        html = parse_wiki_content(content)
        assert "&lt;script&gt;" in html
        assert "&amp;amp;" in html
        assert 'class="callout callout--info"' in html

    def test_empty_callout_body(self):
        """An empty callout body renders correctly."""
        content = """```note
```"""
        html = parse_wiki_content(content)
        assert 'class="callout callout--note"' in html
        assert '<span class="callout__icon">📝</span>' in html

    def test_code_block_not_callout_type(self):
        """Language tags that are not callout types are not intercepted."""
        content = """```javascript
console.log('hello');
```"""
        html = parse_wiki_content(content)
        assert 'class="callout' not in html
        assert "console.log" in html

    def test_nested_code_like_in_body(self):
        """Content that looks like a fence inside the callout body is preserved."""
        content = """```info
Here is some ``` code
```"""
        html = parse_wiki_content(content)
        assert 'class="callout callout--info"' in html
        assert "Here is some ``` code" in html


class TestCalloutIntegration:
    """Integration tests for callout blocks via HTTP route."""

    @pytest.mark.asyncio
    async def test_callout_via_http_route(self, client):
        """Callout inside a page renders correctly via the HTTP route."""
        page_content = """
```info
This is an info callout
```

Some text

```warning
This is a warning callout
```
"""
        resp = await client.post("/page/CalloutTest", data={"content": page_content})
        assert resp.status_code == 200

        resp = await client.get("/page/CalloutTest")
        assert resp.status_code == 200
        html = resp.text
        assert 'class="callout callout--info"' in html
        assert 'class="callout callout--warning"' in html
        assert "This is an info callout" in html
        assert "This is a warning callout" in html


class TestCalloutCSS:
    """Tests for callout CSS variables in style.css."""

    def test_dark_mode_css_variables_present(self):
        """Dark mode CSS variables are present in style.css."""
        import os

        style_css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "style.css",
        )
        with open(style_css_path, "r") as f:
            css = f.read()

        assert '[data-theme="dark"] .callout--info' in css
        assert "--callout-info-bg:" in css
        assert "--callout-info-border:" in css
        assert "--callout-info-color:" in css

        assert '[data-theme="dark"] .callout--warning' in css
        assert '[data-theme="dark"] .callout--tip' in css
        assert '[data-theme="dark"] .callout--error' in css
        assert '[data-theme="dark"] .callout--note' in css

    def test_light_mode_css_variables_present(self):
        """Light mode CSS variables are present in style.css."""
        import os

        style_css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "style.css",
        )
        with open(style_css_path, "r") as f:
            css = f.read()

        assert ".callout--info" in css
        assert "--callout-info-bg:" in css
        assert ".callout--warning" in css
        assert ".callout--tip" in css
        assert ".callout--error" in css
        assert ".callout--note" in css

    def test_callout_base_styles_present(self):
        """Base .callout and .callout__icon, .callout__body styles are present."""
        import os

        style_css_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "meshwiki",
            "static",
            "css",
            "style.css",
        )
        with open(style_css_path, "r") as f:
            css = f.read()

        assert ".callout {" in css
        assert ".callout__icon {" in css
        assert ".callout__body {" in css