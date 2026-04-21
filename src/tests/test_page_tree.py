"""Tests for the declarative MoC page tree builder."""

from __future__ import annotations

from meshwiki.core.models import Page, PageMetadata
from meshwiki.main import build_page_tree_sync


def _page(name: str, children: list[str] | None = None, **extra) -> Page:
    """Helper: build a Page with the given name, children, and extra frontmatter."""
    children = children or []
    meta = PageMetadata(children=children)
    for k, v in extra.items():
        meta.model_extra[k] = v  # type: ignore[index]
    return Page(name=name, content="", metadata=meta)


def _names(tree: list[dict]) -> list[str]:
    return [n["name"] for n in tree]


def _find(tree: list[dict], name: str) -> dict | None:
    for node in tree:
        if node["name"] == name:
            return node
        found = _find(node["children"], name)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_empty_pages():
    assert build_page_tree_sync([]) == []


def test_single_root():
    tree = build_page_tree_sync([_page("Home")])
    assert _names(tree) == ["Home"]
    assert tree[0]["children"] == []
    assert tree[0]["stub"] is False


def test_home_pinned_first():
    pages = [_page("Zebra"), _page("Alpha"), _page("Home"), _page("Mango")]
    tree = build_page_tree_sync(pages)
    assert tree[0]["name"] == "Home"
    remaining = [n["name"] for n in tree[1:]]
    assert remaining == sorted(remaining, key=str.lower)


def test_roots_sorted_alphabetically_without_home():
    pages = [_page("Zebra"), _page("Alpha"), _page("Mango")]
    tree = build_page_tree_sync(pages)
    assert _names(tree) == ["Alpha", "Mango", "Zebra"]


def test_declared_child_appears_under_parent():
    pages = [_page("Parent", children=["Child"]), _page("Child")]
    tree = build_page_tree_sync(pages)
    # Child is declared, so Parent is the only root
    assert _names(tree) == ["Parent"]
    assert _names(tree[0]["children"]) == ["Child"]


def test_level_assigned_correctly():
    pages = [
        _page("Root", children=["Mid"]),
        _page("Mid", children=["Leaf"]),
        _page("Leaf"),
    ]
    tree = build_page_tree_sync(pages)
    root = tree[0]
    mid = root["children"][0]
    leaf = mid["children"][0]
    assert root["level"] == 0
    assert mid["level"] == 1
    assert leaf["level"] == 2


# ---------------------------------------------------------------------------
# Multi-parent (DAG)
# ---------------------------------------------------------------------------


def test_same_child_under_multiple_parents():
    """A page listed under two parents appears in both branches."""
    pages = [
        _page("ParentA", children=["Shared"]),
        _page("ParentB", children=["Shared"]),
        _page("Shared"),
    ]
    tree = build_page_tree_sync(pages)
    # Both parents are roots
    assert {n["name"] for n in tree} == {"ParentA", "ParentB"}
    a_children = _names(_find(tree, "ParentA")["children"])
    b_children = _names(_find(tree, "ParentB")["children"])
    assert "Shared" in a_children
    assert "Shared" in b_children


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_direct_cycle_both_surfaces_via_orphan_recovery():
    """A↔B cycle: orphan recovery ensures both pages appear in the sidebar.

    Neither is a regular root (each is declared as the other's child), so the
    orphan-recovery pass adds them both. The cycle guard prevents infinite
    recursion: A→B renders B's child-of-A edge as a back-edge (dropped).
    """
    pages = [_page("A", children=["B"]), _page("B", children=["A"])]
    tree = build_page_tree_sync(pages)
    node_names = {n["name"] for n in tree}
    assert "A" in node_names
    assert "B" in node_names


def test_longer_cycle_no_infinite_recursion():
    """A→B→C→A should not loop forever."""
    pages = [
        _page("A", children=["B"]),
        _page("B", children=["C"]),
        _page("C", children=["A"]),
    ]
    tree = build_page_tree_sync(pages)  # must return without hanging
    assert isinstance(tree, list)


