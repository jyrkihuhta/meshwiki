"""Tests for plain-text excerpts used by hover-card previews."""

from meshwiki.core.excerpt import make_excerpt


def test_reduces_markdown_to_words():
    text = "# Title\n\nSome **bold** and _italic_ and `code`.\n\n- item one\n- item two"
    assert (
        make_excerpt(text) == "Title Some bold and italic and code. item one item two"
    )


def test_links_keep_their_text():
    assert make_excerpt("See [[Other Page|the other page]] and [[Plain]].") == (
        "See the other page and Plain."
    )
    assert make_excerpt("A [link](https://example.com/x) here") == "A link here"
    assert make_excerpt("![alt text](img.png)") == "alt text"


def test_macros_and_fenced_code_are_dropped():
    text = "Before <<TableOfContents>> middle\n```python\nsecret = 1\n```\nAfter"
    assert make_excerpt(text) == "Before middle After"


def test_raw_html_is_stripped_and_script_content_dropped():
    out = make_excerpt(
        '<script>alert(1)</script><b>bold</b><img src=x onerror="y()">ok'
    )
    assert out == "boldok"
    assert "alert" not in out and "onerror" not in out


def test_entities_are_decoded_to_text():
    # The result is *text*; the caller's autoescape re-escapes it for HTML.
    assert make_excerpt("5 &lt; 6 &amp; 7") == "5 < 6 & 7"


def test_collapses_whitespace():
    assert make_excerpt("a\n\n\n   b\t\tc") == "a b c"


def test_short_text_is_unchanged_and_long_text_is_cut_at_a_word():
    assert make_excerpt("short") == "short"
    out = make_excerpt("word " * 200, limit=50)
    assert len(out) <= 50
    assert out.endswith("…")
    assert not out.rstrip("…").endswith("wor")  # cut on a word boundary


def test_single_long_word_is_still_truncated():
    out = make_excerpt("x" * 500, limit=20)
    assert len(out) <= 20 and out.endswith("…")


def test_empty_content():
    assert make_excerpt("") == ""
    assert make_excerpt("<<Macro>>") == ""
