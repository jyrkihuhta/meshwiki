"""Tests for M22: Factory multi-repo targeting.

Covers:
- FactoryState has task_repo_root and artifact_type fields
- task_intake_node reads repo_root and artifact_type from frontmatter
- task_intake_node propagates new fields on all return paths
- _artifact_intro returns the right description for each artifact type
- grind_subtask_e2b adjusts bootstrap and prompt for non-MeshWiki tasks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.agents.grinder_agent import _artifact_intro
from factory.nodes.task_intake import task_intake_node
from factory.state import FactoryState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> FactoryState:
    defaults: dict = {
        "thread_id": "task-0099",
        "task_wiki_page": "Task_0099_test",
        "title": "",
        "requirements": "",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": [],
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "incremental_costs_usd": [],
        "graph_status": "intake",
        "error": None,
        "escalation_decision": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


def _mock_cm(mock_client: AsyncMock) -> AsyncMock:
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# FactoryState schema
# ---------------------------------------------------------------------------


def test_factory_state_has_task_repo_root() -> None:
    """FactoryState must include task_repo_root field."""
    from factory.state import FactoryState
    assert "task_repo_root" in FactoryState.__annotations__


def test_factory_state_has_artifact_type() -> None:
    """FactoryState must include artifact_type field."""
    from factory.state import FactoryState
    assert "artifact_type" in FactoryState.__annotations__


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_has_molly_url() -> None:
    from factory.config import Settings
    s = Settings()
    assert hasattr(s, "molly_url")
    assert s.molly_url == ""


def test_settings_has_molly_api_token() -> None:
    from factory.config import Settings
    s = Settings()
    assert hasattr(s, "molly_api_token")
    assert s.molly_api_token == ""


def test_settings_has_armory_repo() -> None:
    from factory.config import Settings
    s = Settings()
    assert hasattr(s, "armory_repo")
    assert s.armory_repo == ""


# ---------------------------------------------------------------------------
# _artifact_intro
# ---------------------------------------------------------------------------


def test_artifact_intro_none_returns_meshwiki() -> None:
    intro = _artifact_intro(None, None)
    assert "MeshWiki" in intro


def test_artifact_intro_code_returns_meshwiki() -> None:
    intro = _artifact_intro("code", None)
    assert "MeshWiki" in intro


def test_artifact_intro_tool_describes_toolbase() -> None:
    intro = _artifact_intro("tool", None)
    assert "ToolBase" in intro
    assert "molly-armory" in intro.lower() or "armory" in intro


def test_artifact_intro_tool_includes_repo_root() -> None:
    intro = _artifact_intro("tool", "python/molly/tools")
    assert "python/molly/tools" in intro


def test_artifact_intro_playbook_describes_yaml() -> None:
    intro = _artifact_intro("playbook", None)
    assert "YAML" in intro or "playbook" in intro.lower()


def test_artifact_intro_wordlist_describes_format() -> None:
    intro = _artifact_intro("wordlist", None)
    assert "wordlist" in intro.lower() or "plain text" in intro.lower()


def test_artifact_intro_unknown_type_returns_something() -> None:
    """Unknown artifact_type must not raise."""
    intro = _artifact_intro("unknown_future_type", None)
    assert isinstance(intro, str)


# ---------------------------------------------------------------------------
# task_intake_node — new fields propagated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_intake_reads_repo_root_from_frontmatter() -> None:
    """task_intake_node must set task_repo_root from page metadata."""
    state = _make_state()
    mock_page = {
        "name": "Task_0099_test",
        "content": "## Task",
        "metadata": {
            "title": "Test",
            "assignee": "factory",
            "repo": "jyrkihuhta/molly-armory",
            "repo_root": "python/molly/tools",
        },
    }
    mock_client = _mock_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result.get("task_repo_root") == "python/molly/tools"


@pytest.mark.asyncio
async def test_task_intake_reads_artifact_type_from_frontmatter() -> None:
    """task_intake_node must set artifact_type from page metadata."""
    state = _make_state()
    mock_page = {
        "name": "Task_0099_test",
        "content": "## Task",
        "metadata": {
            "title": "Test",
            "assignee": "factory",
            "repo": "jyrkihuhta/molly-armory",
            "artifact_type": "tool",
        },
    }
    mock_client = _mock_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result.get("artifact_type") == "tool"


@pytest.mark.asyncio
async def test_task_intake_repo_root_none_when_absent() -> None:
    """task_repo_root is None when not in frontmatter (backward-compatible)."""
    state = _make_state()
    mock_page = {
        "name": "Task_0099_test",
        "content": "## Task",
        "metadata": {"title": "Test", "assignee": "factory"},
    }
    mock_client = _mock_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result.get("task_repo_root") is None
    assert result.get("artifact_type") is None


@pytest.mark.asyncio
async def test_task_intake_propagates_fields_on_skip_decomposition() -> None:
    """New fields are included in the skip_decomposition return path."""
    state = _make_state()
    mock_page = {
        "name": "Task_0099_test",
        "content": "## Task",
        "metadata": {
            "title": "Add smart_repeater v2",
            "assignee": "factory",
            "skip_decomposition": True,
            "repo": "jyrkihuhta/molly-armory",
            "repo_root": "python/molly/tools",
            "artifact_type": "tool",
        },
    }
    mock_client = _mock_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result.get("graph_status") == "grinding"
    assert result.get("task_repo_root") == "python/molly/tools"
    assert result.get("artifact_type") == "tool"
    assert result.get("task_repo") == "jyrkihuhta/molly-armory"


@pytest.mark.asyncio
async def test_task_intake_propagates_fields_on_decompose_path() -> None:
    """New fields are included in the standard decompose return path."""
    state = _make_state()
    mock_page = {
        "name": "Task_0099_test",
        "content": "## Task",
        "metadata": {
            "title": "Add playbooks",
            "assignee": "factory",
            "repo": "jyrkihuhta/molly-armory",
            "artifact_type": "playbook",
        },
    }
    mock_client = _mock_cm(AsyncMock())
    mock_client.get_page = AsyncMock(return_value=mock_page)

    with patch("factory.nodes.task_intake.MeshWikiClient", return_value=mock_client):
        result = await task_intake_node(state)

    assert result.get("graph_status") == "decomposing"
    assert result.get("artifact_type") == "playbook"
    assert result.get("task_repo_root") is None


# ---------------------------------------------------------------------------
# grind_subtask_e2b — artifact-aware bootstrap and prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2b_grinder_skips_orchestrator_install_for_tool_task() -> None:
    """For artifact_type='tool', the orchestrator pip install must not run."""
    from factory.agents.grinder_agent import grind_subtask_e2b
    from factory.state import SubTask

    state = _make_state(
        task_repo="jyrkihuhta/molly-armory",
        task_repo_root="python/molly/tools",
        artifact_type="tool",
    )
    subtask: SubTask = {
        "id": "task-0099",
        "wiki_page": "Task_0099_test",
        "parent_task": "Task_0099_test",
        "title": "Add tool",
        "description": "Implement the tool.",
        "status": "pending",
        "assigned_grinder": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": [],
        "acceptance_criteria": [],
        "token_budget": 50000,
        "tokens_used": 0,
        "review_feedback": None,
        "code_skeleton": None,
    }

    install_calls: list[str] = []

    mock_sbx = AsyncMock()
    mock_sbx.kill = AsyncMock()

    async def _fake_run(cmd: str, **kwargs):
        install_calls.append(cmd)
        result = AsyncMock()
        result.exit_code = 0
        result.stderr = ""
        return result

    mock_sbx.commands.run = _fake_run
    mock_sbx.files.write = AsyncMock()

    # PTY mock
    mock_pty_handle = AsyncMock()
    mock_pty_handle.pid = 42
    mock_pty_handle.wait = AsyncMock(return_value=None)
    mock_sbx.pty.create = AsyncMock(return_value=mock_pty_handle)
    mock_sbx.pty.send_stdin = AsyncMock()

    mock_meshwiki = AsyncMock()
    mock_meshwiki.transition_task = AsyncMock()
    mock_meshwiki.get_page = AsyncMock(return_value={"content": "## Task"})
    mock_meshwiki.relay_terminal = AsyncMock()
    mock_meshwiki.append_to_page = AsyncMock()

    with (
        patch("e2b_code_interpreter.AsyncSandbox") as mock_cls,
        patch("factory.agents.grinder_agent.get_settings") as mock_settings,
    ):
        mock_cls.create = AsyncMock(return_value=mock_sbx)
        s = AsyncMock()
        s.dry_run = False
        s.e2b_api_key = "test-key"
        s.github_token = "gh-token"
        s.minimax_api_key = "mm-key"
        s.grinder_model = "MiniMax-M2.7"
        s.pr_base_branch = "main"
        s.terminal_log_max_chars = 1000
        mock_settings.return_value = s

        await grind_subtask_e2b(state, subtask, mock_meshwiki)

    orchestrator_installs = [
        c for c in install_calls if "orchestrator" in c and "pip install" in c
    ]
    assert orchestrator_installs == [], (
        f"orchestrator install must be skipped for artifact_type='tool', "
        f"but found: {orchestrator_installs}"
    )


@pytest.mark.asyncio
async def test_e2b_grinder_runs_orchestrator_install_for_meshwiki_task() -> None:
    """For a standard MeshWiki task (no artifact_type), orchestrator install must run."""
    from factory.agents.grinder_agent import grind_subtask_e2b
    from factory.state import SubTask

    state = _make_state(task_repo="jyrkihuhta/meshwiki")
    subtask: SubTask = {
        "id": "task-0042",
        "wiki_page": "Task_0042_test",
        "parent_task": "Task_0042_test",
        "title": "Fix thing",
        "description": "Fix it.",
        "status": "pending",
        "assigned_grinder": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": [],
        "acceptance_criteria": [],
        "token_budget": 50000,
        "tokens_used": 0,
        "review_feedback": None,
        "code_skeleton": None,
    }

    install_calls: list[str] = []

    mock_sbx = AsyncMock()
    mock_sbx.kill = AsyncMock()

    async def _fake_run(cmd: str, **kwargs):
        install_calls.append(cmd)
        result = AsyncMock()
        result.exit_code = 0
        result.stderr = ""
        return result

    mock_sbx.commands.run = _fake_run
    mock_sbx.files.write = AsyncMock()

    mock_pty_handle = AsyncMock()
    mock_pty_handle.pid = 42
    mock_pty_handle.wait = AsyncMock(return_value=None)
    mock_sbx.pty.create = AsyncMock(return_value=mock_pty_handle)
    mock_sbx.pty.send_stdin = AsyncMock()

    mock_meshwiki = AsyncMock()
    mock_meshwiki.transition_task = AsyncMock()
    mock_meshwiki.get_page = AsyncMock(return_value={"content": "## Task"})
    mock_meshwiki.relay_terminal = AsyncMock()
    mock_meshwiki.append_to_page = AsyncMock()

    with (
        patch("e2b_code_interpreter.AsyncSandbox") as mock_cls,
        patch("factory.agents.grinder_agent.get_settings") as mock_settings,
    ):
        mock_cls.create = AsyncMock(return_value=mock_sbx)
        s = AsyncMock()
        s.dry_run = False
        s.e2b_api_key = "test-key"
        s.github_token = "gh-token"
        s.minimax_api_key = "mm-key"
        s.grinder_model = "MiniMax-M2.7"
        s.pr_base_branch = "main"
        s.terminal_log_max_chars = 1000
        mock_settings.return_value = s

        await grind_subtask_e2b(state, subtask, mock_meshwiki)

    orchestrator_installs = [
        c for c in install_calls if "orchestrator" in c and "pip install" in c
    ]
    assert orchestrator_installs, "orchestrator install must run for MeshWiki tasks"
