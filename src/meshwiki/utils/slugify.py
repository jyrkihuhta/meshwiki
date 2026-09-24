"""Slugify utility for converting text to URL-safe slugs."""

import re


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\-]", "", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    return text
