"""Unit tests for the <<TaskStatus>> macro."""

import pytest

from meshwiki.core.parser import parse_wiki_content


def render(
    content: str, page_name: str = "Task_001", metadata: dict | None = None
) -> str:
    """Helper: render wiki content with TaskStatus context."""
    if metadata is None:
        metadata = {"type": "task", "status": "draft"}
    return parse_wiki_content(content, page_name=page_name, page_metadata=metadata)


# ============================================================
# Error / guard cases
# ============================================================


class TestTaskStatusGuards:
    def test_error_when_no_metadata(self):
        html = parse_wiki_content(
            "<<TaskStatus>>", page_name="Task_001", page_metadata=None
        )
        assert "task-status-error" in html
        assert "TaskStatus" in html

    def test_error_when_wrong_type(self):
        html = render("<<TaskStatus>>", metadata={"type": "page", "status": "draft"})
        assert "task-status-error" in html

    def test_error_when_type_missing(self):
        html = render("<<TaskStatus>>", metadata={"status": "draft"})
        assert "task-status-error" in html

    def test_not_replaced_inside_fenced_code_block(self):
        content = "```\n<<TaskStatus>>\n```"
        html = render(content, metadata={"type": "task", "status": "draft"})
        # Macro must not be rendered; code block escapes < > to HTML entities.
        assert "task-status-wrapper" not in html
        assert "&lt;&lt;TaskStatus&gt;&gt;" in html

    def test_not_replaced_inside_tilde_code_block(self):
        content = "~~~\n<<TaskStatus>>\n~~~"
        html = render(content, metadata={"type": "task", "status": "draft"})
        assert "task-status-wrapper" not in html


# ============================================================
# Status badge
# ============================================================


class TestStatusBadge:
    @pytest.mark.parametrize(
        "status,expected_class",
        [
            ("draft", "task-status-badge--gray"),
            ("planned", "task-status-badge--gray"),
            ("decomposed", "task-status-badge--gray"),
            ("approved", "task-status-badge--blue"),
            ("in_progress", "task-status-badge--amber"),
            ("review", "task-status-badge--purple"),
            ("merged", "task-status-badge--green"),
            ("done", "task-status-badge--green"),
            ("failed", "task-status-badge--red"),
            ("rejected", "task-status-badge--red"),
            ("blocked", "task-status-badge--orange"),
        ],
    )
    def test_badge_class_per_state(self, status, expected_class):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": status})
        assert expected_class in html

    def test_badge_text_shown(self):
        html = render(
            "<<TaskStatus>>", metadata={"type": "task", "status": "in_progress"}
        )
        assert "in progress" in html  # underscores replaced by spaces


# ============================================================
# Mermaid diagram classDef assignments
# ============================================================


class TestMermaidDiagram:
    def test_mermaid_block_present(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "draft"})
        assert "flowchart LR" in html
        assert "class=" not in html or "mermaid" in html  # sanity

    def test_in_progress_nodes(self):
        html = render(
            "<<TaskStatus>>", metadata={"type": "task", "status": "in_progress"}
        )
        # Nodes before in_progress should be "done"
        assert "class draft,planned,decomposed,approved done" in html
        # in_progress itself should be "current"
        assert "class in_progress current" in html
        # Nodes after should be "pending"
        assert "class review,merged,done pending" in html

    def test_done_all_green(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "done"})
        # All nodes before "done" should be in the "done" class (green).
        assert (
            "class draft,planned,decomposed,approved,in_progress,review,merged done"
            in html
        )
        # "done" itself is the current node (also rendered green).
        assert "class done current" in html
        # No future/pending nodes.
        assert "class" not in html.split("class done current")[1].split("classDef")[0]

    def test_draft_all_pending(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "draft"})
        # draft is current, everything else pending
        assert "class draft current" in html
        assert (
            "class planned,decomposed,approved,in_progress,review,merged,done pending"
            in html
        )

    def test_failed_node_added(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "failed"})
        # failed side-branch should be in the diagram source
        assert "failed(failed)" in html
        assert "class failed current" in html
        # in_progress and before should be done
        assert "in_progress" in html.split("class ")[1] if "class " in html else True

    def test_failed_predecessor_nodes_done(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "failed"})
        assert "class draft,planned,decomposed,approved,in_progress done" in html

    def test_rejected_node_added(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "rejected"})
        assert "rejected(rejected)" in html
        assert "class rejected current" in html
        assert "class draft,planned,decomposed,approved,in_progress,review done" in html

    def test_current_color_red_for_failed(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "failed"})
        assert "#ef4444" in html

    def test_current_color_green_for_done(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "done"})
        # green fill used for current
        assert "#22c55e" in html


# ============================================================
# Metadata row
# ============================================================