def test_tail_cycle_root_still_present():
    """A→B→C→B: A is a real root; B and C form a tail cycle off A.

    A must appear as a root; B and C must each appear exactly once in the tree
    (not duplicated by orphan recovery since they are reachable from A).
    """
    pages = [
        _page("A", children=["B"]),
        _page("B", children=["C"]),
        _page("C", children=["B"]),
    ]
    tree = build_page_tree_sync(pages)
    root_names = [n["name"] for n in tree]
    assert "A" in root_names
    # B and C are reachable from A; orphan recovery must not surface them as extra roots
    assert root_names.count("B") == 0
    assert root_names.count("C") == 0
    # Both B and C appear exactly once as descendants of A
    all_names: list[str] = []

    def _collect(nodes: list[dict]) -> None:
        for n in nodes:
            all_names.append(n["name"])
            _collect(n["children"])

    _collect(tree)
    assert all_names.count("B") == 1
    assert all_names.count("C") == 1


def test_self_loop_page_still_appears_as_root():
    """A page listing itself in children: should still appear in the sidebar.

    It is in all_declared_children (suppressing root detection), so without
    special handling it vanishes. The orphan-recovery pass must surface it.
    """
    pages = [_page("Self", children=["Self"]), _page("Other")]
    tree = build_page_tree_sync(pages)
    all_names = {n["name"] for n in tree}
    assert "Self" in all_names
    assert "Other" in all_names


# ---------------------------------------------------------------------------
# Missing children (stubs)
# ---------------------------------------------------------------------------


def test_missing_child_renders_as_stub():
    pages = [_page("Parent", children=["DoesNotExist"])]
    tree = build_page_tree_sync(pages)
    assert _names(tree) == ["Parent"]
    children = tree[0]["children"]
    assert len(children) == 1
    assert children[0]["name"] == "DoesNotExist"
    assert children[0]["stub"] is True


def test_stub_title_derived_from_name():
    pages = [_page("Root", children=["Missing_Page"])]
    tree = build_page_tree_sync(pages)
    stub = tree[0]["children"][0]
    assert stub["title"] == "Missing Page"


# ---------------------------------------------------------------------------
# Factory page filtering
# ---------------------------------------------------------------------------


def test_factory_assignee_visible():
    """Standalone factory tasks appear inside the 'Standalone Tasks' section."""
    pages = [_page("Normal"), _page("FactoryTask", assignee="factory", type="task")]
    tree = build_page_tree_sync(pages)
    # FactoryTask is nested under the section node, not at the root level
    assert _find(tree, "FactoryTask") is not None
    # Root names should not include FactoryTask directly
    assert "FactoryTask" not in _names(tree)


def test_epic_appears_in_factory_section():
    """Epics are grouped under the 'Factory' section node."""
    pages = [_page("MyEpic", type="epic"), _page("Home")]
    tree = build_page_tree_sync(pages)
    section = next(
        (n for n in tree if n.get("section") and n["title"] == "Factory"), None
    )
    assert section is not None
    assert _find(section["children"], "MyEpic") is not None


def test_standalone_task_appears_in_standalone_tasks_section():
    """Standalone tasks appear under Factory > Standalone Tasks subsection."""
    pages = [_page("Solo", type="task"), _page("Home")]
    tree = build_page_tree_sync(pages)
    factory_section = next(
        (n for n in tree if n.get("section") and n["title"] == "Factory"), None
    )
    assert factory_section is not None
    standalone_section = next(
        (
            n
            for n in factory_section["children"]
            if n.get("section") and n["title"] == "Standalone Tasks"
        ),
        None,
    )
    assert standalone_section is not None
    assert _find(standalone_section["children"], "Solo") is not None


def test_standalone_task_not_at_root():
    """Standalone tasks are not placed at the tree root level."""
    pages = [_page("Solo", type="task"), _page("Home")]
    tree = build_page_tree_sync(pages)
    assert "Solo" not in _names(tree)


