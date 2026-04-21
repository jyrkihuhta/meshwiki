"""Unit tests for the <<TableOfContents>> macro."""

import markdown as md_lib

from meshwiki.core.parser import parse_markdown


def _make_toc_html(source: str) -> str:
    """Helper: generate toc_html the same way the route handler would."""
    md = md_lib.Markdown(extensions=["toc"])
    md.convert(source)
    return getattr(md, "toc", "") or ""


class TestTableOfContentsMacro:
    """Test suite for <<TableOfContents>> macro."""

    def test_toc_macro_renders_nav_block(self) -> None:
        """<<TableOfContents>> on a page with h1/h2/h3 headings renders nav block."""
        source = (
            "# Heading One\n## Heading Two\n### Heading Three\n\n<<TableOfContents>>"
        )
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert '<nav class="wiki-toc wiki-toc-inline">' in result
        assert "heading-one" in result.lower() or "heading one" in result.lower()

    def test_toc_macro_depth_2_excludes_h3(self) -> None:
        """<<TableOfContents(depth=2)>> excludes h3 anchors from nav."""
        source = "# H1\n## H2\n### H3\n\n<<TableOfContents(depth=2)>>"
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        nav_content = (
            result.split('<nav class="wiki-toc wiki-toc-inline">')[1]
            .split("</nav>")[0]
            .lower()
        )
        assert "h3" not in nav_content

    def test_toc_macro_depth_1_includes_only_h1(self) -> None:
        """<<TableOfContents(depth=1)>> includes only h1 anchor; h2 absent."""
        source = "# H1\n## H2\n### H3\n\n<<TableOfContents(depth=1)>>"
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        nav_content = (
            result.split('<nav class="wiki-toc wiki-toc-inline">')[1]
            .split("</nav>")[0]
            .lower()
        )
        assert "h2" not in nav_content
        assert "h1" in nav_content or "h-1" in nav_content

    def test_toc_macro_no_headings_renders_empty(self) -> None:
        """Page with no headings: macro renders to empty string (no <nav> tag)."""
        source = "Just some paragraph text.\n\n<<TableOfContents>>"
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert '<nav class="wiki-toc wiki-toc-inline">' not in result

    def test_no_macro_no_nav_injected(self) -> None:
        """Page with no <<TableOfContents>> macro: output unchanged, no nav injected."""
        source = "# Heading\n\nSome content."
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert '<nav class="wiki-toc wiki-toc-inline">' not in result

    def test_toc_macro_default_depth_with_deep_nesting(self) -> None:
        """<<TableOfContents>> with default depth on page with deeply nested headings."""
        source = (
            "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n\n<<TableOfContents>>"
        )
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        nav_content = (
            result.split('<nav class="wiki-toc wiki-toc-inline">')[1]
            .split("</nav>")[0]
            .lower()
        )
        assert "h1" in nav_content or "h-1" in nav_content
        assert "h2" in nav_content or "h-2" in nav_content
        assert "h3" in nav_content or "h-3" in nav_content
        assert "h4" in nav_content or "h-4" in nav_content
        assert "h5" in nav_content or "h-5" in nav_content
        assert "h6" in nav_content or "h-6" in nav_content

    def test_toc_macro_empty_toc_html(self) -> None:
        """When toc_html is empty string, macro renders nothing."""
        source = "# Heading\n\n<<TableOfContents>>"
        result = parse_markdown(source, toc_html="")
        assert '<nav class="wiki-toc wiki-toc-inline">' not in result

    def test_toc_macro_multiple_instances(self) -> None:
        """Multiple <<TableOfContents>> macros on same page all render."""
        source = (
            "# Heading\n\n<<TableOfContents>>\n\nSome content\n\n<<TableOfContents>>"
        )
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert result.count('<nav class="wiki-toc wiki-toc-inline">') == 2

    def test_toc_macro_with_wiki_links(self) -> None:
        """<<TableOfContents>> works alongside wiki links."""
        source = "# Main Topic\n\n[[RelatedPage]]\n\n## Subtopic\n\n<<TableOfContents>>"
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert '<nav class="wiki-toc wiki-toc-inline">' in result
        assert "wiki-link" in result or "RelatedPage" in result

    def test_toc_macro_inside_code_block_not_replaced(self) -> None:
        """<<TableOfContents>> inside fenced code block is not processed as macro."""
        source = "# Heading\n\n```\n<<TableOfContents>>\n```\n\n<<TableOfContents>>"
        toc_html = _make_toc_html(source)
        result = parse_markdown(source, toc_html=toc_html)
        assert '<nav class="wiki-toc wiki-toc-inline">' in result
        assert result.count("<nav") == 1
