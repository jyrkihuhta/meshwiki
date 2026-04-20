"""Utility functions."""

import re


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug.

    Args:
        text: The text to slugify.

    Returns:
        A lowercase slug with hyphens instead of spaces/underscores,
        stripping any character that is not alphanumeric or a hyphen.
    """
    text = text.lower()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^a-z0-9\-]", "", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")
