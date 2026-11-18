"""Storage abstraction for wiki pages."""

import asyncio
import re
import uuid as uuid_mod
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from meshwiki.core.logging import get_logger
from meshwiki.core.models import (
    LIST_FIELDS,
    TEXT_FIELDS,
    Page,
    PageMetadata,
    coerce_frontmatter,
)

if TYPE_CHECKING:
    from meshwiki.core.revision_store import RevisionStore

log = get_logger(__name__)


class PageFileError(Exception):
    """The file behind a page exists but cannot be used as a page.

    Raised for a single page addressed by name: a file that is not UTF-8 text,
    cannot be read, or a directory sitting where the page's file should be.
    Scans over many pages log and skip such files instead of raising.
    """

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Page {name!r} cannot be used: {reason}")
        self.name = name
        self.reason = reason


class InvalidFrontmatterError(ValueError):
    """Frontmatter about to be written has a value its field cannot hold."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"Invalid frontmatter: {'; '.join(problems)}")
        self.problems = problems


# What loading frontmatter can raise. PyYAML reports most problems as YAMLError,
# but its constructors let plain built-in exceptions through for a tagged scalar
# they cannot build: ``!!bool maybe`` is a KeyError, ``!!int ""`` an IndexError,
# ``!!timestamp foo`` an AttributeError, an out-of-range date a ValueError or
# OverflowError. Deeply nested or self-referential YAML ends in RecursionError.
_FRONTMATTER_ERRORS = (
    yaml.YAMLError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    ArithmeticError,
    RecursionError,
)


def _is_text(value: Any) -> bool:
    """Return True if a YAML value is already text or a flat list of text."""
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    return value is None or isinstance(value, str)


def _prefer_source_text(value: Any, source: Any) -> Any:
    """Replace YAML-typed scalars in ``value`` with the text they were written as."""
    if isinstance(value, list):
        if isinstance(source, list) and len(source) == len(value):
            return [_prefer_source_text(v, s) for v, s in zip(value, source)]
        return value
    if isinstance(value, (bool, int, float, date)) and isinstance(source, str):
        return source
    return value


def _restore_source_text(frontmatter: dict[Any, Any], yaml_text: str) -> dict[Any, Any]:
    """Give text fields the text the author wrote, not YAML's reading of it.

    YAML turns ``title: 1.10`` into the float 1.1 and ``tags: [yes]`` into
    ``True``; converting those back with ``str()`` would change the author's
    data. When a text field holds such a scalar the frontmatter is loaded a
    second time with every scalar kept as a string, and that text is used.

    Args:
        frontmatter: Mapping from ``yaml.safe_load``.
        yaml_text: The YAML source it was loaded from.

    Returns:
        ``frontmatter`` itself when nothing needed restoring, otherwise a copy
        with the affected text fields replaced.
    """
    fields = [
        f for f in (*TEXT_FIELDS, *LIST_FIELDS) if not _is_text(frontmatter.get(f))
    ]
    if not fields:
        return frontmatter
    try:
        # BaseLoader builds only str, list and dict, so it is as safe as
        # safe_load.
        source = yaml.load(yaml_text, Loader=yaml.BaseLoader)  # noqa: S506
    except _FRONTMATTER_ERRORS:
        return frontmatter
    if not isinstance(source, dict):
        return frontmatter
    restored = dict(frontmatter)
    for field in fields:
        restored[field] = _prefer_source_text(frontmatter[field], source.get(field))
    return restored


class Storage(ABC):
    """Abstract base class for page storage."""

    @abstractmethod
    async def get_page(self, name: str) -> Page | None:
        """Get a page by name. Returns None if not found."""
        ...

    @abstractmethod
    async def save_page(self, name: str, content: str) -> Page:
        """Save a page. Creates if doesn't exist."""
        ...

    @abstractmethod
    async def delete_page(self, name: str) -> bool:
        """Delete a page. Returns True if deleted, False if not found."""
        ...

    @abstractmethod
    async def list_pages(self) -> list[str]:
        """List all page names."""
        ...

    @abstractmethod
    async def page_exists(self, name: str) -> bool:
        """Check if a page exists."""
        ...

    @abstractmethod
    async def search_pages(self, query: str) -> list[dict]:
        """Search pages by name and content.

        Returns list of dicts with keys: name, title, snippet, match_type.
        Name matches are sorted first.
        """
        ...

    @abstractmethod
    async def list_pages_with_metadata(self) -> list["Page"]:
        """List all pages with full metadata."""
        ...

    @abstractmethod
    async def get_raw_content(self, name: str) -> str | None:
        """Get raw file content including frontmatter for editing."""
        ...

    @abstractmethod
    async def search_by_tag(self, tag: str) -> list["Page"]:
        """Filter pages by tag."""
        ...

    @abstractmethod
    async def update_frontmatter_field(
        self, name: str, field: str, value: str
    ) -> Page | None:
        """Update a single frontmatter field on a page.

        Args:
            name: Page name.
            field: Frontmatter field name to update.
            value: New value. For tags, pass comma-separated values.

        Returns:
            Updated Page, or None if page not found.
        """
        ...

    @abstractmethod
    async def rename_page(self, old_name: str, new_name: str) -> "Page | None":
        """Move a page to a new name/location.

        Returns the page at its new location, or None if the source page
        does not exist.
        """
        ...


