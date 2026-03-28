"""Storage abstraction for wiki pages."""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import yaml

from meshwiki.core.models import Page, PageMetadata


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


class FileStorage(Storage):
    """File-based storage implementation.

    Pages are stored as Markdown files with optional YAML frontmatter.
    File naming: PageName.md (spaces converted to underscores)
    """

    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n",
        re.DOTALL,
    )

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, name: str) -> Path:
        """Get full path for a page.

        Converts spaces to underscores in each path segment and resolves the
        final path.  Raises ``ValueError`` if the resolved path escapes the
        data directory (defense-in-depth against path traversal).
        """
        parts = [seg.replace(" ", "_") for seg in name.split("/")]
        path = self.base_path.joinpath(*parts).with_suffix(".md")
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

    def _parse_frontmatter(self, content: str) -> tuple[PageMetadata, str]:
        """Parse YAML frontmatter from content.

        Returns (metadata, content_without_frontmatter).
        """
        match = self.FRONTMATTER_PATTERN.match(content)
        if match:
            try:
                frontmatter = yaml.safe_load(match.group(1)) or {}
                metadata = PageMetadata(**frontmatter)
                content = content[match.end() :]
                return metadata, content
            except (yaml.YAMLError, TypeError):
                pass
        return PageMetadata(), content

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

    async def get_page(self, name: str) -> Page | None:
        """Get a page by name."""
        path = self._get_path(name)
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8")
        metadata, content = self._parse_frontmatter(content)

        return Page(
            name=name,
            content=content,
            metadata=metadata,
            exists=True,
        )

    async def save_page(self, name: str, content: str) -> Page:
        """Save a page."""
        path = self._get_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Parse any frontmatter from the content
        metadata, body = self._parse_frontmatter(content)

        # Update modification time
        now = datetime.now()
        if not metadata.created:
            metadata.created = now
        metadata.modified = now

        # Write with frontmatter
        frontmatter = self._create_frontmatter(metadata)
        path.write_text(frontmatter + body, encoding="utf-8")

        return Page(
            name=name,
            content=body,
            metadata=metadata,
            exists=True,
        )

    async def delete_page(self, name: str) -> bool:
        """Delete a page and remove any resulting empty parent directories."""
        path = self._get_path(name)
        if path.exists():
            path.unlink()
            # Clean up empty parent directories up to (but not including) base_path
            parent = path.parent
            while parent != self.base_path:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
            return True
        return False

    async def list_pages(self) -> list[str]:
        """List all page names."""
        pages = [self._path_to_name(p) for p in self.base_path.glob("**/*.md")]
        return sorted(pages)

    async def page_exists(self, name: str) -> bool:
        """Check if a page exists."""
        return self._get_path(name).exists()

    async def get_raw_content(self, name: str) -> str | None:
        """Get raw file content including frontmatter for editing."""
        path = self._get_path(name)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    async def search_pages(self, query: str) -> list[dict]:
        """Search pages by name and content."""
        if not query:
            return []

        query_lower = query.lower()
        name_matches = []
        content_matches = []

        for path in self.base_path.glob("**/*.md"):
            name = self._path_to_name(path)
            raw = path.read_text(encoding="utf-8")
            metadata, body = self._parse_frontmatter(raw)
            title = metadata.title or name.replace("_", " ")

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

    async def list_pages_with_metadata(self) -> list[Page]:
        """List all pages with full metadata."""
        pages = []
        for path in self.base_path.glob("**/*.md"):
            name = self._path_to_name(path)
            raw = path.read_text(encoding="utf-8")
            metadata, body = self._parse_frontmatter(raw)
            pages.append(Page(name=name, content=body, metadata=metadata, exists=True))
        return sorted(pages, key=lambda p: p.name.lower())

    async def search_by_tag(self, tag: str) -> list[Page]:
        """Filter pages by tag."""
        tag_lower = tag.lower()
        results = []
        for path in self.base_path.glob("**/*.md"):
            name = self._path_to_name(path)
            raw = path.read_text(encoding="utf-8")
            metadata, body = self._parse_frontmatter(raw)
            if any(t.lower() == tag_lower for t in metadata.tags):
                results.append(
                    Page(name=name, content=body, metadata=metadata, exists=True)
                )
        return sorted(results, key=lambda p: p.name.lower())

    async def update_frontmatter_field(
        self, name: str, field: str, value: str
    ) -> Page | None:
        """Update a single frontmatter field on a page."""
        path = self._get_path(name)
        if not path.exists():
            return None

        raw = path.read_text(encoding="utf-8")
        metadata, body = self._parse_frontmatter(raw)

        # Update the specific field
        if field == "tags":
            metadata.tags = [t.strip() for t in value.split(",") if t.strip()]
        elif field == "title":
            metadata.title = value if value else None
        elif value:
            setattr(metadata, field, value)
        else:
            # Empty value: remove extra field if it exists
            extras = getattr(metadata, "__pydantic_extra__", None)
            if extras and field in extras:
                del extras[field]

        # Update modification time
        metadata.modified = datetime.now()

        # Write back
        frontmatter = self._create_frontmatter(metadata)
        path.write_text(frontmatter + body, encoding="utf-8")

        return Page(
            name=name,
            content=body,
            metadata=metadata,
            exists=True,
        )
