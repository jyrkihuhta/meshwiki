"""Tests for utils module."""

import pytest

from meshwiki.utils import slugify


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("Hello World!", "hello-world"),
        ("Test Page Name", "test-page-name"),
        ("already-slugified", "already-slugified"),
        ("UPPERCASE", "uppercase"),
        ("mixedCaseText", "mixedcasetext"),
        ("  spaces around  ", "spaces-around"),
        ("under_score", "under-score"),
        ("multiple   spaces", "multiple-spaces"),
        ("special!@#chars", "specialchars"),
        ("multiple----dashes", "multiple-dashes"),
        ("trailing---", "trailing"),
        ("---leading", "leading"),
        ("", ""),
        ("abc", "abc"),
        ("123", "123"),
        ("a b c", "a-b-c"),
        ("Version 1.0.0", "version-100"),
    ],
)
def test_slugify(input_text: str, expected: str) -> None:
    """Test slugify with various inputs."""
    assert slugify(input_text) == expected


def test_slugify_empty_string() -> None:
    """Test slugify with empty string."""
    assert slugify("") == ""


def test_slugify_leading_trailing_hyphens() -> None:
    """Test that leading and trailing hyphens are stripped."""
    assert slugify("---hello---") == "hello"
    assert slugify("--a b c--") == "a-b-c"


def test_slugify_special_characters() -> None:
    """Test that special characters are removed."""
    assert slugify("Hello! World?") == "hello-world"
    assert slugify("foo@bar.com") == "foobarcom"
    assert slugify("test[1]") == "test1"


def test_slugify_collapse_multiple_hyphens() -> None:
    """Test that multiple consecutive hyphens are collapsed."""
    assert slugify("a--b--c") == "a-b-c"
    assert slugify("foo-----bar") == "foo-bar"
