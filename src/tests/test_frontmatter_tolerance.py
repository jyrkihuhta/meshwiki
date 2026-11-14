"""One badly typed or unreadable page file must never take the wiki down.

Regression tests for deep-review item A2: frontmatter that is ordinary YAML but
does not match ``PageMetadata``'s field types (``title: 2024``, ``tags: python``,
...), non-UTF-8 files, and directories named like pages. Covers the storage
layer, the page-scan loops, the HTML routes, the inline-metadata PATCH endpoint
and the save route.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from structlog.testing import capture_logs

from meshwiki.core.models import (
    SELF_REFERENCE_TEXT,
    Page,
    PageMetadata,
    coerce_frontmatter,
    datetime_sort_key,
    json_safe_metadata,
)
from meshwiki.core.parser import parse_wiki_content
from meshwiki.core.storage import (
    FileStorage,
    InvalidFrontmatterError,
    PageFileError,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

# YAML shapes that used to raise pydantic.ValidationError out of every scan.
# id -> (frontmatter YAML, attribute to check, expected value after reading)
BAD_SHAPES: dict[str, tuple[str, str, Any]] = {
    "title_int": ("title: 2024", "title", "2024"),
    "title_date": ("title: 2026-11-10", "title", "2026-11-10"),
    "title_bool": ("title: yes", "title", "yes"),
    "title_float": ("title: 1.10", "title", "1.10"),
    "title_list": ("title: [a, b]", "title", None),
    "tags_scalar": ("tags: python", "tags", ["python"]),
    "tags_mixed": ("tags: [2024, a]", "tags", ["2024", "a"]),
    "tags_null": ("tags:", "tags", []),
    "tags_nested": ("tags: [[a], [b, 1.10]]", "tags", ["a", "b", "1.10"]),
    "tags_mapping": ("tags: {a: 1}", "tags", []),
    "children_scalar": ("children: A", "children", ["A"]),
    "children_int": ("children: 7", "children", ["7"]),
    "uuid_int": ("uuid: 123", "uuid", "123"),
    "created_text": ("created: yesterday", "created", None),
    "created_bool": ("created: yes", "created", None),
    "modified_list": ("modified: [1, 2]", "modified", None),
}

# Explicitly tagged scalars PyYAML cannot build. It raises KeyError, IndexError
# and AttributeError for these, not YAMLError.
TAGGED_SCALARS: dict[str, str] = {
    "tagged_bool": "draft: !!bool maybe",
    "tagged_int": 'x: !!int ""',
    "tagged_float": 'x: !!float ""',
    "tagged_timestamp": "x: !!timestamp foo",
    "tagged_declared_field": "created: !!timestamp foo",
}

# Values YAML loads fine but that no JSON encoder accepts as they are.
UNSERIALISABLE: dict[str, str] = {
    "binary_extra": "x: !!binary gICA",
    "binary_title": "title: !!binary gICA",
    "binary_nested": "x: {k: [!!binary gICA]}",
    "self_referential_list": "x: &a [*a]",
    "self_referential_mapping": "x: &a {k: *a}",
    "nan_extra": "x: .nan",
    "inf_nested": "x: [.inf, {k: -.inf}]",
    "nan_key": "x: {.nan: 1}",
    "odd_set": "x: !!set {.nan, !!binary gICA}",
}

# Frontmatter that is valid for PageMetadata but used to break the sidebar tree
# builder or the "recently modified" sorts.
BAD_EXTRAS: dict[str, str] = {
    "parent_task_list": "parent_task: [Good]",
    "parent_task_wikilink": "parent_task: [[Good]]",
    "parent_task_int": "parent_task: 42",
    "parent_task_mapping": "parent_task: {a: 1}",
    "status_mapping": "status: {a: 1}",
    "status_int": "status: 3",
    "modified_aware": "modified: 2026-01-01T00:00:00Z",
    "modified_aware_text": "modified: '2026-01-01T00:00:00+02:00'",
    "modified_ancient": "modified: 0001-01-01",
    "non_string_key": "1: one\ntitle: Keyed",
    "not_a_mapping": "- a\n- b",
    "recursive_alias": "tags: &a [*a]",
    **TAGGED_SCALARS,
    **UNSERIALISABLE,
}

HTML_ROUTES = [
    "/",
    "/page/Good",
    "/page/Good/edit",
    "/search?q=body",
    "/search?tag=python",
    "/tags",
    "/graph",
    "/login",
]


def _write(base: Path, file_name: str, frontmatter: str, body: str = "body\n") -> Path:
    """Write a page file directly to disk, bypassing the wiki's own writer."""
    path = base / file_name
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def _frontmatter_on_disk(path: Path) -> dict[str, Any]:
    """Return the YAML frontmatter mapping of a page file."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path)


async def _seed_good_page(base: Path) -> None:
    """Create a normal page the way the wiki itself writes one."""
    await FileStorage(base).save_page("Good", "---\ntags: [python]\n---\n\nbody\n")


# ---------------------------------------------------------------------------
# Model: coercion
# ---------------------------------------------------------------------------


class TestCoercion:
    def test_scalars_for_text_fields_become_text(self) -> None:
        meta = PageMetadata(title=2024, uuid=123)
        assert meta.title == "2024"
        assert meta.uuid == "123"

    def test_scalar_for_list_field_becomes_one_item_list(self) -> None:
        assert PageMetadata(tags="python").tags == ["python"]
        assert PageMetadata(children=7).children == ["7"]

    def test_list_items_become_text_and_nested_lists_flatten(self) -> None:
        assert PageMetadata(tags=[2024, "a", ["b"], None]).tags == ["2024", "a", "b"]

    def test_null_and_empty_list_values_mean_no_items(self) -> None:
        assert PageMetadata(tags=None, children="").model_dump()["tags"] == []
        assert PageMetadata(children="").children == []

    def test_date_becomes_datetime(self) -> None:
        meta = PageMetadata(created=datetime(2026, 11, 10).date())
        assert meta.created == datetime(2026, 11, 10)

    def test_unparseable_date_keeps_raw_value_in_extras(self) -> None:
        meta = PageMetadata(created="yesterday")
        assert meta.created is None
        assert meta.model_extra["created_invalid"] == "yesterday"

    def test_uncoercible_values_are_reported_and_preserved(self) -> None:
        data, problems = coerce_frontmatter(
            {"title": ["a"], "tags": {"a": 1}, "status": "draft"}
        )
        assert data == {
            "title_invalid": ["a"],
            "tags_invalid": {"a": 1},
            "status": "draft",
        }
        assert len(problems) == 2
        assert any(p.startswith("title") for p in problems)
        assert any(p.startswith("tags") for p in problems)

    def test_valid_frontmatter_is_untouched(self) -> None:
        now = datetime.now()
        raw = {"title": "T", "tags": ["a"], "created": now, "status": "x"}
        data, problems = coerce_frontmatter(raw)
        assert data == raw
        assert problems == []

    def test_input_mapping_is_not_mutated(self) -> None:
        raw = {"tags": "python", "created": "yesterday"}
        coerce_frontmatter(raw)
        assert raw == {"tags": "python", "created": "yesterday"}


class TestJsonSafeMetadata:
    def test_ordinary_metadata_is_the_same_as_model_dump(self) -> None:
        meta = PageMetadata(
            title="T",
            tags=["a"],
            created=datetime(2026, 1, 2, 3, 4, 5),
            modified=datetime(2026, 1, 2, tzinfo=timezone.utc),
            status="planned",
            priority=3,
            ratio=1.5,
            flag=True,
            nothing=None,
            due=datetime(2026, 11, 10).date(),
            nested={"a": [1, {"b": "c"}], 2: "int key"},
        )

        assert json_safe_metadata(meta) == meta.model_dump()

    def test_bytes_become_text(self) -> None:
        meta = PageMetadata(x=b"hello", y=b"\x80\x80", z=[{"k": b"\xff"}])

        data = json_safe_metadata(meta)

        assert data["x"] == "hello"
        assert data["y"] == "\ufffd\ufffd"
        assert data["z"] == [{"k": "\ufffd"}]

    def test_self_reference_becomes_a_placeholder(self) -> None:
        loop: list[Any] = ["a"]
        loop.append(loop)
        ring: dict[str, Any] = {"name": "ring"}
        ring["self"] = ring

        data = json_safe_metadata(PageMetadata(x=loop, y=ring))

        assert data["x"] == ["a", SELF_REFERENCE_TEXT]
        assert data["y"] == {"name": "ring", "self": SELF_REFERENCE_TEXT}

    def test_shared_value_that_is_not_a_cycle_is_kept(self) -> None:
        shared = [1, 2]

        data = json_safe_metadata(PageMetadata(a=shared, b=[shared, shared]))

        assert data["a"] == [1, 2]
        assert data["b"] == [[1, 2], [1, 2]]

    def test_non_finite_numbers_become_text(self) -> None:
        meta = PageMetadata(a=float("nan"), b=[float("inf")], c={float("-inf"): 1})

        data = json_safe_metadata(meta)

        assert data["a"] == "nan"
        assert data["b"] == ["inf"]
        assert data["c"] == {"-inf": 1}

    def test_sets_become_sorted_lists(self) -> None:
        data = json_safe_metadata(PageMetadata(x={"b", "a", b"c"}))

        assert data["x"] == ["a", "b", "c"]

    def test_stashed_invalid_values_are_covered(self) -> None:
        meta = PageMetadata(title=b"\x80", created=float("nan"))

        data = json_safe_metadata(meta)

        assert data["title"] is None
        assert data["title_invalid"] == "\ufffd"
        assert data["created_invalid"] == "nan"

    def test_the_model_keeps_its_values(self) -> None:
        loop: list[Any] = []
        loop.append(loop)
        meta = PageMetadata(x=b"\x80", y=loop, z=float("inf"))

        json_safe_metadata(meta)

        assert meta.model_extra["x"] == b"\x80"
        assert meta.model_extra["y"] is loop and loop[0] is loop
        assert meta.model_extra["z"] == float("inf")

    @pytest.mark.parametrize("shape", UNSERIALISABLE)
    def test_result_is_strict_json(self, shape: str) -> None:
        import json

        frontmatter = yaml.safe_load(UNSERIALISABLE[shape])

        data = json_safe_metadata(PageMetadata(**frontmatter))

        json.dumps(data, allow_nan=False, default=str)


class TestDatetimeSortKey:
    def test_orders_naive_and_aware_together(self) -> None:
        naive = datetime.now()
        aware_older = datetime.now(timezone.utc) - timedelta(hours=1)
        aware_newer = datetime.now(timezone.utc) + timedelta(hours=1)
        ordered = sorted([aware_newer, naive, aware_older], key=datetime_sort_key)
        assert ordered == [aware_older, naive, aware_newer]

    @pytest.mark.parametrize("value", [None, "yesterday", 5, datetime(1, 1, 1)])
    def test_missing_or_unusable_values_sort_first(self, value: Any) -> None:
        assert datetime_sort_key(value) < datetime_sort_key(datetime(1971, 1, 1))


# ---------------------------------------------------------------------------
# Storage: reading
# ---------------------------------------------------------------------------


class TestStorageReadsBadFrontmatter:
    @pytest.mark.parametrize("shape", BAD_SHAPES)
    async def test_page_reads_with_coerced_value(
        self, storage: FileStorage, tmp_path: Path, shape: str
    ) -> None:
        frontmatter, attr, expected = BAD_SHAPES[shape]
        _write(tmp_path, "Bad.md", frontmatter)

        page = await storage.get_page("Bad")

        assert page is not None
        assert page.content.strip() == "body"
        assert getattr(page.metadata, attr) == expected

    @pytest.mark.parametrize("shape", [*BAD_SHAPES, *BAD_EXTRAS])
    async def test_scans_survive_the_bad_page(
        self, storage: FileStorage, tmp_path: Path, shape: str
    ) -> None:
        frontmatter = BAD_SHAPES[shape][0] if shape in BAD_SHAPES else BAD_EXTRAS[shape]
        _write(tmp_path, "Bad.md", frontmatter)
        await _seed_good_page(tmp_path)

        listed = await storage.list_pages_with_metadata()
        assert [p.name for p in listed] == ["Bad", "Good"]
        assert {r["name"] for r in await storage.search_pages("body")} == {
            "Bad",
            "Good",
        }
        assert "Good" in [p.name for p in await storage.search_by_tag("python")]
        assert [p.name for p in await storage.get_pages(["Bad", "Good"])] == [
            "Bad",
            "Good",
        ]

    async def test_uncoercible_values_are_kept_under_an_invalid_key(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", "title: [a, b]\ntags: {a: 1}\ncreated: yesterday")

        page = await storage.get_page("Bad")

        assert page is not None
        extras = page.metadata.model_extra
        assert extras["title_invalid"] == ["a", "b"]
        assert extras["tags_invalid"] == {"a": 1}
        assert extras["created_invalid"] == "yesterday"
        assert page.title == "Bad"

    @pytest.mark.parametrize("shape", TAGGED_SCALARS)
    async def test_frontmatter_yaml_cannot_build_stays_in_the_body(
        self, storage: FileStorage, tmp_path: Path, shape: str
    ) -> None:
        frontmatter = TAGGED_SCALARS[shape]
        _write(tmp_path, "Bad.md", frontmatter)

        page = await storage.get_page("Bad")

        # Same reading as a YAML syntax error: no metadata, nothing dropped.
        assert page is not None
        assert page.metadata == PageMetadata()
        assert frontmatter in page.content
        assert page.content.rstrip().endswith("body")

    async def test_author_text_is_kept_not_the_yaml_interpretation(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", "title: 0123\ntags: [yes, 1.10, 0x10]\nuuid: 1e3")

        page = await storage.get_page("Bad")

        assert page is not None
        assert page.metadata.title == "0123"
        assert page.metadata.tags == ["yes", "1.10", "0x10"]
        assert page.metadata.uuid == "1e3"


class TestStorageSkipsUnreadableFiles:
    async def _seed(self, base: Path) -> None:
        await _seed_good_page(base)
        (base / "Latin.md").write_bytes(b"---\ntitle: Caf\xe9\n---\n\nbody \xe4\n")
        (base / "x.md").mkdir()
        (base / "x.md" / "Inner.md").write_text("inner body\n", encoding="utf-8")

    async def test_scans_skip_bad_files(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await self._seed(tmp_path)

        listed = [p.name for p in await storage.list_pages_with_metadata()]
        assert listed == ["Good", "x.md/Inner"]
        # Names only (nothing is read): the unreadable page is still a page,
        # the directory is not.
        assert await storage.list_pages() == ["Good", "Latin", "x.md/Inner"]
        assert {r["name"] for r in await storage.search_pages("body")} == {
            "Good",
            "x.md/Inner",
        }
        assert [p.name for p in await storage.search_by_tag("python")] == ["Good"]
        assert [p.name for p in await storage.get_pages(["Latin", "Good", "x"])] == [
            "Good"
        ]

    async def test_skipped_file_is_logged_by_name(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await self._seed(tmp_path)

        with capture_logs() as logs:
            storage.list_pages_with_metadata_sync()

        warnings = [e for e in logs if e["log_level"] == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["file"].endswith("Latin.md")

    async def test_unforeseen_failure_in_one_file_does_not_fail_a_scan(
        self, storage: FileStorage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_good_page(tmp_path)
        _write(tmp_path, "Odd.md", "title: Odd")
        read_page = storage._read_page

        def flaky(name: str, path: Path) -> Page:
            if name == "Odd":
                raise KeyError("a failure nobody anticipated")
            return read_page(name, path)

        monkeypatch.setattr(storage, "_read_page", flaky)

        with capture_logs() as logs:
            listed = storage.list_pages_with_metadata_sync()
            loaded = storage.get_pages_sync(["Odd", "Good"])
            found = await storage.search_pages("body")
            tagged = await storage.search_by_tag("python")

        assert [p.name for p in listed] == ["Good"]
        assert [p.name for p in loaded] == ["Good"]
        assert [r["name"] for r in found] == ["Good"]
        assert [p.name for p in tagged] == ["Good"]
        skipped = [e for e in logs if e["event"] == "page_file_skipped"]
        assert len(skipped) == 4
        assert all(e["log_level"] == "warning" for e in skipped)
        assert all(e["error_type"] == "KeyError" for e in skipped)
        assert skipped[0]["file"].endswith("Odd.md")
        assert skipped[1]["page"] == "Odd"

    async def test_direct_read_raises_a_specific_error(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await self._seed(tmp_path)

        with pytest.raises(PageFileError, match="UTF-8"):
            await storage.get_page("Latin")
        with pytest.raises(PageFileError, match="UTF-8"):
            await storage.get_raw_content("Latin")
        with pytest.raises(PageFileError):
            await storage.update_frontmatter_field("Latin", "status", "x")
        with pytest.raises(PageFileError):
            await storage.patch_frontmatter("Latin", {"status": "x"})
        # Nothing was rewritten by the failed updates.
        assert (tmp_path / "Latin.md").read_bytes().startswith(b"---\ntitle: Caf\xe9")

    async def test_directory_named_like_a_page_is_not_a_page(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await self._seed(tmp_path)

        assert await storage.get_page("x") is None
        assert await storage.get_raw_content("x") is None
        assert await storage.page_exists("x") is False
        assert await storage.delete_page("x") is False
        assert await storage.update_frontmatter_field("x", "status", "y") is None
        with pytest.raises(PageFileError):
            await storage.save_page("x", "content")
        assert (tmp_path / "x.md" / "Inner.md").exists()

    async def test_saving_over_an_unreadable_file_replaces_it(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await self._seed(tmp_path)

        await storage.save_page("Latin", "fixed\n")

        page = await storage.get_page("Latin")
        assert page is not None
        assert page.content == "fixed\n"


# ---------------------------------------------------------------------------
# Storage: writing and round trips
# ---------------------------------------------------------------------------


class TestRoundTrip:
    async def test_updating_another_field_normalises_but_loses_nothing(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        path = _write(
            tmp_path,
            "Bad.md",
            "title: 1.10\n"
            "tags: python\n"
            "children: 7\n"
            "uuid: 123\n"
            "created: yesterday\n"
            "status: draft\n"
            "custom: {a: [1, 2], b: 2026-11-10}\n"
            "priority: 3",
        )

        await storage.update_frontmatter_field("Bad", "status", "active")

        data = _frontmatter_on_disk(path)
        assert data.pop("modified")
        assert data == {
            "title": "1.10",
            "tags": ["python"],
            "children": ["7"],
            "uuid": "123",
            "created_invalid": "yesterday",
            "status": "active",
            "custom": {"a": [1, 2], "b": datetime(2026, 11, 10).date()},
            "priority": 3,
        }
        assert path.read_text(encoding="utf-8").endswith("---\n\nbody\n")

    async def test_rewritten_file_reads_back_identically(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "Bad.md",
            "title: yes\ntags: [2024, a]\ncreated: yesterday\nmodified: [1]\nx: 1",
        )
        before = await storage.get_page("Bad")
        assert before is not None

        text = storage._create_frontmatter(before.metadata) + before.content
        after_meta, after_body = storage._parse_frontmatter(text)

        assert after_meta == before.metadata
        assert after_body == before.content

    async def test_save_normalises_coercible_frontmatter(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        page = await storage.save_page(
            "New", "---\ntitle: 2024\ntags: python\nkeep: [1, 2]\n---\n\nbody\n"
        )

        assert page.metadata.title == "2024"
        assert page.metadata.tags == ["python"]
        data = _frontmatter_on_disk(tmp_path / "New.md")
        assert data["title"] == "2024"
        assert data["tags"] == ["python"]
        assert data["keep"] == [1, 2]
        reread = await storage.get_page("New")
        assert reread is not None
        assert reread.metadata == page.metadata

    @pytest.mark.parametrize(
        "frontmatter",
        ["created: yesterday", "title: [a, b]", "tags: {a: 1}", "children: [{a: 1}]"],
    )
    async def test_save_rejects_frontmatter_that_cannot_be_made_valid(
        self, storage: FileStorage, tmp_path: Path, frontmatter: str
    ) -> None:
        await storage.save_page("Page", "original\n")
        before = (tmp_path / "Page.md").read_text(encoding="utf-8")
        field = frontmatter.split(":")[0]

        with pytest.raises(InvalidFrontmatterError, match=field):
            await storage.save_page("Page", f"---\n{frontmatter}\n---\n\nnew\n")
        with pytest.raises(InvalidFrontmatterError, match=field):
            await storage.save_page("Fresh", f"---\n{frontmatter}\n---\n\nnew\n")

        assert (tmp_path / "Page.md").read_text(encoding="utf-8") == before
        assert not (tmp_path / "Fresh.md").exists()

    async def test_save_accepts_a_previously_stashed_invalid_value(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await storage.save_page(
            "Page", "---\ncreated_invalid: yesterday\n---\n\nbody\n"
        )

        data = _frontmatter_on_disk(tmp_path / "Page.md")
        assert data["created_invalid"] == "yesterday"
        assert isinstance(datetime.fromisoformat(data["created"]), datetime)

    async def test_aware_timestamps_survive_a_save(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await storage.save_page(
            "Page", "---\ncreated: 2026-01-01T00:00:00Z\n---\n\nbody\n"
        )

        page = await storage.get_page("Page")
        assert page is not None
        assert page.metadata.created == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestSetAsideValuesAreLogged:
    """A write that renames a key to ``<field>_invalid`` says so in the log."""

    _FRONTMATTER = "created: yesterday\ntitle: [a, b]\ntags: python"

    @staticmethod
    def _set_aside(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in logs if e["event"] == "frontmatter_values_set_aside"]

    async def test_reading_and_scanning_stay_quiet(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", self._FRONTMATTER)

        with capture_logs() as logs:
            storage.list_pages_with_metadata_sync()
            await storage.search_pages("body")
            await storage.get_page("Bad")

        assert logs == []

    async def test_field_update_logs_the_renamed_keys_once(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        path = _write(tmp_path, "Bad.md", self._FRONTMATTER)

        with capture_logs() as logs:
            await storage.update_frontmatter_field("Bad", "note", "x")

        events = self._set_aside(logs)
        assert len(events) == 1
        assert events[0]["log_level"] == "warning"
        assert events[0]["page"] == "Bad"
        assert events[0]["file"].endswith("Bad.md")
        problems = events[0]["problems"]
        assert len(problems) == 2
        assert any(p.startswith("created:") for p in problems)
        assert any(p.startswith("title:") for p in problems)
        data = _frontmatter_on_disk(path)
        assert data["created_invalid"] == "yesterday"
        assert data["title_invalid"] == ["a", "b"]
        assert "created" not in data and "title" not in data

        # The file no longer has anything to set aside.
        with capture_logs() as logs:
            await storage.update_frontmatter_field("Bad", "note", "y")
        assert self._set_aside(logs) == []

    async def test_patch_frontmatter_logs_too(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", self._FRONTMATTER)

        with capture_logs() as logs:
            await storage.patch_frontmatter("Bad", {"status": "planned"})

        events = self._set_aside(logs)
        assert len(events) == 1
        assert events[0]["page"] == "Bad"

    async def test_rejected_update_writes_and_logs_nothing(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        path = _write(tmp_path, "Bad.md", self._FRONTMATTER)
        before = path.read_text(encoding="utf-8")

        with capture_logs() as logs:
            with pytest.raises(InvalidFrontmatterError):
                await storage.update_frontmatter_field("Bad", "modified", "never")

        assert self._set_aside(logs) == []
        assert path.read_text(encoding="utf-8") == before

    async def test_coercible_values_are_not_reported(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", "title: 2024\ntags: python\nchildren: A")

        with capture_logs() as logs:
            await storage.update_frontmatter_field("Bad", "note", "x")

        assert self._set_aside(logs) == []


class TestFieldUpdates:
    async def test_children_scalar_is_stored_as_a_list(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await storage.save_page("Page", "body\n")

        page = await storage.update_frontmatter_field("Page", "children", "A, B")

        assert page is not None
        assert page.metadata.children == ["A", "B"]
        assert _frontmatter_on_disk(tmp_path / "Page.md")["children"] == ["A", "B"]
        reread = await storage.get_page("Page")
        assert reread is not None
        assert reread.metadata.children == ["A", "B"]

    async def test_empty_value_clears_a_list_field(self, storage: FileStorage) -> None:
        await storage.save_page("Page", "---\nchildren: [A]\ntags: [t]\n---\n\nbody\n")

        await storage.update_frontmatter_field("Page", "children", "")
        page = await storage.update_frontmatter_field("Page", "tags", "")

        assert page is not None
        assert page.metadata.children == []
        assert page.metadata.tags == []

    @pytest.mark.parametrize(
        "field", ["model_dump", "model_extra", "model_config", "copy", "json"]
    )
    async def test_field_named_like_a_model_attribute_is_a_plain_extra(
        self, storage: FileStorage, tmp_path: Path, field: str
    ) -> None:
        await storage.save_page("Page", "body\n")

        await storage.update_frontmatter_field("Page", field, "x")

        assert _frontmatter_on_disk(tmp_path / "Page.md")[field] == "x"
        reread = await storage.get_page("Page")
        assert reread is not None
        assert reread.metadata.model_extra[field] == "x"
        await storage.update_frontmatter_field("Page", field, "")
        assert field not in _frontmatter_on_disk(tmp_path / "Page.md")

    @pytest.mark.parametrize("value", ["123", "yes", "2026-11-10", "null", "[a]"])
    async def test_text_that_looks_like_another_yaml_type_stays_text(
        self, storage: FileStorage, value: str
    ) -> None:
        await storage.save_page("Page", "body\n")

        await storage.update_frontmatter_field("Page", "title", value)
        await storage.update_frontmatter_field("Page", "parent_task", value)

        page = await storage.get_page("Page")
        assert page is not None
        assert page.metadata.title == value
        assert page.metadata.model_extra["parent_task"] == value

    async def test_date_field_rejects_an_unparseable_value(
        self, storage: FileStorage, tmp_path: Path
    ) -> None:
        await storage.save_page("Page", "body\n")
        before = (tmp_path / "Page.md").read_text(encoding="utf-8")

        with pytest.raises(InvalidFrontmatterError, match="created"):
            await storage.update_frontmatter_field("Page", "created", "yesterday")
        with pytest.raises(InvalidFrontmatterError, match="created"):
            await storage.patch_frontmatter("Page", {"created": "yesterday"})

        assert (tmp_path / "Page.md").read_text(encoding="utf-8") == before

    async def test_patch_frontmatter_keeps_its_semantics(
        self, storage: FileStorage
    ) -> None:
        await storage.save_page("Page", "---\nold: 1\nkeep: 2\n---\n\nbody\n")

        page = await storage.patch_frontmatter(
            "Page",
            {
                "children": "A\nB, C",
                "tags": "x, y",
                "title": "T",
                "old": None,
                "empty": "",
                "created": "2026-01-02T03:04:05",
            },
        )

        assert page is not None
        assert page.metadata.children == ["A", "B", "C"]
        assert page.metadata.tags == ["x", "y"]
        assert page.metadata.title == "T"
        assert page.metadata.created == datetime(2026, 1, 2, 3, 4, 5)
        assert page.metadata.model_extra == {"keep": 2, "empty": ""}
        reread = await storage.get_page("Page")
        assert reread is not None
        assert reread.metadata == page.metadata


# ---------------------------------------------------------------------------
# Sidebar tree builder and date sorts
# ---------------------------------------------------------------------------


def _page(name: str, **frontmatter: Any) -> Page:
    return Page(name=name, content="", metadata=PageMetadata(**frontmatter))


def _find(tree: list[dict], name: str) -> dict | None:
    for node in tree:
        if node["name"] == name:
            return node
        found = _find(node["children"], name)
        if found:
            return found
    return None


class TestPageTreeTolerance:
    @pytest.mark.parametrize(
        "parent_task", [["Epic"], [["Epic"]], "Epic", ("Epic",), [["Epic"], "Other"]]
    )
    def test_list_valued_parent_task_uses_the_first_reference(
        self, parent_task: Any
    ) -> None:
        from meshwiki.main import build_page_tree_sync

        tree = build_page_tree_sync(
            [_page("Epic"), _page("Task", parent_task=parent_task)]
        )

        epic = _find(tree, "Epic")
        assert epic is not None
        assert [c["name"] for c in epic["children"]] == ["Task"]

    @pytest.mark.parametrize(
        "parent_task", [42, 4.5, True, {"a": 1}, [], [[]], [{"a": 1}], None, ""]
    )
    def test_odd_parent_task_never_breaks_the_tree(self, parent_task: Any) -> None:
        from meshwiki.main import build_page_tree_sync

        tree = build_page_tree_sync(
            [_page("Home"), _page("Task", parent_task=parent_task)]
        )

        assert _find(tree, "Home") is not None
        assert _find(tree, "Task") is not None

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("done", "done"),
            (["review", "x"], "review"),
            ([], ""),
            (3, "3"),
            ({"a": 1}, ""),
            ([{"a": 1}], ""),
            (None, ""),
        ],
    )
    def test_status_is_always_text(self, status: Any, expected: str) -> None:
        from meshwiki.main import build_page_tree_sync

        tree = build_page_tree_sync([_page("Task", status=status)])

        assert tree[0]["status"] == expected

    @pytest.mark.parametrize("value", [["task"], {"a": 1}, 5])
    def test_odd_type_assignee_and_sidebar_values(self, value: Any) -> None:
        from meshwiki.main import build_page_tree_sync

        tree = build_page_tree_sync(
            [_page("Task", type=value, assignee=value, sidebar=value, parent=value)]
        )

        assert _find(tree, "Task") is not None


class TestRecentChangesTolerance:
    def test_macro_sorts_mixed_naive_and_aware_timestamps(self) -> None:
        pages = [
            _page("Naive", modified=datetime.now() - timedelta(hours=2)),
            _page("Aware", modified=datetime.now(timezone.utc) - timedelta(hours=1)),
            _page("Newest", modified=datetime.now()),
            _page("Undated"),
        ]

        html = parse_wiki_content("<<RecentChanges(10)>>", recent_pages=pages)

        order = [html.index(name) for name in ("Newest", "Aware", "Naive", "Undated")]
        assert order == sorted(order)


# ---------------------------------------------------------------------------
# HTML routes with a bad file present
# ---------------------------------------------------------------------------


class TestRoutesSurviveBadPage:
    @pytest.mark.parametrize("shape", [*BAD_SHAPES, *BAD_EXTRAS])
    async def test_every_route_still_works(
        self, client, tmp_path: Path, shape: str
    ) -> None:
        frontmatter = BAD_SHAPES[shape][0] if shape in BAD_SHAPES else BAD_EXTRAS[shape]
        _write(tmp_path, "Bad.md", frontmatter)
        await _seed_good_page(tmp_path)

        for url in [*HTML_ROUTES, "/page/Bad", "/page/Bad/edit"]:
            resp = await client.get(url)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}"

        index = (await client.get("/")).text
        assert 'href="/page/Good" class="page-tree-link' in index
        assert 'href="/page/Bad" class="page-tree-link' in index
        search = (await client.get("/search?q=body")).text
        assert "/page/Good" in search and "/page/Bad" in search
        assert "python" in (await client.get("/tags")).text
        assert "body" in (await client.get("/page/Bad")).text

    async def test_bad_page_can_be_repaired_through_the_editor(
        self, client, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Bad.md", "title: 2024\ntags: python\nchildren: A")

        edit = await client.get("/page/Bad/edit")
        assert edit.status_code == 200
        assert "title: 2024" in edit.text

        resp = await client.post(
            "/page/Bad", data={"content": "---\ntitle: Fixed\n---\n\nbody\n"}
        )
        assert resp.status_code == 200
        assert _frontmatter_on_disk(tmp_path / "Bad.md")["title"] == "Fixed"

    async def test_unreadable_files_are_skipped_everywhere(
        self, client, tmp_path: Path
    ) -> None:
        await _seed_good_page(tmp_path)
        (tmp_path / "Latin.md").write_bytes(b"---\ntitle: Caf\xe9\n---\n\nbody \xe4\n")
        (tmp_path / "x.md").mkdir()

        for url in HTML_ROUTES:
            resp = await client.get(url)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}"

        index = (await client.get("/")).text
        assert 'href="/page/Good" class="page-tree-link' in index
        assert "/page/Latin" not in index
        search = (await client.get("/search?q=body")).text
        assert "/page/Good" in search and "/page/Latin" not in search

    async def test_include_macro_survives_an_unreadable_file(
        self, client, tmp_path: Path
    ) -> None:
        await _seed_good_page(tmp_path)
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")
        (tmp_path / "Host.md").write_text("<<Include(Good)>>\n", encoding="utf-8")

        resp = await client.get("/page/Host")

        assert resp.status_code == 200
        assert "body" in resp.text

    @pytest.mark.parametrize(
        "url",
        [
            "/page/Latin",
            "/page/Latin/edit",
            "/page/Latin/raw",
            "/api/pages/Latin/fragment",
        ],
    )
    async def test_viewing_an_unreadable_page_explains_the_problem(
        self, client, tmp_path: Path, url: str
    ) -> None:
        await _seed_good_page(tmp_path)
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")

        resp = await client.get(url)

        assert resp.status_code == 500
        assert "Latin" in resp.text
        assert "UTF-8" in resp.text
        assert "Traceback" not in resp.text
        if url.startswith("/api/"):
            assert "UTF-8" in resp.json()["detail"]
        else:
            assert resp.headers["content-type"].startswith("text/html")

    async def test_unreadable_page_can_be_deleted_from_the_ui(
        self, client, tmp_path: Path
    ) -> None:
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")

        resp = await client.post("/page/Latin/delete")

        assert resp.status_code == 200
        assert not (tmp_path / "Latin.md").exists()

    async def test_directory_named_like_a_page_offers_to_create_the_page(
        self, client, tmp_path: Path
    ) -> None:
        (tmp_path / "x.md").mkdir()

        resp = await client.get("/page/x")

        assert resp.status_code == 200
        assert 'id="edit-form"' in resp.text

    async def test_epic_page_survives_oddly_typed_child_tasks(
        self, client, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Epic.md", "type: epic", body="<<EpicStatus>>\n")
        _write(tmp_path, "TaskA.md", "parent_task: [[Epic]]\nstatus: {a: 1}")
        _write(tmp_path, "TaskB.md", "parent_task: Epic\nstatus: 3")
        _write(tmp_path, "TaskC.md", "parent_task: {a: 1}\nstatus: [done]")

        resp = await client.get("/page/Epic")

        assert resp.status_code == 200
        assert "0 / 2 tasks complete" in resp.text

    async def test_index_and_view_sort_mixed_naive_and_aware_timestamps(
        self, client, tmp_path: Path
    ) -> None:
        await _seed_good_page(tmp_path)
        _write(tmp_path, "Aware.md", "modified: 2026-01-01T00:00:00Z")
        _write(tmp_path, "AwareText.md", "modified: '2030-01-01T00:00:00+02:00'")
        _write(tmp_path, "Host.md", "title: Host", body="<<RecentChanges(10)>>\n")

        index = await client.get("/")
        assert index.status_code == 200
        recent = index.text[index.text.index("Recently Modified") :]
        assert recent.index("AwareText") < recent.index("Good") < recent.index("Aware<")

        for url in ("/page/Host", "/api/pages/Host/fragment"):
            resp = await client.get(url)
            assert resp.status_code == 200, url
            assert resp.text.index("AwareText") < resp.text.index("Good")


# ---------------------------------------------------------------------------
# PATCH /api/page/{name}/metadata
# ---------------------------------------------------------------------------


class TestMetadataPatchValidation:
    async def _patch(self, client, page: str, field: str, value: str):
        return await client.patch(
            f"/api/page/{page}/metadata", data={"field": field, "value": value}
        )

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("children", "A", ["A"]),
            ("children", "A, B_C", ["A", "B_C"]),
            ("children", "2024", ["2024"]),
            ("tags", "python", ["python"]),
            ("tags", "a, 2024, yes", ["a", "2024", "yes"]),
        ],
    )
    async def test_list_fields_are_written_as_lists(
        self, client, tmp_path: Path, field: str, value: str, expected: list[str]
    ) -> None:
        import meshwiki.main

        await client.post("/page/Home", data={"content": "body\n"})

        resp = await self._patch(client, "Home", field, value)

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert _frontmatter_on_disk(tmp_path / "Home.md")[field] == expected
        page = await meshwiki.main.storage.get_page("Home")
        assert getattr(page.metadata, field) == expected
        for url in ("/", "/page/Home", "/search?q=body", "/tags", "/page/Home/edit"):
            assert (await client.get(url)).status_code == 200, url

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("title", "2024"),
            ("title", "yes"),
            ("parent_task", "42"),
            ("status", "[a]"),
            ("model_dump", "x"),
            ("model_extra", "x"),
        ],
    )
    async def test_text_values_cannot_poison_the_wiki(
        self, client, tmp_path: Path, field: str, value: str
    ) -> None:
        await client.post("/page/Home", data={"content": "body\n"})

        resp = await self._patch(client, "Home", field, value)

        assert resp.status_code == 200
        assert _frontmatter_on_disk(tmp_path / "Home.md")[field] == value
        for url in ("/", "/page/Home", "/search?q=body", "/tags", "/page/Home/edit"):
            assert (await client.get(url)).status_code == 200, url

    @pytest.mark.parametrize("field", ["uuid", "created", "modified", "name"])
    async def test_identity_and_timestamp_fields_are_protected(
        self, client, tmp_path: Path, field: str
    ) -> None:
        await client.post("/page/Home", data={"content": "body\n"})
        before = (tmp_path / "Home.md").read_text(encoding="utf-8")

        resp = await self._patch(client, "Home", field, "123")

        assert resp.status_code == 400
        assert "cannot be edited" in resp.json()["detail"]
        assert (tmp_path / "Home.md").read_text(encoding="utf-8") == before

    async def test_storage_rejection_becomes_a_422_with_a_message(
        self, client, monkeypatch
    ) -> None:
        import meshwiki.main

        await client.post("/page/Home", data={"content": "body\n"})

        async def reject(name: str, field: str, value: str) -> None:
            raise InvalidFrontmatterError(["children: expected a list of text"])

        monkeypatch.setattr(meshwiki.main.storage, "update_frontmatter_field", reject)

        resp = await self._patch(client, "Home", "children", "A")

        assert resp.status_code == 422
        assert "children: expected a list of text" in resp.json()["detail"]

    async def test_patch_on_an_unreadable_page_is_reported_as_json(
        self, client, tmp_path: Path
    ) -> None:
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")

        resp = await self._patch(client, "Latin", "status", "x")

        assert resp.status_code == 500
        assert "UTF-8" in resp.json()["detail"]
        assert (tmp_path / "Latin.md").read_bytes() == b"body \xe4\n"

    async def test_patch_refreshes_cached_listings(self, client) -> None:
        await client.post("/page/Home", data={"content": "body\n"})
        assert "freshtag" not in (await client.get("/tags")).text

        resp = await self._patch(client, "Home", "tags", "freshtag")

        assert resp.status_code == 200
        assert "freshtag" in (await client.get("/tags")).text


# ---------------------------------------------------------------------------
# POST /page/{name} (editor save)
# ---------------------------------------------------------------------------


class TestSaveRouteValidation:
    @pytest.mark.parametrize(
        "frontmatter", ["created: yesterday", "title: [a, b]", "tags: {a: 1}"]
    )
    async def test_unfixable_frontmatter_is_a_422_and_nothing_is_written(
        self, client, tmp_path: Path, frontmatter: str
    ) -> None:
        field = frontmatter.split(":")[0]

        resp = await client.post(
            "/page/New", data={"content": f"---\n{frontmatter}\n---\n\nbody\n"}
        )

        assert resp.status_code == 422
        assert field in resp.json()["detail"]
        assert not (tmp_path / "New.md").exists()
        assert (await client.get("/")).status_code == 200

    @pytest.mark.parametrize("shape", ["title_int", "tags_scalar", "children_scalar"])
    async def test_coercible_frontmatter_is_saved_normalised(
        self, client, tmp_path: Path, shape: str
    ) -> None:
        frontmatter, attr, expected = BAD_SHAPES[shape]

        resp = await client.post(
            "/page/New", data={"content": f"---\n{frontmatter}\n---\n\nbody\n"}
        )

        assert resp.status_code == 200
        assert _frontmatter_on_disk(tmp_path / "New.md")[attr] == expected

    @pytest.mark.parametrize("shape", TAGGED_SCALARS)
    async def test_frontmatter_yaml_cannot_build_is_saved_as_body_text(
        self, client, tmp_path: Path, shape: str
    ) -> None:
        frontmatter = TAGGED_SCALARS[shape]

        resp = await client.post(
            "/page/New", data={"content": f"---\n{frontmatter}\n---\n\nbody\n"}
        )

        # Treated like a YAML syntax error: kept as text, never a 500.
        assert resp.status_code == 200
        saved = (tmp_path / "New.md").read_text(encoding="utf-8")
        assert saved.endswith(f"---\n{frontmatter}\n---\n\nbody\n")
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/page/New")).status_code == 200

    async def test_htmx_save_with_unfixable_frontmatter_is_a_422(
        self, client, tmp_path: Path
    ) -> None:
        resp = await client.post(
            "/page/New",
            data={"content": "---\ncreated: yesterday\n---\n\nbody\n"},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 422
        assert "created" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# JSON API (/api/v1, factory enabled)
# ---------------------------------------------------------------------------

_AUTH = {"Authorization": "Bearer test-key-123"}
_TASK = "type: task\nassignee: factory\nstatus: planned"


@pytest.fixture
async def factory_client(tmp_path: Path):
    """HTTP client for an app with the factory JSON API mounted."""
    import importlib

    from httpx import ASGITransport, AsyncClient

    import meshwiki.config as cfg
    import meshwiki.main

    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_api_key="test-key-123",
        factory_webhook_url="",
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app), base_url="http://test"
    ) as c:
        yield c
    cfg.settings = original
    importlib.reload(meshwiki.main)


class TestJsonApi:
    async def test_listing_survives_bad_files(
        self, factory_client, tmp_path: Path
    ) -> None:
        await _seed_good_page(tmp_path)
        _write(tmp_path, "Bad.md", "title: 2024\ntags: python\ncreated: yesterday")
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")
        (tmp_path / "x.md").mkdir()

        resp = await factory_client.get("/api/v1/pages", headers=_AUTH)

        assert resp.status_code == 200
        pages = {p["name"]: p["metadata"] for p in resp.json()}
        assert set(pages) == {"Bad", "Good"}
        assert pages["Bad"]["title"] == "2024"
        assert pages["Bad"]["tags"] == ["python"]
        assert pages["Bad"]["created_invalid"] == "yesterday"
        tasks = await factory_client.get("/api/v1/tasks", headers=_AUTH)
        assert tasks.status_code == 200

    async def test_unreadable_page_is_a_json_error(
        self, factory_client, tmp_path: Path
    ) -> None:
        (tmp_path / "Latin.md").write_bytes(b"body \xe4\n")

        resp = await factory_client.get("/api/v1/pages/Latin", headers=_AUTH)

        assert resp.status_code == 500
        assert "UTF-8" in resp.json()["detail"]

    async def test_put_rejects_unfixable_frontmatter_with_422(
        self, factory_client, tmp_path: Path
    ) -> None:
        resp = await factory_client.put(
            "/api/v1/pages/New",
            json={"name": "New", "content": "---\ncreated: yesterday\n---\nbody"},
            headers=_AUTH,
        )

        assert resp.status_code == 422
        assert "created" in resp.json()["detail"]
        assert not (tmp_path / "New.md").exists()

    async def test_put_normalises_coercible_frontmatter(
        self, factory_client, tmp_path: Path
    ) -> None:
        resp = await factory_client.put(
            "/api/v1/pages/New",
            json={"name": "New", "content": "---\ntags: python\n---\nbody"},
            headers=_AUTH,
        )

        assert resp.status_code == 200
        assert resp.json()["metadata"]["tags"] == ["python"]
        assert _frontmatter_on_disk(tmp_path / "New.md")["tags"] == ["python"]

    async def test_patch_validates_declared_fields(
        self, factory_client, tmp_path: Path
    ) -> None:
        await _seed_good_page(tmp_path)
        before = (tmp_path / "Good.md").read_text(encoding="utf-8")

        bad = await factory_client.patch(
            "/api/v1/pages/Good",
            json={"fields": {"status": "x", "created": "yesterday"}},
            headers=_AUTH,
        )
        assert bad.status_code == 422
        assert "created" in bad.json()["detail"]
        assert (tmp_path / "Good.md").read_text(encoding="utf-8") == before

        good = await factory_client.patch(
            "/api/v1/pages/Good",
            json={"fields": {"children": "A\nB", "status": "x"}},
            headers=_AUTH,
        )
        assert good.status_code == 200
        assert good.json()["metadata"]["children"] == ["A", "B"]

    @pytest.mark.parametrize("shape", UNSERIALISABLE)
    async def test_listings_survive_a_value_json_cannot_hold(
        self, factory_client, tmp_path: Path, shape: str
    ) -> None:
        _write(tmp_path, "Good.md", _TASK)
        _write(tmp_path, "Bad.md", f"{_TASK}\n{UNSERIALISABLE[shape]}")
        urls = [
            "/api/v1/pages",
            "/api/v1/pages?status=planned",
            "/api/v1/tasks",
            "/api/v1/tasks?status=planned",
            "/api/factory/tasks",
            "/api/factory/tasks?status=planned",
        ]

        for url in urls:
            resp = await factory_client.get(url, headers=_AUTH)
            assert resp.status_code == 200, f"{url} -> {resp.status_code}"
            assert {p["name"] for p in resp.json()} == {"Bad", "Good"}, url
        single = await factory_client.get("/api/v1/pages/Bad", headers=_AUTH)
        assert single.status_code == 200
        assert single.json()["metadata"]["status"] == "planned"

    async def test_agent_listing_survives_a_value_json_cannot_hold(
        self, factory_client, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Good.md", "type: agent\nstatus: idle")
        _write(tmp_path, "Bad.md", "type: agent\nstatus: idle\nx: !!binary gICA")

        listed = await factory_client.get("/api/v1/agents", headers=_AUTH)
        single = await factory_client.get("/api/v1/agents/Bad", headers=_AUTH)

        assert listed.status_code == 200
        assert {a["name"] for a in listed.json()} == {"Bad", "Good"}
        assert single.status_code == 200
        assert single.json()["metadata"]["x"] == "\ufffd\ufffd\ufffd"

    async def test_json_shows_a_stand_in_and_the_file_keeps_the_value(
        self, factory_client, tmp_path: Path
    ) -> None:
        path = _write(
            tmp_path, "Bad.md", f"{_TASK}\nblob: !!binary gICA\ntags: &a [*a]"
        )

        resp = await factory_client.post(
            "/api/v1/tasks/Bad/transition",
            json={"status": "in_progress"},
            headers=_AUTH,
        )

        assert resp.status_code == 200
        metadata = resp.json()["metadata"]
        assert metadata["status"] == "in_progress"
        assert metadata["blob"] == "\ufffd\ufffd\ufffd"
        assert metadata["tags"] == []
        assert metadata["tags_invalid"] == [SELF_REFERENCE_TEXT]
        on_disk = _frontmatter_on_disk(path)
        assert on_disk["status"] == "in_progress"
        assert on_disk["blob"] == b"\x80\x80\x80"
        assert isinstance(on_disk["tags_invalid"], list)


# ---------------------------------------------------------------------------
# Factory routes with list- or mapping-valued ``type`` / ``status``
# ---------------------------------------------------------------------------

# id -> frontmatter of a task page whose type or status is not plain text
ODD_TASKS: dict[str, str] = {
    "status_list": "type: task\nassignee: factory\nstatus: [planned]",
    "status_mapping": "type: task\nassignee: factory\nstatus: {a: 1}",
    "status_nested_list": "type: task\nassignee: factory\nstatus: [[planned]]",
    "type_list": "type: [task]\nassignee: factory\nstatus: planned",
    "type_mapping": "type: {a: 1}\nassignee: factory\nstatus: planned",
    "both_lists": "type: [task]\nassignee: factory\nstatus: [planned]",
}


class TestFactoryOddTypeAndStatus:
    @pytest.mark.parametrize("shape", ODD_TASKS)
    async def test_dashboard_task_filter_survives(
        self, factory_client, tmp_path: Path, shape: str
    ) -> None:
        _write(tmp_path, "Good.md", _TASK)
        _write(tmp_path, "Bad.md", ODD_TASKS[shape])

        # factory.js fetches one status per dashboard column.
        for status in ("planned", "in_progress", "done"):
            resp = await factory_client.get(f"/api/factory/tasks?status={status}")
            assert resp.status_code == 200, status
            names = [t["name"] for t in resp.json()]
            assert ("Good" in names) is (status == "planned")
            # Not plain text, so it is in no column, as in /api/v1/tasks.
            if "status_" in shape or shape == "both_lists":
                assert "Bad" not in names
        unfiltered = await factory_client.get("/api/factory/tasks")
        assert unfiltered.status_code == 200
        assert "Good" in [t["name"] for t in unfiltered.json()]
        api = await factory_client.get("/api/v1/tasks?status=planned", headers=_AUTH)
        assert api.status_code == 200

    @pytest.mark.parametrize("shape", ODD_TASKS)
    async def test_editor_can_repair_the_page(
        self, factory_client, tmp_path: Path, shape: str
    ) -> None:
        path = _write(tmp_path, "Bad.md", ODD_TASKS[shape])

        resp = await factory_client.post(
            "/page/Bad", data={"content": f"---\n{_TASK}\n---\n\nfixed\n"}
        )

        assert resp.status_code == 302
        data = _frontmatter_on_disk(path)
        assert data["type"] == "task"
        assert data["status"] == "planned"
        assert path.read_text(encoding="utf-8").endswith("fixed\n")

    @pytest.mark.parametrize("shape", ODD_TASKS)
    async def test_editor_can_resave_the_page_unchanged(
        self, factory_client, tmp_path: Path, shape: str
    ) -> None:
        path = _write(tmp_path, "Bad.md", ODD_TASKS[shape])
        before = _frontmatter_on_disk(path)

        resp = await factory_client.post(
            "/page/Bad", data={"content": path.read_text(encoding="utf-8")}
        )

        assert resp.status_code == 302
        after = _frontmatter_on_disk(path)
        assert after["type"] == before["type"]
        assert after["status"] == before["status"]

    @pytest.mark.parametrize("shape", ["status_list", "type_list", "both_lists"])
    async def test_status_change_still_goes_through_the_state_machine(
        self, factory_client, tmp_path: Path, shape: str
    ) -> None:
        path = _write(tmp_path, "Bad.md", ODD_TASKS[shape])
        before = path.read_text(encoding="utf-8")

        refused = await factory_client.post(
            "/page/Bad",
            data={"content": "---\ntype: task\nstatus: done\n---\n\nbody\n"},
        )

        assert refused.status_code == 422
        assert "Cannot transition from 'planned' to 'done'" in refused.json()["detail"]
        assert path.read_text(encoding="utf-8") == before

        allowed = await factory_client.post(
            "/page/Bad",
            data={
                "content": "---\ntype: task\nassignee: factory\n"
                "status: in_progress\n---\n\nbody\n"
            },
        )

        assert allowed.status_code == 302
        assert _frontmatter_on_disk(path)["status"] == "in_progress"
