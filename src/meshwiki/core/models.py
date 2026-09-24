"""Data models for MeshWiki."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Revision(BaseModel):
    """A single revision of a wiki page."""

    id: int
    page_name: str
    revision: int
    timestamp: datetime
    content: str
    message: str = ""
    author: str = ""
    operation: str = "edit"

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: object) -> datetime:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v  # type: ignore[return-value]

    @property
    def operation_label(self) -> str:
        """Human-readable operation label."""
        return {
            "create": "Created",
            "edit": "Edited",
            "frontmatter_update": "Metadata",
            "rename": "Renamed",
            "restore": "Restored",
        }.get(self.operation, self.operation.capitalize())


class PageMetadata(BaseModel):
    """Metadata extracted from page frontmatter."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    created: datetime | None = None
    modified: datetime | None = None
    uuid: str | None = None


class Page(BaseModel):
    """Represents a wiki page."""

    name: str
    content: str
    metadata: PageMetadata = Field(default_factory=PageMetadata)
    exists: bool = True

    @property
    def title(self) -> str:
        """Return title from metadata or derive from name."""
        return self.metadata.title or self.name.replace("_", " ")

    @property
    def word_count(self) -> int:
        """Approximate word count of page content."""
        return len(self.content.split())
