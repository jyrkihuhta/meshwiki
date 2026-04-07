"""Tests for CalloutBlockPreprocessor and CalloutExtension."""

import re  # noqa: F401  # required by project style
from pathlib import Path

import pytest

# TASK001 exports this as create_parser(); spec referred to it as make_parser()
from meshwiki.core.parser import (
    CALLOUT_ICONS,
    CALLOUT_TYPES,
)
from meshwiki.core.parser import create_parser as make_parser

# ---------------------------------------------------------------------------
# Unit tests - CalloutBlockPreprocessor via make_parser().convert()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("callout_type", CALLOUT_TYPES)
def test_each_type_renders_correct_class_and_icon(callout_type: str) -> None:
    """Each of the 5 types renders correct class, icon, and body."""
    content = f"""```{callout_type}
This is the callout body
with multiple lines
```"""
    html = make_parser().convert(content)
    assert f'class="callout callout--{callout_type}"' in html
    assert f'<span class="callout__icon">{CALLOUT_ICONS[callout_type]}</span>' in html
    assert "This is the callout body" in html


def test_info_callout_renders() -> None:
    """```info\\n...``` renders as a callout with info styling."""
    html = make_parser().convert("```info\nThis is an info message.\n```")
    assert 'class="callout callout--info"' in html
    assert "ℹ️" in html
    assert "This is an info message" in html


def test_warning_callout_renders() -> None:
    """```warning\\n...``` renders as a callout with warning styling."""
    html = make_parser().convert("```warning\nThis is a warning message.\n```")
    assert 'class="callout callout--warning"' in html
    assert "⚠️" in html
    assert "This is a warning message" in html


def test_tip_callout_renders() -> None:
    """```tip\\n...``` renders as a callout with tip styling."""
    html = make_parser().convert("```tip\nThis is a tip.\n```")
    assert 'class="callout callout--tip"' in html
    assert "💡" in html
    assert "This is a tip" in html


def test_error_callout_renders() -> None:
    """```error\\n...``` renders as a callout with error styling."""
    html = make_parser().convert("```error\nThis is an error.\n```")
    assert 'class="callout callout--error"' in html
    assert "❌" in html
    assert "This is an error" in html


def test_note_callout_renders() -> None:
    """```note\\n...``` renders as a callout with note styling."""
    html = make_parser().convert("```note\nThis is a note.\n```")
    assert 'class="callout callout--note"' in html
    assert "📝" in html
    assert "This is a note" in html


def test_python_code_block_not_affected() -> None:
    """A regular ```python code block is not affected by callout processing."""
    content = """```python
def hello():
    print("hello")
```"""
    html = make_parser().convert(content)
    assert 'class="callout"' not in html
    assert "<code" in html
    assert "def hello():" in html


def test_javascript_code_block_not_affected() -> None:
    """A regular ```javascript code block is not affected by callout processing."""
    content = """```javascript
console.log('hello');
```"""
    html = make_parser().convert(content)
    assert 'class="callout"' not in html
    assert "<code" in html
    assert "console.log" in html


def test_multiple_callouts_on_one_page() -> None:
    """Multiple callout blocks of different types all render in order."""
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
    idx_info = html.find('class="callout callout--info"')
    idx_warning = html.find('class="callout callout--warning"')
    idx_tip = html.find('class="callout callout--tip"')
    assert idx_info < idx_warning < idx_tip, "Callouts do not appear in correct order"
    assert "First callout" in html
    assert "Second callout" in html
    assert "Third callout" in html


def test_html_escaping_in_callout_body() -> None:
    """HTML in callout body is escaped to prevent XSS."""
    content = """```info
Use <script>alert(1)</script>
```"""
    html = make_parser().convert(content)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert 'class="callout callout--info"' in html


def test_tilde_fence_style() -> None:
    """Callout blocks using ~~~ fence render correctly."""
    content = """~~~warning
Watch out!
~~~"""
    html = make_parser().convert(content)
    assert 'class="callout callout--warning"' in html
    assert "⚠️" in html
    assert "Watch out!" in html


def test_unclosed_fence_passed_through() -> None:
    """An unclosed fence does not render as a callout."""
    content = """```info
This callout is never closed"""
    html = make_parser().convert(content)
    assert 'class="callout' not in html


def test_empty_callout_body() -> None:
    """An empty callout body renders with the correct structure."""
    content = """```note
```"""
    html = make_parser().convert(content)
    assert 'class="callout callout--note"' in html
    assert '<span class="callout__icon">📝</span>' in html


def test_nested_code_like_in_body() -> None:
    """Content that looks like a fence inside callout body is preserved as text."""
    content = """```info
Here is some ``` code
```"""
    html = make_parser().convert(content)
    assert 'class="callout callout--info"' in html
    assert "Here is some ``` code" in html


# ---------------------------------------------------------------------------
# CSS presence tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def css_content() -> str:
    """Read style.css once for the entire test module."""
    css_path = (
        Path(__file__).parent.parent / "meshwiki" / "static" / "css" / "style.css"
    )
    return css_path.read_text()


def test_dark_mode_support(css_content: str) -> None:
    """style.css contains dark mode support for callouts."""
    assert '[data-theme="dark"]' in css_content


def test_callout_info_css(css_content: str) -> None:
    """style.css contains .callout--info and --callout-info-* variables."""
    assert ".callout--info" in css_content
    assert "--callout-info-bg" in css_content


def test_callout_warning_css(css_content: str) -> None:
    """style.css contains .callout--warning and --callout-warning-* variables."""
    assert ".callout--warning" in css_content
    assert "--callout-warning-bg" in css_content


def test_callout_tip_css(css_content: str) -> None:
    """style.css contains .callout--tip and --callout-tip-* variables."""
    assert ".callout--tip" in css_content
    assert "--callout-tip-bg" in css_content


def test_callout_error_css(css_content: str) -> None:
    """style.css contains .callout--error and --callout-error-* variables."""
    assert ".callout--error" in css_content
    assert "--callout-error-bg" in css_content


def test_callout_note_css(css_content: str) -> None:
    """style.css contains .callout--note and --callout-note-* variables."""
    assert ".callout--note" in css_content
    assert "--callout-note-bg" in css_content