class FileStorage(Storage):
    """File-based storage implementation.

    Pages are stored as Markdown files with optional YAML frontmatter.
    File naming: PageName.md (spaces converted to underscores)
    """

    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n",
        re.DOTALL,
    )

    def __init__(self, base_path: Path, revision_store: "RevisionStore | None" = None):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._revisions = revision_store

    def _get_path(self, name: str) -> Path:
        """Get full path for a page.

        Converts spaces to underscores in each path segment and resolves the
        final path.  Raises ``ValueError`` if the resolved path escapes the
        data directory (defense-in-depth against path traversal).
        """
        parts = [seg.replace(" ", "_") for seg in name.split("/") if seg]
        if not parts:
            raise ValueError(f"Invalid page name: {name!r}")
        # Append ".md" rather than using with_suffix(): with_suffix replaces
        # everything after the last dot, so "Release 1.2" would map to
        # Release_1.md and overwrite the page "Release 1".
        parts[-1] = f"{parts[-1]}.md"
        path = self.base_path.joinpath(*parts)
        # Defense-in-depth: ensure the resolved path stays inside base_path.
        try:
            path.resolve().relative_to(self.base_path.resolve())
        except ValueError as exc:
            raise ValueError(f"Path traversal detected: {name!r}") from exc
        return path

    def _path_to_name(self, path: Path) -> str:
        """Convert an absolute .md file path to a page name.

        Strips the base path prefix, drops the ``.md`` suffix, and converts
        underscores back to spaces in each segment.
        """
        rel = path.relative_to(self.base_path)
        parts = rel.with_suffix("").parts
        return "/".join(p.replace("_", " ") for p in parts)

    def _load_frontmatter(self, content: str) -> tuple[PageMetadata, str, list[str]]:
        """Parse YAML frontmatter from content, tolerating wrongly typed values.

        Never raises because of what the frontmatter contains. Values that do not
        match a declared field's type are coerced (see ``coerce_frontmatter``);
        frontmatter that is not a YAML mapping at all is left in the body.

        Returns:
            ``(metadata, content_without_frontmatter, problems)`` where
            ``problems`` names each declared field whose value could not be
            coerced and was set aside under ``<field>_invalid``.
        """
        match = self.FRONTMATTER_PATTERN.match(content)
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
                if not isinstance(frontmatter, dict):
                    raise TypeError("frontmatter is not a mapping")
                frontmatter = _restore_source_text(frontmatter, match.group(1))
                frontmatter, problems = coerce_frontmatter(frontmatter)
                metadata = PageMetadata(**frontmatter)
                return metadata, content[match.end() :], problems
            except _FRONTMATTER_ERRORS:
                # Not usable as frontmatter (YAML syntax error, a tagged value
                # PyYAML cannot build, non-string keys, ...): keep the whole
                # text as the page body.
                pass
        return PageMetadata(), content, []

    def _parse_frontmatter(self, content: str) -> tuple[PageMetadata, str]:
        """Parse YAML frontmatter from content.

        Returns (metadata, content_without_frontmatter).
        """
        metadata, body, _ = self._load_frontmatter(content)
        return metadata, body

    def _create_frontmatter(self, metadata: PageMetadata) -> str:
        """Create YAML frontmatter string from metadata."""
        data = metadata.model_dump(exclude_none=True, exclude_defaults=True)
        if not data:
            return ""
        # Convert datetime to ISO format string
        if "created" in data:
            data["created"] = data["created"].isoformat()
        if "modified" in data:
            data["modified"] = data["modified"].isoformat()
        return f"---\n{yaml.dump(data, default_flow_style=False)}---\n\n"

    def _read_file(self, name: str, path: Path) -> str:
        """Read a page file as UTF-8 text.

        Raises:
            PageFileError: If the file cannot be read or is not UTF-8 text.
        """
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PageFileError(name, "the file is not valid UTF-8 text") from exc
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            raise PageFileError(name, f"the file cannot be read ({reason})") from exc

    def _read_page(self, name: str, path: Path) -> Page:
        """Read and parse one page file.

        Raises:
            PageFileError: If the file cannot be read or is not UTF-8 text.
        """
        metadata, body = self._parse_frontmatter(self._read_file(name, path))
        return Page(name=name, content=body, metadata=metadata, exists=True)

    def _scan_pages(self) -> Iterator[Page]:
        """Yield every readable page on disk.

        One bad file never fails the scan: a file that cannot be read (wrong
        encoding, permissions, vanished mid-scan) is logged by name and skipped.
        Directories that happen to be named ``*.md`` are not pages.
        """
        for path in self.base_path.glob("**/*.md"):
            try:
                if not path.is_file():
                    continue
                page = self._read_page(self._path_to_name(path), path)
            except Exception as exc:
                # Deliberately broad: this is the one place that keeps a single
                # file from failing every listing, so it must also hold for a
                # failure nobody anticipated (a parser bug, say). The file is
                # named in the log; only this page is left out.
                log.warning(
                    "page_file_skipped",
                    file=str(path),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            yield page

    async def get_page(self, name: str) -> Page | None:
        """Get a page by name.

        Raises:
            PageFileError: If the page's file exists but cannot be read.
        """
        path = self._get_path(name)
        if not path.is_file():
            return None
        return self._read_page(name, path)

    async def save_page(self, name: str, content: str) -> Page:
        """Save a page.

        Frontmatter values of the wrong type are normalised on the way in
        (``tags: python`` is written back as a one-item list).

        Raises:
            InvalidFrontmatterError: If a declared frontmatter field holds a
                value that cannot be read as that field's type. Nothing is
                written.
            PageFileError: If a directory occupies the page's file name.
        """
        path = self._get_path(name)

        # Parse any frontmatter from the content
        metadata, body, problems = self._load_frontmatter(content)
        if problems:
            raise InvalidFrontmatterError(problems)
        if path.is_dir():
            raise PageFileError(name, "a directory has this page's file name")
        path.parent.mkdir(parents=True, exist_ok=True)

        # Carry forward uuid and created from the file on disk when the incoming
        # content has no frontmatter (e.g. programmatic saves without frontmatter).
        if path.exists() and (not metadata.created or not metadata.uuid):
            try:
                existing_meta = self._read_page(name, path).metadata
            except PageFileError as exc:
                # Saving over an unreadable file is how it gets repaired.
                log.warning("page_file_replaced", file=str(path), error=str(exc))
                existing_meta = PageMetadata()
            if not metadata.created:
                metadata.created = existing_meta.created
            if not metadata.uuid:
                metadata.uuid = existing_meta.uuid

        # Update modification time; assign stable UUID on first save.
        now = datetime.now()
        if not metadata.created:
            metadata.created = now
            if not metadata.uuid:
                metadata.uuid = str(uuid_mod.uuid4())
        metadata.modified = now

        # Write with frontmatter
        frontmatter = self._create_frontmatter(metadata)
        raw = frontmatter + body
        path.write_text(raw, encoding="utf-8")

        if self._revisions is not None:
            operation = (
                "create" if self._revisions.revision_count(name) == 0 else "edit"
            )
            self._revisions.record(name, raw, operation=operation)

        return Page(
            name=name,
            content=body,
            metadata=metadata,
            exists=True,
        )

    async def delete_page(self, name: str) -> bool:
        """Delete a page and remove any resulting empty parent directories."""
        path = self._get_path(name)
        if path.is_file():
            path.unlink()
            # Clean up empty parent directories up to (but not including) base_path
            parent = path.parent
            while parent != self.base_path:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
            if self._revisions is not None:
                self._revisions.delete_page_history(name)
            return True
        return False

    async def list_pages(self) -> list[str]:
        """List all page names."""
        pages = [
            self._path_to_name(p) for p in self.base_path.glob("**/*.md") if p.is_file()
        ]
        return sorted(pages)

    async def page_exists(self, name: str) -> bool:
        """Check if a page exists."""
        return self._get_path(name).is_file()

    async def get_raw_content(self, name: str) -> str | None:
        """Get raw file content including frontmatter for editing.

        Raises:
            PageFileError: If the page's file exists but cannot be read.
        """
        path = self._get_path(name)
        if not path.is_file():
            return None
        return self._read_file(name, path)

    async def search_pages(self, query: str) -> list[dict]:
        """Search pages by name and content."""
        if not query:
            return []

        query_lower = query.lower()
        name_matches = []
        content_matches = []

        for page in self._scan_pages():
            name = page.name
            body = page.content
            title = page.metadata.title or name.replace("_", " ")

            if query_lower in name.lower() or query_lower in title.lower():
                # Extract snippet from beginning of content
                snippet = body.strip()[:150].replace("\n", " ")
                name_matches.append(
                    {
                        "name": name,
                        "title": title,
                        "snippet": snippet,
                        "match_type": "name",
                    }
                )
            elif query_lower in body.lower():
                # Find snippet around match
                idx = body.lower().index(query_lower)
                start = max(0, idx - 50)
                end = min(len(body), idx + len(query) + 100)
                snippet = body[start:end].replace("\n", " ").strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(body):
                    snippet = snippet + "..."
                content_matches.append(
                    {
                        "name": name,
                        "title": title,
                        "snippet": snippet,
                        "match_type": "content",
                    }
                )

        # Name matches first, then content matches, both sorted by name
        name_matches.sort(key=lambda x: x["name"].lower())
        content_matches.sort(key=lambda x: x["name"].lower())
        return name_matches + content_matches

    def list_pages_with_metadata_sync(self) -> list[Page]:
        """Scan pages on disk synchronously; call it via run_in_executor.

        Files that cannot be read are logged and left out.
        """
        return sorted(self._scan_pages(), key=lambda p: p.name.lower())

    def get_pages_sync(self, names: list[str]) -> list[Page]:
        """Load the named pages from disk, skipping any that no longer exist.

        Reads and parses each named file; call via ``run_in_executor`` so the
        batch of disk reads does not block the event loop. A page whose file
        cannot be read is logged and skipped, like a missing one.
        """
        pages: list[Page] = []
        for name in names:
            try:
                path = self._get_path(name)
                if not path.is_file():
                    continue
                pages.append(self._read_page(name, path))
            except Exception as exc:
                # Broad for the same reason as in _scan_pages.
                log.warning(
                    "page_file_skipped",
                    page=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return pages

    async def get_pages(self, names: list[str]) -> list[Page]:
        """Load the named pages, off the event loop.

        For a large candidate set (e.g. a status filter matching many tasks),
        loading each page inline would occupy the loop for the whole batch, so
        the reads run in a thread.
        """
        if not names:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_pages_sync, names)

    async def list_pages_with_metadata(self) -> list[Page]:
        """List all pages with full metadata.

        The scan reads and parses every page on disk, which is slow at wiki
        scale, so it runs in a thread to avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.list_pages_with_metadata_sync)

    async def search_by_tag(self, tag: str) -> list[Page]:
        """Filter pages by tag."""
        tag_lower = tag.lower()
        results = [
            page
            for page in self._scan_pages()
            if any(t.lower() == tag_lower for t in page.metadata.tags)
        ]
        return sorted(results, key=lambda p: p.name.lower())

    @staticmethod
    def _set_field(
        metadata: PageMetadata, field: str, value: str | None, *, remove: bool
    ) -> None:
        """Set one frontmatter field from text, converting to the field's type.

        Declared fields are converted (list fields split on commas and
        newlines, dates parsed); every other field is stored as given, so what
        is written can always be read back.

        Args:
            metadata: Metadata to update in place.
            field: Frontmatter field name.
            value: New value as text. An empty value clears a declared field.
            remove: For undeclared fields, delete the field instead of setting it.

        Raises:
            InvalidFrontmatterError: If the value does not fit a declared field.
        """
        if field in PageMetadata.model_fields:
            raw: Any = value or None
            if field in LIST_FIELDS and isinstance(value, str):
                raw = [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
            coerced, problems = coerce_frontmatter({field: raw})
            if problems:
                raise InvalidFrontmatterError(problems)
            setattr(metadata, field, coerced[field])
            return
        # Write to the extras mapping, not setattr(): a field named like a model
        # attribute (``model_dump``, ``copy``) would otherwise shadow it or be
        # silently dropped.
        if remove:
            metadata.model_extra.pop(field, None)
        else:
            metadata.model_extra[field] = value

    def _write_frontmatter_update(
        self,
        name: str,
        path: Path,
        metadata: PageMetadata,
        body: str,
        message: str,
        set_aside: list[str],
    ) -> Page:
        """Stamp ``modified``, write the page back and record the revision.

        ``set_aside`` is the problem list from reading the file: each entry is a
        declared field whose value could not be read as its type, which this
        write stores under ``<field>_invalid``. That renames a key the caller
        never asked to change, so it is logged with the page name.
        """
        metadata.modified = datetime.now()

        raw = self._create_frontmatter(metadata) + body
        path.write_text(raw, encoding="utf-8")
        if set_aside:
            log.warning(
                "frontmatter_values_set_aside",
                page=name,
                file=str(path),
                problems=set_aside,
            )

        if self._revisions is not None:
            self._revisions.record(
                name, raw, operation="frontmatter_update", message=message
            )

        return Page(name=name, content=body, metadata=metadata, exists=True)

    async def update_frontmatter_field(
        self, name: str, field: str, value: str
    ) -> Page | None:
        """Update a single frontmatter field on a page.

        Args:
            name: Page name.
            field: Frontmatter field name to update.
            value: New value. For list fields (``tags``, ``children``) pass
                comma-separated values. An empty value clears the field.

        Returns:
            Updated Page, or None if page not found.

        Raises:
            InvalidFrontmatterError: If the value does not fit the field's
                type. Nothing is written.
            PageFileError: If the page's file cannot be read.
        """
        path = self._get_path(name)
        if not path.is_file():
            return None

        metadata, body, set_aside = self._load_frontmatter(self._read_file(name, path))
        self._set_field(metadata, field, value, remove=not value)
        return self._write_frontmatter_update(
            name, path, metadata, body, f"Updated {field}", set_aside
        )

    async def patch_frontmatter(
        self, name: str, fields: dict[str, str | None]
    ) -> "Page | None":
        """Update multiple frontmatter fields without touching the page body.

        Pass ``None`` as a value to remove an extra field.  Declared metadata
        fields (``title``, ``tags``, ``children``, ...) follow the same
        conversion rules as ``update_frontmatter_field``.

        Raises:
            InvalidFrontmatterError: If a value does not fit its field's type.
                Nothing is written.
            PageFileError: If the page's file cannot be read.
        """
        path = self._get_path(name)
        if not path.is_file():
            return None

        metadata, body, set_aside = self._load_frontmatter(self._read_file(name, path))
        for field, value in fields.items():
            self._set_field(metadata, field, value, remove=value is None)
        return self._write_frontmatter_update(
            name, path, metadata, body, f"Updated {', '.join(fields)}", set_aside
        )

    async def rename_page(self, old_name: str, new_name: str) -> "Page | None":
        """Move a page to a new name/location."""
        old_path = self._get_path(old_name)
        if not old_path.is_file():
            return None
        new_path = self._get_path(new_name)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        if self._revisions is not None:
            self._revisions.rename_history(old_name, new_name)
        return await self.get_page(new_name)
