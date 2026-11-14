"""Plain-text excerpts of page content (for hover cards and similar previews)."""

from __future__ import annotations

import html
import re

import nh3

_FENCED_CODE = re.compile(r"(```|~~~).*?\1", re.S)
_MACRO = re.compile(r"<<[^>]*>>")
_WIKI_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LINE_MARKER = re.compile(r"^[ \t]{0,3}(?:#{1,6}|[-*+]|\d+\.|>)[ \t]+", re.M)
_INLINE_MARKUP = re.compile(r"(\*\*|__|~~|[*_`])")
_WHITESPACE = re.compile(r"\s+")


def make_excerpt(content: str, limit: int = 200) -> str:
    """Return a short plain-text summary of Markdown *content*.

    The result is text, not HTML: raw HTML is stripped (``<script>`` and
    ``<style>`` lose their content), entities are decoded and Markdown syntax is
    reduced to the words. Callers must still escape it when inserting into HTML
    (Jinja's autoescape does this), because decoding can reintroduce ``<``.

    Args:
        content: Markdown source (frontmatter already removed).
        limit: Maximum length of the excerpt, ellipsis included.

    Returns:
        The excerpt, cut at a word boundary and ending in ``…`` when truncated.
    """
    text = _FENCED_CODE.sub(" ", content)
    text = _MACRO.sub(" ", text)
    text = nh3.clean(text, tags=set(), clean_content_tags={"script", "style"})
    text = html.unescape(text)
    text = _WIKI_LINK.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _LINE_MARKER.sub("", text)
    text = _INLINE_MARKUP.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0] or text[: limit - 1]
    return cut.rstrip(" ,.;:-") + "…"
