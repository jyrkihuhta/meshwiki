"""Tests for slugify utility."""

from meshwiki.utils.slugify import slugify


class TestSlugify:
    def test_hello_world(self):
        assert slugify("Hello World") == "hello-world"

    def test_foo_bar_with_special_chars(self):
        assert slugify("foo bar!") == "foo-bar"

    def test_multiple_spaces(self):
        assert slugify("foo  bar   baz") == "foo-bar-baz"

    def test_strips_leading_trailing_hyphens(self):
        assert slugify("  hello  ") == "hello"

    def test_preserves_hyphens_in_middle(self):
        assert slugify("foo-bar-baz") == "foo-bar-baz"

    def test_removes_special_chars(self):
        assert slugify("foo@bar#baz") == "foobarbaz"
