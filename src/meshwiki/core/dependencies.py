"""Storage dependency for FastAPI routes.

Provides a module-level accessor for the FileStorage singleton so that
API modules can import ``get_storage`` without creating a circular import
back through ``meshwiki.main``.  Mirrors the pattern used by
``meshwiki.core.graph`` for the Rust engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshwiki.core.storage import FileStorage

_storage: "FileStorage | None" = None


def set_storage(storage: "FileStorage") -> None:
    """Register the global storage instance.  Called once at app startup."""
    global _storage
    _storage = storage


def get_storage() -> "FileStorage":
    """FastAPI dependency: return the global FileStorage instance."""
    if _storage is None:
        raise RuntimeError("Storage not initialised — call set_storage() first")
    return _storage
