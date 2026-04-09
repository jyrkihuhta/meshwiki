"""Storage dependency for FastAPI routes.

Provides module-level accessors for the FileStorage and RevisionStore
singletons so that API modules can import them without creating circular
imports back through ``meshwiki.main``.  Mirrors the pattern used by
``meshwiki.core.graph`` for the Rust engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshwiki.core.revision_store import RevisionStore
    from meshwiki.core.storage import FileStorage

_storage: "FileStorage | None" = None
_revision_store: "RevisionStore | None" = None


def set_storage(storage: "FileStorage") -> None:
    """Register the global storage instance.  Called once at app startup."""
    global _storage
    _storage = storage


def get_storage() -> "FileStorage":
    """FastAPI dependency: return the global FileStorage instance."""
    if _storage is None:
        raise RuntimeError("Storage not initialised — call set_storage() first")
    return _storage


def set_revision_store(store: "RevisionStore") -> None:
    """Register the global RevisionStore instance.  Called once at app startup."""
    global _revision_store
    _revision_store = store


def get_revision_store() -> "RevisionStore":
    """Return the global RevisionStore instance."""
    if _revision_store is None:
        raise RuntimeError(
            "RevisionStore not initialised — call set_revision_store() first"
        )
    return _revision_store
