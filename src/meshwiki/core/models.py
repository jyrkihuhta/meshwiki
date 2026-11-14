"""Data models for MeshWiki."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)


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


# Frontmatter is hand-written YAML, so a declared field can arrive as any YAML
# type (``title: 2024`` is an int, ``tags: python`` a string). The helpers below
# turn such values into the declared type where the meaning is clear; a value
# with no sensible reading is moved to ``<field>_invalid`` so it is still in the
# page's metadata (and in the file after the next write) instead of being lost.
INVALID_SUFFIX = "_invalid"
TEXT_FIELDS = ("title", "uuid")
LIST_FIELDS = ("tags", "children")
DATETIME_FIELDS = ("created", "modified")

_SCALAR_TYPES = (str, bool, int, float, date)  # date also covers datetime
_DATETIME_ADAPTER: TypeAdapter[datetime] = TypeAdapter(datetime)


def _describe(value: Any) -> str:
    """Return a short repr of a rejected value for an error message."""
    text = repr(value)
    return text if len(text) <= 60 else f"{text[:57]}..."


def _coerce_text(value: Any) -> str | None:
    """Coerce a frontmatter value to text, or raise ``ValueError``."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, _SCALAR_TYPES):
        return str(value)
    raise ValueError(f"expected text, got {_describe(value)}")


def _coerce_text_list(value: Any) -> list[str]:
    """Coerce a frontmatter value to a flat list of text, or raise ``ValueError``.

    A scalar becomes a one-item list, nested lists are flattened, and empty
    items are dropped (the same reading the Rust graph engine applies).
    """
    if value is None or value == "":
        return []
    if isinstance(value, _SCALAR_TYPES):
        return [str(value)]
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(_coerce_text_list(item))
        return items
    raise ValueError(f"expected a list of text values, got {_describe(value)}")


def _coerce_datetime(value: Any) -> datetime | None:
    """Coerce a frontmatter value to a datetime, or raise ``ValueError``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, bool):
        try:
            return _DATETIME_ADAPTER.validate_python(value)
        except ValidationError:
            pass
    raise ValueError(f"expected a date and time, got {_describe(value)}")


_COERCERS: tuple[tuple[tuple[str, ...], Callable[[Any], Any]], ...] = (
    (TEXT_FIELDS, _coerce_text),
    (LIST_FIELDS, _coerce_text_list),
    (DATETIME_FIELDS, _coerce_datetime),
)


def coerce_frontmatter(data: dict[Any, Any]) -> tuple[dict[Any, Any], list[str]]:
    """Make raw frontmatter values fit ``PageMetadata``'s declared field types.

    Scalars for text fields become text, a scalar for a list field becomes a
    one-item list, list items become text, and dates become datetimes. A value
    that cannot be read as the field's type is moved to ``<field>_invalid`` and
    reported. Fields that are not declared on ``PageMetadata`` are left alone.

    Args:
        data: Frontmatter mapping as loaded from YAML. It is not modified.

    Returns:
        ``(coerced, problems)``: a new mapping that ``PageMetadata`` accepts, and
        one human-readable message per value that had to be set aside.
    """
    coerced = dict(data)
    problems: list[str] = []
    for fields, coerce in _COERCERS:
        for name in fields:
            if name not in coerced:
                continue
            try:
                coerced[name] = coerce(coerced[name])
            except RecursionError:
                # A YAML alias that contains itself (``tags: &a [*a]``).
                coerced[f"{name}{INVALID_SUFFIX}"] = coerced.pop(name)
                problems.append(f"{name}: the value refers to itself")
            except ValueError as exc:
                coerced[f"{name}{INVALID_SUFFIX}"] = coerced.pop(name)
                problems.append(f"{name}: {exc}")
    return coerced, problems


def datetime_sort_key(value: datetime | None) -> float:
    """Return a sort key that orders naive and timezone-aware datetimes together.

    Page files written by the wiki carry naive local timestamps, while files
    written by hand or by other tools may carry an offset; comparing the two
    directly raises ``TypeError``. Naive values are read as local time.

    Args:
        value: The timestamp to order by. ``None`` and anything that is not a
            usable datetime sort before every real timestamp.

    Returns:
        Seconds since the epoch, or negative infinity when there is no usable
        timestamp.
    """
    if not isinstance(value, datetime):
        return float("-inf")
    try:
        return value.timestamp()
    except (OverflowError, OSError, ValueError):
        # Out of the platform's range (e.g. year 1 shifted by a UTC offset).
        return float("-inf")


class PageMetadata(BaseModel):
    """Metadata extracted from page frontmatter.

    Construction never fails because of a wrongly typed declared field: values
    are passed through ``coerce_frontmatter`` first.
    """

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    created: datetime | None = None
    modified: datetime | None = None
    uuid: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_declared_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data, _ = coerce_frontmatter(data)
        return data


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
