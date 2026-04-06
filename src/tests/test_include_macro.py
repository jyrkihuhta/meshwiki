"""Unit tests for the <<Include(...)>> macro."""

from meshwiki.core.parser import parse_wiki_content


def render(
    text: str, page_contents: dict[str, str] | None = None, current_page: str = ""
) -> str:
    """Render text through the parser with page contents."""
    return parse_wiki_content(
        text, page_contents=page_contents or {}, page_name=current_page
    )


class TestIncludeBasic:
    """Basic include rendering tests."""

    def test_basic_include(self):
        """<<Include(PageName)>> embeds the rendered content of PageName."""
        page_contents = {
            "TargetPage": "# Hello World\n\nThis is the target page content.",
        }
        html = render(
            "Before\n\n<<Include(TargetPage)>>\n\nAfter", page_contents=page_contents
        )
        assert "include-content" in html
        assert "Hello World" in html
        assert "This is the target page content" in html

    def test_missing_page_shows_placeholder(self):
        """<<Include(MissingPage)>> when page doesn't exist shows placeholder."""
        html = render("<<Include(MissingPage)>>", page_contents={})
        assert "include-missing" in html
        assert "[[MissingPage]]" in html

    def test_include_multiple_pages(self):
        """Multiple <<Include(...)>> macros work in the same document."""
        page_contents = {
            "PageA": "# Content A",
            "PageB": "# Content B",
        }
        html = render(
            "<<Include(PageA)>>\n\n<<Include(PageB)>>", page_contents=page_contents
        )
        assert html.count("include-content") == 2
        assert "Content A" in html
        assert "Content B" in html


class TestIncludeWithHeading:
    """Tests for <<Include(PageName, "Heading", level)>> syntax."""

    def test_heading_with_level(self):
        """<<Include(PageName, "Title", 2)>> prepends an H2 heading."""
        page_contents = {
            "Docs/Guide": "This is the guide content.",
        }
        html = render(
            '<<Include(Docs/Guide, "Custom Title", 2)>>',
            page_contents=page_contents,
        )
        assert "<h2>Custom Title</h2>" in html
        assert "This is the guide content" in html

    def test_heading_level_1_to_6(self):
        """Heading levels 1-6 are respected."""
        page_contents = {"Page": "Content"}
        for level in range(1, 7):
            html = render(
                f'<<Include(Page, "Title", {level})>>',
                page_contents=page_contents,
            )
            assert f"<h{level}>Title</h{level}>" in html

    def test_heading_defaults_to_page_name_when_level_only(self):
        """<<Include(PageName, , 3)>> uses page name as heading text."""
        page_contents = {"MyPage": "Content here."}
        html = render("<<Include(MyPage, , 3)>>", page_contents=page_contents)
        assert "<h3>MyPage</h3>" in html
        assert "Content here" in html

    def test_heading_defaults_to_level_2_when_text_only(self):
        """<<Include(PageName, "Title")>> uses level 2 by default."""
        page_contents = {"MyPage": "Content here."}
        html = render('<<Include(MyPage, "Title")>>', page_contents=page_contents)
        assert "<h2>Title</h2>" in html
        assert "Content here" in html

    def test_level_clamped_to_1_6(self):
        """Levels outside 1-6 are clamped."""
        page_contents = {"Page": "Content"}
        html = render('<<Include(Page, "Title", 10)>>', page_contents=page_contents)
        assert "<h6>Title</h6>" in html


class TestIncludeSnippets:
    """Tests for from/to snippet extraction."""

    def test_from_marker_only(self):
        """<<Include(PageName, , , from="StartText")>> extracts from marker to end."""
        page_contents = {
            "SnippetPage": """# Header

StartText
This should be included.
More content here.
""",
        }
        html = render(
            '<<Include(SnippetPage, , , from="StartText")>>',
            page_contents=page_contents,
        )
        assert "This should be included" in html
        assert "StartText" not in html

    def test_to_marker_only(self):
        """<<Include(PageName, , , to="EndText")>> extracts from start to marker."""
        page_contents = {
            "SnippetPage": """# Header

This should be included.
More content here.
EndText
""",
        }
        html = render(
            '<<Include(SnippetPage, , , to="EndText")>>',
            page_contents=page_contents,
        )
        assert "This should be included" in html
        assert "EndText" not in html

    def test_from_and_to_markers(self):
        """<<Include(PageName, , , from="A", to="B")>> extracts between markers."""
        page_contents = {
            "SnippetPage": """# Header

Before A section.
A
Middle content here.
B
After B section.
""",
        }
        html = render(
            '<<Include(SnippetPage, , , from="A", to="B")>>',
            page_contents=page_contents,
        )
        assert "Middle content here" in html
        assert "Before A section" not in html
        assert "After B section" not in html

    def test_snippet_with_heading(self):
        """Snippet extraction combined with heading works."""
        page_contents = {
            "Guide": """# Guide Title

StartHere
Extracted content.
EndHere
""",
        }
        html = render(
            '<<Include(Guide, "My Heading", 2, from="StartHere", to="EndHere")>>',
            page_contents=page_contents,
        )
        assert "<h2>My Heading</h2>" in html
        assert "Extracted content" in html


