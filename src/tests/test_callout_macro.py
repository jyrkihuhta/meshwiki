"""Unit tests for the CalloutBlockPreprocessor feature.

Tests fenced code blocks with callout language tags (```info, ```warning, etc.)
are rendered as styled callout boxes with appropriate icons.

Depends on: TASK001 (CalloutBlockPreprocessor implementation)
"""

import re
from pathlib import Path

import pytest

from meshwiki.core.parser import create_parser


CALLOUT_TYPES = {
    "info": "ℹ️",
    "warning": "⚠️",
    "tip": "💡",
    "error": "❌",
    "note": "📝",
}


@pytest.fixture(scope="module")
def css_content() -> str:
    css_path = (
        Path(__file__).parent.parent / "meshwiki" / "static" / "css" / "style.css"
    )
    return css_path.read_text()


def test_info_callout_renders() -> None:
    """```info\n...\n``` renders as a callout with info styling."""
    html = create_parser().convert("```info\nThis is an info message.\n```")
    assert 'class="callout callout--info"' in html
    assert "ℹ️" in html
    assert "This is an info message" in html


def test_warning_callout_renders() -> None:
    """```warning\n...\n``` renders as a callout with warning styling."""
    html = create_parser().convert("```warning\nWatch out!\n```")
    assert 'class="callout callout--warning"' in html
    assert "⚠️" in html
    assert "Watch out" in html


def test_tip_callout_renders() -> None:
    """```tip\n...\n``` renders as a callout with tip styling."""
    html = create_parser().convert("```tip\nPro tip: use the force.\n```")
    assert 'class="callout callout--tip"' in html
    assert "💡" in html
    assert "Pro tip: use the force" in html


def test_error_callout_renders() -> None:
    """```error\n...\n``` renders as a callout with error styling."""
    html = create_parser().convert("```error\nSomething went wrong.\n```")
    assert 'class="callout callout--error"' in html
    assert "❌" in html
    assert "Something went wrong" in html


def test_note_callout_renders() -> None:
    """```note\n...\n``` renders as a callout with note styling."""
    html = create_parser().convert("```note\nRemember this.\n```")
    assert 'class="callout callout--note"' in html
    assert "📝" in html
    assert "Remember this" in html


def test_python_code_block_not_affected() -> None:
    """```python\n...\n``` renders as a normal code block, not a callout."""
    html = create_parser().convert('```python\nprint("hello")\n```')
    assert 'class="callout"' not in html
    assert "<code" in html


def test_javascript_code_block_not_affected() -> None:
    """```javascript\n...\n``` renders as a normal code block."""
    html = create_parser().convert("```javascript\nconst x = 1;\n```")
    assert 'class="callout"' not in html
    assert "<code" in html


def test_generic_fence_not_affected() -> None:
    """```text\n...\n``` renders as a normal code block."""
    html = create_parser().convert("```text\nSome text content.\n```")
    assert 'class="callout"' not in html
    assert "Some text content" in html


def test_multiple_different_callout_types() -> None:
    """Multiple callout blocks of different types appear in correct order."""
    content = "\n\n".join(
        [
            "```info\nFirst info.\n```",
            "```warning\nThen warning.\n```",
            "```tip\nFinally tip.\n```",
        ]
    )
    html = create_parser().convert(content)

    info_pos = html.find("callout--info")
    warning_pos = html.find("callout--warning")
    tip_pos = html.find("callout--tip")

    assert info_pos != -1, "Info callout not found"
    assert warning_pos != -1, "Warning callout not found"
    assert tip_pos != -1, "Tip callout not found"
    assert info_pos < warning_pos < tip_pos, "Callouts not in correct order"


def test_multiple_same_callout_type() -> None:
    """Multiple callout blocks of the same type both render."""
    content = "\n\n".join(
        [
            "```info\nFirst info message.\n```",
            "```info\nSecond info message.\n```",
        ]
    )
    html = create_parser().convert(content)
    assert html.count("callout--info") == 2
    assert "First info message" in html
    assert "Second info message" in html


def test_script_tag_escaped() -> None:
    """<script>alert(1)</script> in callout body is HTML-escaped."""
    html = create_parser().convert("```warning\n<script>alert(1)</script>\n```")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_tags_escaped() -> None:
    """Raw HTML tags in callout body are escaped."""
    html = create_parser().convert("```error\n<div>hello</div>\n```")
    assert "<div>hello</div>" not in html
    assert "&lt;div&gt;hello&lt;/div&gt;" in html


def test_tilde_warning_callout() -> None:
    """~~~warning\n...\n~~~ renders as a callout."""
    html = create_parser().convert("~~~warning\nWatch out!\n~~~")
    assert 'class="callout callout--warning"' in html
    assert "⚠️" in html
    assert "Watch out" in html


def test_tilde_info_callout() -> None:
    """~~~info\n...\n~~~ renders as a callout."""
    html = create_parser().convert("~~~info\nInfo message.\n~~~")
    assert 'class="callout callout--info"' in html
    assert "ℹ️" in html


def test_tilde_tip_callout() -> None:
    """~~~tip\n...\n~~~ renders as a callout."""
    html = create_parser().convert("~~~tip\nPro tip.\n~~~")
    assert 'class="callout callout--tip"' in html
    assert "💡" in html


def test_dark_mode_support(css_content: str) -> None:
    """style.css contains [data-theme="dark"] for dark mode."""
    assert '[data-theme="dark"]' in css_content


def test_callout_info_bg_css_variable(css_content: str) -> None:
    """style.css contains --callout-info-bg custom property."""
    assert "--callout-info-bg" in css_content


def test_callout_warning_bg_css_variable(css_content: str) -> None:
    """style.css contains --callout-warning-bg custom property."""
    assert "--callout-warning-bg" in css_content


def test_callout_tip_bg_css_variable(css_content: str) -> None:
    """style.css contains --callout-tip-bg custom property."""
    assert "--callout-tip-bg" in css_content


def test_callout_error_bg_css_variable(css_content: str) -> None:
    """style.css contains --callout-error-bg custom property."""
    assert "--callout-error-bg" in css_content


def test_callout_note_bg_css_variable(css_content: str) -> None:
    """style.css contains --callout-note-bg custom property."""
    assert "--callout-note-bg" in css_content


def test_callout_info_css_class(css_content: str) -> None:
    """style.css contains .callout--info class."""
    assert ".callout--info" in css_content


def test_callout_warning_css_class(css_content: str) -> None:
    """style.css contains .callout--warning class."""
    assert ".callout--warning" in css_content


def test_callout_tip_css_class(css_content: str) -> None:
    """style.css contains .callout--tip class."""
    assert ".callout--tip" in css_content


def test_callout_error_css_class(css_content: str) -> None:
    """style.css contains .callout--error class."""
    assert ".callout--error" in css_content


def test_callout_note_css_class(css_content: str) -> None:
    """style.css contains .callout--note class."""
    assert ".callout--note" in css_content