class TestMetadataRow:
    def test_pr_link_shown_when_pr_url_present(self):
        html = render(
            "<<TaskStatus>>",
            metadata={
                "type": "task",
                "status": "review",
                "pr_url": "https://github.com/org/repo/pull/42",
                "pr_number": "42",
            },
        )
        assert 'href="https://github.com/org/repo/pull/42"' in html
        assert "#42" in html

    def test_pr_link_absent_when_no_pr_url(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "draft"})
        assert "href=" not in html or "wiki-link" in html  # only wiki links, no PR link

    def test_assignee_shown(self):
        html = render(
            "<<TaskStatus>>",
            metadata={"type": "task", "status": "in_progress", "assignee": "grinder-1"},
        )
        assert "grinder-1" in html
        assert "Assignee" in html

    def test_branch_shown_as_code(self):
        html = render(
            "<<TaskStatus>>",
            metadata={
                "type": "task",
                "status": "in_progress",
                "branch": "factory/task-001",
            },
        )
        assert "<code>factory/task-001</code>" in html

    def test_parent_task_wiki_link(self):
        html = render(
            "<<TaskStatus>>",
            metadata={
                "type": "task",
                "status": "in_progress",
                "parent_task": "Epic_001",
            },
        )
        assert 'href="/page/Epic_001"' in html
        assert "Epic_001" in html

    def test_no_metadata_row_when_no_fields(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "draft"})
        # task-status-meta div should not appear if no meta fields set
        assert "task-status-meta" not in html


# ============================================================
# Live terminal section
# ============================================================


class TestTerminalSection:
    def test_terminal_present_for_in_progress(self):
        html = render(
            "<<TaskStatus>>", metadata={"type": "task", "status": "in_progress"}
        )
        assert "task-status-terminal" in html
        assert "task-terminal-body" in html

    def test_websocket_url_in_script(self):
        html = render(
            "<<TaskStatus>>",
            page_name="factory/Task_001",
            metadata={"type": "task", "status": "in_progress"},
        )
        assert "/ws/terminal/" in html
        assert "factory/Task_001" in html

    def test_xterm_constructor_in_script(self):
        html = render(
            "<<TaskStatus>>", metadata={"type": "task", "status": "in_progress"}
        )
        assert "new Terminal(" in html

    def test_terminal_absent_for_draft(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "draft"})
        assert "task-status-terminal" not in html

    def test_terminal_absent_for_done(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "done"})
        assert "task-status-terminal" not in html

    def test_terminal_absent_for_failed(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "failed"})
        assert "task-status-terminal" not in html

    def test_terminal_absent_for_review(self):
        html = render("<<TaskStatus>>", metadata={"type": "task", "status": "review"})
        assert "task-status-terminal" not in html

    def test_page_name_xss_safe(self):
        """page_name is embedded via json.dumps — angle brackets must be escaped."""
        html = render(
            "<<TaskStatus>>",
            page_name="<script>alert(1)</script>",
            metadata={"type": "task", "status": "in_progress"},
        )
        assert "<script>alert(1)</script>" not in html


# ============================================================
# terminal_sessions module
# ============================================================


class TestTerminalSessions:
    def setup_method(self):
        from meshwiki.core import terminal_sessions

        terminal_sessions._sessions.clear()

    def test_create_and_get_session(self):
        from meshwiki.core.terminal_sessions import create_session, get_session

        create_session("TestTask")
        assert get_session("TestTask") is not None

    def test_get_nonexistent_session_returns_none(self):
        from meshwiki.core.terminal_sessions import get_session

        assert get_session("NonExistent") is None

    def test_put_chunk_buffers_and_fans_out(self):
        import asyncio

        from meshwiki.core.terminal_sessions import (
            create_session,
            get_session,
            put_chunk,
            subscribe,
        )

        create_session("TestTask2")
        sub_q = subscribe("TestTask2")

        async def _run():
            await put_chunk("TestTask2", "hello")
            session = get_session("TestTask2")
            assert session.buffer == ["hello"]
            return sub_q.get_nowait()

        result = asyncio.run(_run())
        assert result == "hello"

    def test_close_session_keeps_buffer_sends_sentinel(self):
        import asyncio

        from meshwiki.core.terminal_sessions import (
            close_session,
            create_session,
            get_session,
            put_chunk,
            subscribe,
        )

        create_session("TestTask3")
        sub_q = subscribe("TestTask3")

        async def _run():
            await put_chunk("TestTask3", "line1")
            await close_session("TestTask3")

        asyncio.run(_run())

        session = get_session("TestTask3")
        assert session is not None  # buffer kept after close
        assert session.closed is True
        assert session.buffer == ["line1"]
        assert sub_q.get_nowait() == "line1"
        assert sub_q.get_nowait() is None  # sentinel

    def test_subscribe_returns_none_for_closed_session(self):
        import asyncio

        from meshwiki.core.terminal_sessions import (
            close_session,
            create_session,
            subscribe,
        )

        create_session("TestTask4")
        asyncio.run(close_session("TestTask4"))
        assert subscribe("TestTask4") is None

    def test_replay_buffer_on_reconnect(self):
        import asyncio

        from meshwiki.core.terminal_sessions import (
            create_session,
            get_session,
            put_chunk,
        )

        create_session("TestTask5")

        async def _run():
            await put_chunk("TestTask5", "a")
            await put_chunk("TestTask5", "b")

        asyncio.run(_run())

        # Buffer accumulates all chunks regardless of subscribers
        session = get_session("TestTask5")
        assert session.buffer == ["a", "b"]
