"""Unit tests for the <<EpicStatus>> macro."""

import importlib

import pytest

import meshwiki.main
from meshwiki.core.parser import parse_wiki_content

_EPIC_META = {
    "type": "epic",
    "title": "My Epic",
    "_child_tasks": [],
}

_CHILD_TASKS = [
    {"name": "Epic/Task1", "title": "Task One", "status": "merged"},
    {"name": "Epic/Task2", "title": "Task Two", "status": "in_progress"},
    {"name": "Epic/Task3", "title": "Task Three", "status": "planned"},
]


def render(
    content: str,
    page_name: str = "Factory/MyEpic",
    metadata: dict | None = None,
) -> str:
    if metadata is None:
        metadata = dict(_EPIC_META)
    return parse_wiki_content(content, page_name=page_name, page_metadata=metadata)


# ============================================================
# Regex / pattern matching
# ============================================================


class TestEpicStatusPattern:
    def test_no_parens_matches(self):
        html = render("<<EpicStatus>>")
        assert "epic-status-wrapper" in html

    def test_empty_parens_matches(self):
        html = render("<<EpicStatus()>>")
        assert "epic-status-wrapper" in html

    def test_not_replaced_inside_fenced_code_block(self):
        content = "```\n<<EpicStatus>>\n```"
        html = render(content)
        assert "epic-status-wrapper" not in html

    def test_not_replaced_inside_tilde_code_block(self):
        content = "~~~\n<<EpicStatus()>>\n~~~"
        html = render(content)
        assert "epic-status-wrapper" not in html


# ============================================================
# Guard / error cases
# ============================================================


class TestEpicStatusGuards:
    def test_error_when_wrong_type(self):
        html = render("<<EpicStatus>>", metadata={"type": "task"})
        assert "task-status-error" in html
        assert "EpicStatus" in html

    def test_error_when_type_missing(self):
        html = render("<<EpicStatus>>", metadata={})
        assert "task-status-error" in html

    def test_no_tasks_shows_hint(self):
        meta = {**_EPIC_META, "_child_tasks": []}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "epic-no-tasks" in html
        assert "Factory/MyEpic" in html


# ============================================================
# Progress bar
# ============================================================


class TestEpicStatusProgress:
    def test_progress_label_shown(self):
        meta = {**_EPIC_META, "_child_tasks": _CHILD_TASKS}
        html = render("<<EpicStatus>>", metadata=meta)
        # 1 merged out of 3
        assert "1 / 3 tasks complete" in html

    def test_all_complete_shows_100pct(self):
        tasks = [
            {"name": "Epic/T1", "title": "T1", "status": "done"},
            {"name": "Epic/T2", "title": "T2", "status": "merged"},
        ]
        meta = {**_EPIC_META, "_child_tasks": tasks}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "2 / 2 tasks complete" in html
        assert "width:100%" in html

    def test_zero_tasks_shows_zero_pct(self):
        meta = {**_EPIC_META, "_child_tasks": []}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "0 / 0 tasks complete" in html
        assert "width:0%" in html

    def test_done_and_merged_both_count_as_complete(self):
        tasks = [
            {"name": "E/T1", "title": "T1", "status": "done"},
            {"name": "E/T2", "title": "T2", "status": "merged"},
            {"name": "E/T3", "title": "T3", "status": "in_progress"},
        ]
        meta = {**_EPIC_META, "_child_tasks": tasks}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "2 / 3 tasks complete" in html


# ============================================================
# Mermaid diagram
# ============================================================


class TestEpicStatusDiagram:
    def test_mermaid_rendered_for_tasks(self):
        meta = {**_EPIC_META, "_child_tasks": _CHILD_TASKS}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "task-status-diagram" in html
        assert "mermaid" in html

    def test_task_title_in_diagram(self):
        meta = {**_EPIC_META, "_child_tasks": _CHILD_TASKS}
        html = render("<<EpicStatus>>", metadata=meta)
        # Mermaid source is html-escaped once when placed in the div
        assert "Task One" in html
        assert "Task Two" in html

    def test_ampersand_in_title_not_double_escaped(self):
        tasks = [{"name": "E/T1", "title": "Fix A & B", "status": "in_progress"}]
        meta = {**_EPIC_META, "_child_tasks": tasks}
        html = render("<<EpicStatus>>", metadata=meta)
        # Should contain &amp; (one level of HTML escaping) — NOT &amp;amp;
        assert "&amp;amp;" not in html

    def test_status_classdef_applied(self):
        meta = {**_EPIC_META, "_child_tasks": _CHILD_TASKS}
        html = render("<<EpicStatus>>", metadata=meta)
        assert ":::merged" in html or "classDef merged" in html

    def test_no_diagram_when_no_tasks(self):
        meta = {**_EPIC_META, "_child_tasks": []}
        html = render("<<EpicStatus>>", metadata=meta)
        assert "task-status-diagram" not in html


# ============================================================
# _child_tasks population in the view route
# ============================================================


@pytest.mark.asyncio
async def test_child_tasks_populated_for_epic(tmp_path):
    """The view route injects _child_tasks into epic page frontmatter."""
    from httpx import ASGITransport, AsyncClient

    import meshwiki.config as cfg

    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=False,
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)

    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(tmp_path, watch=False)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=meshwiki.main.app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            # Create an epic page
            epic_content = "---\ntype: epic\ntitle: My Epic\n---\n<<EpicStatus>>"
            await meshwiki.main.storage.save_page("MyEpic", epic_content)

            # Create a child task with parent_task pointing at the epic
            task_content = (
                "---\ntype: task\nstatus: in_progress\n"
                "parent_task: MyEpic\n---\nTask body"
            )
            await meshwiki.main.storage.save_page("MyEpic/Task1", task_content)

            # Create a second child as a subpage without explicit parent_task field
            task2_content = "---\ntype: task\nstatus: merged\n---\nTask 2"
            await meshwiki.main.storage.save_page("MyEpic/Task2", task2_content)

            resp = await client.get("/page/MyEpic")
            assert resp.status_code == 200
            body = resp.text
            # Progress bar should show 1 complete (merged) out of 2
            assert "1 / 2 tasks complete" in body
    finally:
        shutdown_engine()
        cfg.settings = original
        importlib.reload(meshwiki.main)


@pytest.mark.asyncio
async def test_child_tasks_uses_parent_task_field(tmp_path):
    """Tasks linked via parent_task (not subpath) are included in _child_tasks."""
    from httpx import ASGITransport, AsyncClient

    import meshwiki.config as cfg

    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=False,
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)

    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(tmp_path, watch=False)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=meshwiki.main.app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            # Epic and task use flat names (post-MoC; slashes are invalid)
            await meshwiki.main.storage.save_page(
                "Sprint1",
                "---\ntype: epic\ntitle: Sprint 1\n---\n<<EpicStatus>>",
            )
            await meshwiki.main.storage.save_page(
                "FixBug",
                "---\ntype: task\nstatus: done\nparent_task: Sprint1\n---\nBody",
            )

            resp = await client.get("/page/Sprint1")
            assert resp.status_code == 200
            assert "1 / 1 tasks complete" in resp.text
    finally:
        shutdown_engine()
        cfg.settings = original
        importlib.reload(meshwiki.main)
