"""Tests for CalloutBlockPreprocessor and CalloutExtension."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from meshwiki.core.parser import (
    CALLOUT_ICONS,
    CALLOUT_TYPES,
    create_parser,
)

# TASK001 exports this as create_parser(); spec referred to it as make_parser()
make_parser = create_parser


# ============================================================
# Unit tests
# ============================================================


class TestCalloutBlockPreprocessor:
    """Unit tests for CalloutBlockPreprocessor using full Markdown conversion."""

    @pytest.mark.parametrize("callout_type", CALLOUT_TYPES)
    def test_each_type_renders_correct_class_and_icon(self, callout_type):
        """Each of the 5 types renders correct class and icon."""
        content = f"""```{callout_type}
This is the callout body
with multiple lines
```"""
        html = make_parser().convert(content)
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
        html = make_parser().convert(content)
        assert '<code class="language-python"' in html or "<code>" in html
        assert "def hello():" in html
        assert "print" in html
        assert 'class="callout' not in html

    def test_tilde_fence_callout(self):
        """Callout blocks using ~~~ fence are also detected."""
        content = """~~~info
This is a tilde callout
~~~"""
        html = make_parser().convert(content)
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
        html = make_parser().convert(content)
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
        html = make_parser().convert(content)
        assert 'class="callout' not in html

    def test_html_escaping_in_body(self):
        """HTML special characters in callout body are escaped."""
        content = """```info
Use <script>alert('xss')</script> and &amp;
```"""
        html = make_parser().convert(content)
        assert "&lt;script&gt;" in html
        assert "&amp;amp;" in html
        assert 'class="callout callout--info"' in html

    def test_empty_callout_body(self):
        """An empty callout body renders correctly."""
        content = """```note
```"""
        html = make_parser().convert(content)
        assert 'class="callout callout--note"' in html
        assert '<span class="callout__icon">📝</span>' in html

    def test_code_block_not_callout_type(self):
        """Language tags that are not callout types are not intercepted."""
        content = """```javascript
console.log('hello');
```"""
        html = make_parser().convert(content)
        assert 'class="callout' not in html
        assert "console.log" in html

    def test_nested_code_like_in_body(self):
        """Content that looks like a fence inside the callout body is preserved."""
        content = """```info
Here is some ``` code
```"""
        html = make_parser().convert(content)
        assert 'class="callout callout--info"' in html
        assert "Here is some ``` code" in html


# ============================================================
# Integration tests (synchronous)
# ============================================================


def test_warning_callout_via_http(wiki_app, tmp_path):
    """A warning callout inside a wiki page renders correctly via the HTTP route."""
    content = """```warning
This is a warning message.
```"""
    page_path = tmp_path / "CalloutTestPage.md"
    page_path.write_text(content)

    with TestClient(wiki_app, base_url="http://test") as client:
        resp = client.get("/page/CalloutTestPage")
        assert resp.status_code == 200
        assert "callout--warning" in resp.text


# ============================================================
# CSS presence tests
# ============================================================


@pytest.fixture(scope="module")
def css_content() -> str:
    """Read style.css once for the entire test module."""
    css_path = (
        Path(__file__).parent.parent / "meshwiki" / "static" / "css" / "style.css"
    )
    return css_path.read_text()


def test_dark_mode_support(css_content: str) -> None:
    """Dark mode CSS variables are present in style.css."""
    assert '[data-theme="dark"]' in css_content


def test_callout_info_css(css_content: str) -> None:
    """CSS custom properties for info callout are present."""
    assert "--callout-info-bg:" in css_content
    assert ".callout--info" in css_content


def test_callout_warning_css(css_content: str) -> None:
    """CSS custom properties for warning callout are present."""
    assert "--callout-warning-bg:" in css_content
    assert ".callout--warning" in css_content


def test_callout_tip_css(css_content: str) -> None:
    """CSS custom properties for tip callout are present."""
    assert "--callout-tip-bg:" in css_content
    assert ".callout--tip" in css_content


def test_callout_error_css(css_content: str) -> None:
    """CSS custom properties for error callout are present."""
    assert "--callout-error-bg:" in css_content
    assert ".callout--error" in css_content


def test_callout_note_css(css_content: str) -> None:
    """CSS custom properties for note callout are present."""
    assert "--callout-note-bg:" in css_content
    assert ".callout--note" in css_content
