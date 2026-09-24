"""Shared validation helpers for the agent factory JSON API."""

from __future__ import annotations

from fastapi import HTTPException, status


def validate_api_page_name(name: str) -> None:
    """Reject page names that could escape the storage directory.

    Unlike the wiki UI validator (which forbids all slashes), the factory API
    uses hierarchical, slash-separated page names (e.g.
    ``Factory/Macros/MyTask``), so forward slashes are allowed.  What is *not*
    allowed is anything that could traverse outside the data directory:
    absolute paths, ``..`` segments, backslashes and null bytes.  This mirrors
    the defense-in-depth guard in ``FileStorage._get_path`` but returns a clean
    ``400`` instead of surfacing an internal ``ValueError`` as a ``500``.

    Raises:
        HTTPException: ``400`` if the name is empty or looks like a traversal.
    """
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty page name"
        )
    if "\x00" in name or "\\" in name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid page name: {name!r}",
        )
    if name.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Absolute page names are not allowed: {name!r}",
        )
    # Reject any ".." path segment (handles "..", "../x", "a/../b", "a/..").
    if any(segment == ".." for segment in name.split("/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path traversal is not allowed in page name: {name!r}",
        )