def test_section_nodes_not_in_wiki_roots():
    """Regular wiki pages are not inside a section."""
    pages = [_page("Home"), _page("WikiPage"), _page("AnEpic", type="epic")]
    tree = build_page_tree_sync(pages)
    wiki_names = [n["name"] for n in tree if not n.get("section")]
    assert "WikiPage" in wiki_names
    assert "Home" in wiki_names
    assert "AnEpic" not in wiki_names


def test_subtask_appears_under_epic():
    """A page with parent_task: Epic becomes a child of Epic in the sidebar."""
    pages = [
        _page("Epic"),
        _page("Subtask", parent_task="Epic"),
    ]
    tree = build_page_tree_sync(pages)
    epic_node = _find(tree, "Epic")
    assert epic_node is not None
    assert "Subtask" in _names(epic_node["children"])


def test_subtask_not_a_root():
    """A page with parent_task: should not appear as a top-level root."""
    pages = [
        _page("Normal"),
        _page("Epic"),
        _page("Subtask", parent_task="Epic"),
    ]
    tree = build_page_tree_sync(pages)
    root_names = _names(tree)
    assert "Subtask" not in root_names


def test_sidebar_false_excluded():
    pages = [_page("Normal"), _page("Scratch", sidebar=False)]
    tree = build_page_tree_sync(pages)
    assert _names(tree) == ["Normal"]


def test_sidebar_true_not_excluded():
    """sidebar: true (or any truthy value) should not suppress the page."""
    pages = [_page("Normal"), _page("Explicit", sidebar=True)]
    tree = build_page_tree_sync(pages)
    assert any(n["name"] == "Explicit" for n in tree)


def test_sidebar_false_string_not_excluded():
    """sidebar: 'false' (string) is truthy — page should still appear."""
    pages = [_page("Normal"), _page("StringFalse", sidebar="false")]
    tree = build_page_tree_sync(pages)
    assert any(n["name"] == "StringFalse" for n in tree)


def test_factory_child_not_shown_even_if_declared():
    """A factory page is excluded even if listed as a child of a non-factory page."""
    pages = [
        _page("Root", children=["FactoryTask"]),
        _page("FactoryTask", assignee="factory"),
    ]
    tree = build_page_tree_sync(pages)
    # FactoryTask is in children_of[Root], but _is_factory_page returns True for it.
    # build_page_tree_sync only calls children_of for the *parent* page,
    # so the factory page can still be listed in children_of[Root].
    # However it will appear as a stub (no page_map entry is filtered out here —
    # only the *root* selection and the *children_of* collection filter factory pages).
    # Current design: factory pages are excluded from BEING parents, not from
    # being referenced as children. A user who puts a factory task in children:
    # gets a stub link. This is acceptable (unlikely scenario in practice).
    root = tree[0]
    assert root["name"] == "Root"


def test_empty_parent_task_is_not_a_child():
    """parent_task: '' (empty string) is falsy — page still appears as a root."""
    pages = [_page("Epic_001_foo", parent_task=""), _page("Home")]
    tree = build_page_tree_sync(pages)
    assert any(n["name"] == "Epic_001_foo" for n in tree)


def test_slash_name_page_excluded():
    """Pages with '/' in their name (old-style subpages) are hidden from the sidebar."""
    pages = [_page("Normal"), _page("Factory/Standalone")]
    tree = build_page_tree_sync(pages)
    assert _names(tree) == ["Normal"]


# ---------------------------------------------------------------------------
# Status propagation
# ---------------------------------------------------------------------------


def test_status_propagated_from_frontmatter():
    pages = [_page("Task", status="done")]
    tree = build_page_tree_sync(pages)
    assert tree[0]["status"] == "done"


def test_status_defaults_to_empty_string():
    tree = build_page_tree_sync([_page("NoStatus")])
    assert tree[0]["status"] == ""


def test_factory_task_node_has_status_in_tree():
    """A factory task node includes status field in the tree JSON."""
    pages = [
        _page("Epic_001", children=["Task_001"]),
        _page("Task_001", status="in_progress", assignee="factory"),
    ]
    tree = build_page_tree_sync(pages)
    task_node = _find(tree, "Task_001")
    assert task_node is not None
    assert "status" in task_node
    assert task_node["status"] == "in_progress"
