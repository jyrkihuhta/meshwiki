"""Unit tests for the NewPage macro."""

from meshwiki.core.parser import (
    NEWPAGE_PATTERN,
    NewPageExtension,
    NewPagePreprocessor,
    _render_newpage_macro,
    parse_wiki_content,
)


class TestNewPagePattern:
    def test_basic_pattern(self):
        m = NEWPAGE_PATTERN.search("<<NewPage(MyTemplate)>>")
        assert m is not None
        assert m.group(1) == "MyTemplate"
        assert m.group(2) is None
        assert m.group(3) is None

    def test_pattern_with_button_label(self):
        m = NEWPAGE_PATTERN.search('<<NewPage(MyTemplate, "Create task")>>')
        assert m is not None
        assert m.group(1) == "MyTemplate"
        assert m.group(2) == "Create task"
        assert m.group(3) is None

    def test_pattern_with_parent(self):
        m = NEWPAGE_PATTERN.search('<<NewPage(MyTemplate, "Create", Projects)>>')
        assert m is not None
        assert m.group(1) == "MyTemplate"
        assert m.group(2) == "Create"
        assert m.group(3) == "Projects"

    def test_pattern_default_button(self):
        m = NEWPAGE_PATTERN.search("<<NewPage(MyTemplate)>>")
        assert m is not None
        assert m.group(2) is None

    def test_pattern_whitespace(self):
        m = NEWPAGE_PATTERN.search("<<NewPage(  MyTemplate  )>>")
        assert m is not None
        assert m.group(1).strip() == "MyTemplate"


class TestRenderNewPageMacro:
    def test_basic(self):
        html = _render_newpage_macro("MyTemplate", "New page", None)
        assert 'class="new-page-macro"' in html
        assert 'class="new-page-input"' in html
        assert 'class="new-page-button"' in html
        assert "New page" in html
        assert "MyTemplate" in html

    def test_custom_button_label(self):
        html = _render_newpage_macro("MyTemplate", "Create task", None)
        assert "Create task" in html

    def test_with_parent_page(self):
        html = _render_newpage_macro("MyTemplate", "New page", "Projects")
        assert "/page/Projects/" in html

    def test_default_button_when_empty(self):
        html = _render_newpage_macro("MyTemplate", "", None)
        assert "New page" in html

    def test_input_placeholder(self):
        html = _render_newpage_macro("MyTemplate", "New page", None)
        assert 'placeholder="Page name"' in html

    def test_onclick_navigates_correctly(self):
        html = _render_newpage_macro("MyTemplate", "New page", None)
        assert "onclick=" in html
        assert "/page/" in html
        assert "template=MyTemplate" in html

    def test_onclick_with_parent(self):
        html = _render_newpage_macro("MyTemplate", "Create", "Projects")
        assert "/page/Projects/" in html
        assert "template=MyTemplate" in html

    def test_html_escaping(self):
        html = _render_newpage_macro('Template"X', 'Create "X"', None)
        assert "&quot;" in html or "&#34;" in html


class TestNewPagePreprocessor:
    def test_skips_when_no_macro(self):
        from markdown import Markdown

        md = Markdown(extensions=[NewPageExtension()])
        preprocessor = NewPagePreprocessor(md)
        lines = ["Hello", "World"]
        result = preprocessor.run(lines)
        assert result == lines

    def test_skips_fenced_code_blocks(self):
        from markdown import Markdown

        md = Markdown(extensions=[NewPageExtension()])
        preprocessor = NewPagePreprocessor(md)
        lines = ["```", "<<NewPage(MyTemplate)>>", "```"]
        result = preprocessor.run(lines)
        result_text = "\n".join(result)
        assert "new-page-macro" not in result_text

    def test_skips_tilde_fenced_code_blocks(self):
        from markdown import Markdown

        md = Markdown(extensions=[NewPageExtension()])
        preprocessor = NewPagePreprocessor(md)
        lines = ["~~~", "<<NewPage(MyTemplate)>>", "~~~"]
        result = preprocessor.run(lines)
        result_text = "\n".join(result)
        assert "new-page-macro" not in result_text


class TestNewPageInContent:
    def test_basic_in_content(self):
        html = parse_wiki_content("<<NewPage(MyTemplate)>>")
        assert "new-page-macro" in html
        assert "new-page-input" in html
        assert "new-page-button" in html

    def test_custom_label_in_content(self):
        html = parse_wiki_content('<<NewPage(MyTemplate, "Create task")>>')
        assert "Create task" in html

    def test_with_parent_in_content(self):
        html = parse_wiki_content('<<NewPage(MyTemplate, "Create", Projects)>>')
        assert "/page/Projects/" in html

    def test_multiple_macros(self):
        html = parse_wiki_content(
            '<<NewPage(Template1)>> and <<NewPage(Template2, "Label 2")>>'
        )
        assert html.count("new-page-macro") == 2

    def test_macro_in_paragraph(self):
        html = parse_wiki_content("Click <<NewPage(MyTemplate)>> to create.")
        assert "new-page-macro" in html

    def test_not_in_fenced_code(self):
        html = parse_wiki_content("```\n<<NewPage(MyTemplate)>>\n```")
        assert "new-page-macro" not in html

    def test_not_in_tilde_fenced_code(self):
        html = parse_wiki_content("~~~\n<<NewPage(MyTemplate)>>\n~~~")
        assert "new-page-macro" not in html
