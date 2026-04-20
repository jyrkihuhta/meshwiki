"""Public JSON API endpoints (hover cards, previews, search)."""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from meshwiki.core.dependencies import get_storage

router = APIRouter()


class PagePreview(BaseModel):
    page_name: str
    excerpt: str
    exists: bool


def _first_paragraph(body: str, max_len: int = 300) -> str:
    """Return the first non-empty paragraph, plain-text, truncated."""
    for chunk in body.split("\n\n"):
        text = chunk.strip()
        if text:
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
            text = re.sub(r"[*_`#]", "", text)
            return text[:max_len]
    return ""


@router.get("/pages/{page_name}/preview", response_model=PagePreview)
async def page_preview(page_name: str) -> PagePreview:
    """Return the first paragraph of a wiki page for hover card preview."""
    storage = get_storage()
    page = await storage.get_page(page_name)
    if page is None:
        return PagePreview(page_name=page_name, excerpt="", exists=False)
    excerpt = _first_paragraph(page.content)
    return PagePreview(page_name=page_name, excerpt=excerpt, exists=True)
