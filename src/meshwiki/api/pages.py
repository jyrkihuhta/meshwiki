"""Generic page CRUD endpoints for the agent factory JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from meshwiki.api.auth import require_api_key
from meshwiki.core.dependencies import get_storage
from meshwiki.core.storage import FileStorage

router = APIRouter(dependencies=[Depends(require_api_key)])


class PageCreate(BaseModel):
    name: str
    content: str


class PageResponse(BaseModel):
    name: str
    content: str
    metadata: dict
    exists: bool


def _page_response(page) -> PageResponse:
    return PageResponse(
        name=page.name,
        content=page.content,
        metadata=page.metadata.model_dump(),
        exists=page.exists,
    )


@router.get("/pages", response_model=list[PageResponse])
async def list_pages(
    tag: str | None = None,
    type: str | None = None,
    status: str | None = None,
    storage: FileStorage = Depends(get_storage),
) -> list[PageResponse]:
    """List pages with optional filters."""
    pages = await storage.list_pages_with_metadata()

    results = []
    for page in pages:
        extra = page.metadata.model_extra or {}

        if tag is not None:
            if tag not in (page.metadata.tags or []):
                continue
        if type is not None:
            if extra.get("type") != type:
                continue
        if status is not None:
            if extra.get("status") != status:
                continue

        results.append(_page_response(page))

    return results


@router.get("/pages/{name:path}", response_model=PageResponse)
async def get_page(
    name: str,
    storage: FileStorage = Depends(get_storage),
) -> PageResponse:
    """Get a single page by name."""
    page = await storage.get_page(name)
    if page is None or not page.exists:
        raise HTTPException(status_code=404, detail=f"Page not found: {name!r}")
    # Include raw content (with frontmatter) so agents can round-trip it
    raw = await storage.get_raw_content(name)
    page.content = raw or page.content
    return _page_response(page)


@router.post("/pages", response_model=PageResponse, status_code=status.HTTP_201_CREATED)
async def create_page(
    body: PageCreate,
    storage: FileStorage = Depends(get_storage),
) -> PageResponse:
    """Create a new page."""
    page = await storage.save_page(body.name, body.content)
    return _page_response(page)


@router.put("/pages/{name:path}", response_model=PageResponse)
async def update_page(
    name: str,
    body: PageCreate,
    storage: FileStorage = Depends(get_storage),
) -> PageResponse:
    """Create or update a page."""
    page = await storage.save_page(name, body.content)
    return _page_response(page)


@router.delete("/pages/{name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(
    name: str,
    storage: FileStorage = Depends(get_storage),
) -> None:
    """Delete a page."""
    deleted = await storage.delete_page(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Page not found: {name!r}")