class TestIncludePattern:
    """Tests for pattern includes like <<Include(Docs/*)>>."""

    def test_pattern_include_all_matching(self):
        """<<Include(Docs/*)>> includes all pages under Docs/."""
        page_contents = {
            "Docs/Intro": "# Introduction\n\nWelcome to the docs.",
            "Docs/Setup": "# Setup\n\nGetting started.",
            "Docs/Advanced": "# Advanced\n\nDeep dive.",
            "Blog/Post": "# Blog Post\n\nNot in docs.",
        }
        html = render("<<Include(Docs/*)>>", page_contents=page_contents)
        assert "Introduction" in html
        assert "Setup" in html
        assert "Advanced" in html
        assert "Blog Post" not in html

    def test_pattern_include_sort_ascending(self):
        """<<Include(Docs/*, , , sort=ascending)>> sorts alphabetically ascending."""
        page_contents = {
            "Docs/Zebra": "# Zebra",
            "Docs/Apple": "# Apple",
            "Docs/Banana": "# Banana",
        }
        html = render(
            "<<Include(Docs/*, , , sort=ascending)>>", page_contents=page_contents
        )
        apple_pos = html.find("Apple")
        banana_pos = html.find("Banana")
        zebra_pos = html.find("Zebra")
        assert apple_pos < banana_pos < zebra_pos

    def test_pattern_include_sort_descending(self):
        """<<Include(Docs/*, , , sort=descending)>> sorts alphabetically descending."""
        page_contents = {
            "Docs/Zebra": "# Zebra",
            "Docs/Apple": "# Apple",
            "Docs/Banana": "# Banana",
        }
        html = render(
            "<<Include(Docs/*, , , sort=descending)>>", page_contents=page_contents
        )
        apple_pos = html.find("Apple")
        banana_pos = html.find("Banana")
        zebra_pos = html.find("Zebra")
        assert zebra_pos < banana_pos < apple_pos

    def test_pattern_no_matches(self):
        """<<Include(Foo/*)>> when no pages match shows missing placeholder."""
        page_contents = {
            "Bar/Page": "# Bar Page",
        }
        html = render("<<Include(Foo/*)>>", page_contents=page_contents)
        assert "include-missing" in html


class TestIncludeCircular:
    """Tests for circular include detection."""

    def test_circular_include_skipped(self):
        """Page including itself shows circular skip message."""
        page_contents = {
            "SelfPage": "# Self Content\n\n<<Include(SelfPage)>>",
        }
        html = render(
            "<<Include(SelfPage)>>",
            page_contents=page_contents,
            current_page="SelfPage",
        )
        assert "include-circular" in html
        assert "circular include skipped" in html

    def test_circular_not_detected_when_different_page(self):
        """<<Include(OtherPage)>> from PageName does NOT trigger circular."""
        page_contents = {
            "PageA": "# Content A",
            "PageB": "# Content B\n\n<<Include(PageA)>>",
        }
        html = render(
            "<<Include(PageB)>>", page_contents=page_contents, current_page="PageB"
        )
        assert "circular" not in html
        assert "Content A" in html

    def test_circular_pattern_include_skipped(self):
        """Circular detection works inside pattern includes."""
        page_contents = {
            "Docs/Page1": "# Doc Page 1",
            "Docs/Page2": "# Doc Page 2\n\n<<Include(Docs/Page1)>>\n\n<<Include(Docs/Page2)>>",
        }
        html = render("<<Include(Docs/*)>>", page_contents=page_contents)
        assert html.count("include-circular") == 1


class TestIncludeFrontmatter:
    """Tests for frontmatter stripping."""

    def test_frontmatter_stripped(self):
        """Frontmatter is stripped from included page content."""
        page_contents = {
            "WithFrontmatter": """---
title: Test Page
tags: [test, sample]
---

# Actual Content

This is the real content.
""",
        }
        html = render("<<Include(WithFrontmatter)>>", page_contents=page_contents)
        assert "Actual Content" in html
        assert "This is the real content" in html
        assert "title: Test Page" not in html
        assert "tags:" not in html


class TestIncludeCodeBlockEscaping:
    """Tests for code block escaping."""

    def test_not_expanded_in_fenced_code_block(self):
        """<<Include(...)>> inside ``` code blocks is NOT expanded."""
        page_contents = {
            "TargetPage": "# Should Not Appear",
        }
        content = "```\n<<Include(TargetPage)>>\n```"
        html = render(content, page_contents=page_contents)
        assert "include-content" not in html
        assert "&lt;&lt;Include(TargetPage)&gt;&gt;" in html

    def test_not_expanded_in_tilde_code_block(self):
        """<<Include(...)>> inside ~~~ code blocks is NOT expanded."""
        page_contents = {
            "TargetPage": "# Should Not Appear",
        }
        content = "~~~\n<<Include(TargetPage)>>\n~~~"
        html = render(content, page_contents=page_contents)
        assert "include-content" not in html


class TestIncludeEdgeCases:
    """Edge case tests."""

    def test_empty_page_content(self):
        """<<Include(EmptyPage)>> with empty page renders without error."""
        page_contents = {
            "EmptyPage": "",
        }
        html = render("<<Include(EmptyPage)>>", page_contents=page_contents)
        assert "include-content" in html

    def test_page_name_with_spaces(self):
        """<<Include(Page Name)>> with spaces in name works."""
        page_contents = {
            "Page Name": "# Content",
        }
        html = render("<<Include(Page Name)>>", page_contents=page_contents)
        assert "include-content" in html
        assert "Content" in html

    def test_page_name_with_slashes(self):
        """<<Include(Docs/SubPage)>> with subpage path works."""
        page_contents = {
            "Docs/SubPage": "# SubPage Content",
        }
        html = render("<<Include(Docs/SubPage)>>", page_contents=page_contents)
        assert "include-content" in html
        assert "SubPage Content" in html
