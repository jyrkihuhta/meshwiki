"""Unit tests for the Markdown parser and extensions."""

from meshwiki.core.parser import (
    create_parser,
    extract_wiki_links,
    parse_wiki_content,
    parse_wiki_content_with_toc,
    word_count,
)

# ============================================================
# Strikethrough extension
# ============================================================


class TestStrikethrough:
    def test_basic_strikethrough(self):
        html = parse_wiki_content("~~deleted~~")
        assert "<del>deleted</del>" in html

    def test_strikethrough_in_paragraph(self):
        html = parse_wiki_content("This is ~~removed~~ text.")
        assert "<del>removed</del>" in html
        assert "This is" in html
        assert "text." in html

    def test_strikethrough_multiple(self):
        html = parse_wiki_content("~~one~~ and ~~two~~")
        assert html.count("<del>") == 2


# ============================================================
# Wiki links
# ============================================================


class TestWikiLinks:
    def test_existing_page_link(self):
        html = parse_wiki_content("See [[HomePage]]", page_exists=lambda x: True)
        assert 'class="wiki-link"' in html
        assert 'href="/page/HomePage"' in html
        assert ">HomePage</a>" in html

    def test_missing_page_link(self):
        html = parse_wiki_content("See [[Missing]]", page_exists=lambda x: False)
        assert "wiki-link-missing" in html

    def test_display_text(self):
        html = parse_wiki_content("[[Page|Click Here]]", page_exists=lambda x: True)
        assert ">Click Here</a>" in html
        assert 'href="/page/Page"' in html

    def test_spaces_in_name(self):
        html = parse_wiki_content("[[My Page]]", page_exists=lambda x: True)
        assert 'href="/page/My_Page"' in html
        assert ">My Page</a>" in html

    def test_multiple_links_on_one_line(self):
        html = parse_wiki_content(
            "See [[Page1]] and [[Page2]]", page_exists=lambda x: True
        )
        assert 'href="/page/Page1"' in html
        assert 'href="/page/Page2"' in html

    def test_default_page_exists(self):
        """Without page_exists callback, links default to existing style."""
        html = parse_wiki_content("[[SomePage]]")
        assert "wiki-link-missing" not in html
        assert "wiki-link" in html


# ============================================================
# extract_wiki_links
# ============================================================


class TestExtractWikiLinks:
    def test_basic(self):
        links = extract_wiki_links("See [[HomePage]] for info.")
        assert links == ["HomePage"]

    def test_with_display_text(self):
        links = extract_wiki_links("[[Page|Display]]")
        assert links == ["Page"]

    def test_empty_content(self):
        assert extract_wiki_links("No links here.") == []

    def test_multiple_links(self):
        links = extract_wiki_links("[[A]], [[B]], [[C]]")
        assert links == ["A", "B", "C"]

    def test_strips_whitespace(self):
        links = extract_wiki_links("[[ Spaced ]]")
        assert links == ["Spaced"]


# ============================================================
# Full parser (end-to-end)
# ============================================================


class TestParseWikiContent:
    def test_markdown_headings(self):
        html = parse_wiki_content("# Title\n\n## Subtitle")
        assert "<h1" in html
        assert "Title</h1>" in html
        assert "<h2" in html
        assert "Subtitle</h2>" in html

    def test_markdown_bold_italic(self):
        html = parse_wiki_content("**bold** and *italic*")
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html

    def test_code_block(self):
        html = parse_wiki_content("```python\nprint('hi')\n```")
        assert "<code" in html
        assert "print" in html

    def test_task_list(self):
        html = parse_wiki_content("- [ ] todo\n- [x] done")
        assert 'type="checkbox"' in html

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = parse_wiki_content(md)
        assert "<table>" in html
        assert "<td>1</td>" in html

    def test_combined_extensions(self):
        """Multiple custom extensions work together."""
        md = "~~old~~ see [[NewPage]]"
        html = parse_wiki_content(md, page_exists=lambda x: True)
        assert "<del>old</del>" in html
        assert "wiki-link" in html


# ============================================================
# create_parser
# ============================================================


class TestCreateParser:
    def test_returns_markdown_instance(self):
        from markdown import Markdown

        parser = create_parser()
        assert isinstance(parser, Markdown)

    def test_custom_page_exists(self):
        """Parser respects the page_exists callback."""
        parser = create_parser(page_exists=lambda x: False)
        html = parser.convert("[[Test]]")
        assert "wiki-link-missing" in html


# ============================================================
# parse_wiki_content_with_toc
# ============================================================


class TestParseWikiContentWithToc:
    def test_returns_tuple(self):
        html, toc = parse_wiki_content_with_toc("# Hello")
        assert isinstance(html, str)
        assert isinstance(toc, str)

    def test_toc_contains_headings(self):
        content = "# First\n\n## Second\n\n### Third"
        html, toc = parse_wiki_content_with_toc(content)
        assert "First" in toc
        assert "Second" in toc
        assert "Third" in toc
        assert "<li>" in toc

    def test_toc_empty_without_headings(self):
        html, toc = parse_wiki_content_with_toc("Just a paragraph.")
        # No headings means TOC is empty or has no list items
        assert "<li>" not in toc

    def test_html_still_correct(self):
        content = "# Title\n\nSome **bold** text."
        html, toc = parse_wiki_content_with_toc(content)
        assert "<strong>bold</strong>" in html
        assert "Title" in html

    def test_wiki_links_in_toc_content(self):
        content = "# Heading\n\n[[SomePage]] is linked."
        html, toc = parse_wiki_content_with_toc(content, page_exists=lambda x: True)
        assert "wiki-link" in html
        assert "Heading" in toc

    def test_multiple_headings_generate_toc(self):
        content = "## A\n\n## B\n\n## C"
        html, toc = parse_wiki_content_with_toc(content)
        assert toc.count("<li>") >= 3


# ============================================================
# MetaTable inside code blocks
# ============================================================


class TestMetaTableInCodeBlock:
    def test_metatable_not_rendered_in_fenced_code(self):
        """MetaTable macro inside ``` should be shown as literal text."""
        content = "```\n<<MetaTable(status=draft, ||name||status||)>>\n```"
        html = parse_wiki_content(content)
        assert "<table" not in html
        assert "MetaTable" in html

    def test_metatable_not_rendered_in_tilde_code(self):
        """MetaTable macro inside ~~~ should be shown as literal text."""
        content = "~~~\n<<MetaTable(||name||)>>\n~~~"
        html = parse_wiki_content(content)
        assert "<table" not in html
        assert "MetaTable" in html


# ============================================================
# word_count
# ============================================================


class TestWordCount:
    def test_normal_paragraph(self):
        assert word_count("Hello world this is a test") == 6

    def test_empty_content(self):
        assert word_count("") == 0
        assert word_count("   ") == 0

    def test_content_with_only_frontmatter(self):
        content = """---
title: Test Page
tags: [test, sample]
---
"""
        assert word_count(content) == 0

    def test_content_with_frontmatter_and_body(self):
        content = """---
title: Test Page
tags: [test, sample]
---
This is the body content.
"""
        assert word_count(content) == 5

    def test_content_with_code_blocks(self):
        content = """---
title: Test
---
This is text.

```python
def hello():
    print("hello world")
```

More text here.
"""
        assert word_count(content) == 12

    def test_content_with_wiki_links(self):
        content = """---
title: Test
---
See [[PageOne]] and [[PageTwo]] for details.
"""
        assert word_count(content) == 6

    def test_frontmatter_stripped(self):
        content = """---
created: '2026-01-01'
modified: '2026-01-02'
---
This is body text with five words.
"""
        assert word_count(content) == 7
