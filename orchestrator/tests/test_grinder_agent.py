"""Tests for the Grinder agent (grinder_agent.py)."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.agents.grinder_agent import (
    GRINDER_SYSTEM_PROMPT,
    GRINDER_TOOLS,
    GrinderToolExecutor,
    grind_subtask,
    grind_subtask_e2b,
)
from factory.state import FactoryState, SubTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(tmp_path: Path, meshwiki_client=None) -> GrinderToolExecutor:
    """Create a GrinderToolExecutor backed by a temporary directory."""
    if meshwiki_client is None:
        meshwiki_client = AsyncMock()
    return GrinderToolExecutor(repo_root=tmp_path, meshwiki_client=meshwiki_client)


def _make_state(**kwargs) -> FactoryState:
    """Return a minimal FactoryState for testing."""
    defaults: dict = {
        "thread_id": "task-0042",
        "task_wiki_page": "Task_0042_test",
        "title": "Test Task",
        "requirements": "Implement a test feature.",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": {},
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "graph_status": "grinding",
        "error": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


def _make_subtask(**kwargs) -> SubTask:
    """Return a minimal SubTask for testing."""
    defaults: dict = {
        "id": "task-0042-sub-abc123",
        "wiki_page": "Task_0042_Sub_01_add_feature",
        "title": "Add feature",
        "description": "Implement the feature.",
        "status": "pending",
        "assigned_grinder": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": ["src/meshwiki/main.py"],
        "token_budget": 10000,
        "tokens_used": 0,
        "review_feedback": None,
    }
    defaults.update(kwargs)
    return SubTask(**defaults)


def _make_tool_use_block(tool_name: str, tool_input: dict, block_id: str = "tu_001"):
    """Build a mock tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = block_id
    return block


def _make_text_block(text: str = "Done."):
    """Build a mock text content block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_response(content, stop_reason: str = "tool_use"):
    """Build a mock Anthropic Messages response."""
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    resp.usage = MagicMock()
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


# ---------------------------------------------------------------------------
# GrinderToolExecutor: file system tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grinder_tool_read_file(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('read_file') returns file content."""
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world")

    executor = _make_executor(tmp_path)
    result = await executor.execute("read_file", {"path": "hello.txt"})

    assert result == "hello world"


@pytest.mark.asyncio
async def test_grinder_tool_read_file_missing(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('read_file') returns 'File not found' for missing files."""
    executor = _make_executor(tmp_path)
    result = await executor.execute("read_file", {"path": "nonexistent.txt"})

    assert result == "File not found"


@pytest.mark.asyncio
async def test_grinder_tool_write_file(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('write_file') writes content and returns confirmation."""
    executor = _make_executor(tmp_path)
    result = await executor.execute(
        "write_file", {"path": "subdir/new_file.py", "content": "# new file\n"}
    )

    assert "Written:" in result
    written = tmp_path / "subdir" / "new_file.py"
    assert written.exists()
    assert written.read_text() == "# new file\n"


@pytest.mark.asyncio
async def test_grinder_tool_write_file_creates_parents(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('write_file') creates parent directories."""
    executor = _make_executor(tmp_path)
    await executor.execute(
        "write_file", {"path": "a/b/c/deep_file.txt", "content": "deep"}
    )

    assert (tmp_path / "a" / "b" / "c" / "deep_file.txt").exists()


@pytest.mark.asyncio
async def test_grinder_tool_list_directory(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('list_directory') lists directory contents."""
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")
    (tmp_path / "subdir").mkdir()

    executor = _make_executor(tmp_path)
    result = await executor.execute("list_directory", {"path": "."})

    assert "alpha.py" in result
    assert "beta.py" in result
    assert "subdir" in result


@pytest.mark.asyncio
async def test_grinder_tool_list_directory_missing(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('list_directory') returns error for missing dir."""
    executor = _make_executor(tmp_path)
    result = await executor.execute("list_directory", {"path": "nonexistent"})

    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_grinder_tool_search_code(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('search_code') runs rg and returns matches."""
    rg = shutil.which("rg")
    if rg is None:
        pytest.skip("ripgrep (rg) not available on this machine")

    (tmp_path / "example.py").write_text("def hello_world():\n    pass\n")

    executor = _make_executor(tmp_path)
    result = await executor.execute(
        "search_code",
        {"pattern": "hello_world", "path": "."},
    )

    assert "hello_world" in result


@pytest.mark.asyncio
async def test_grinder_tool_search_code_no_matches(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('search_code') returns 'No matches' when nothing found."""
    rg = shutil.which("rg")
    if rg is None:
        pytest.skip("ripgrep (rg) not available on this machine")

    executor = _make_executor(tmp_path)
    result = await executor.execute(
        "search_code",
        {"pattern": "zzz_unlikely_pattern_zzz", "path": "."},
    )

    assert result == "No matches"


# ---------------------------------------------------------------------------
# GrinderToolExecutor: git tools (mocked subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grinder_tool_git_create_branch(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('git_create_branch') calls git checkout -b."""
    executor = _make_executor(tmp_path)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("factory.agents.grinder_agent.subprocess.run", return_value=mock_result):
        result = await executor.execute(
            "git_create_branch", {"branch_name": "factory/task-0042-sub-01"}
        )

    assert "factory/task-0042-sub-01" in result


@pytest.mark.asyncio
async def test_grinder_tool_git_create_branch_failure(tmp_path: Path) -> None:
    """GrinderToolExecutor.execute('git_create_branch') returns error on failure."""
    executor = _make_executor(tmp_path)

    mock_result = MagicMock()
    mock_result.returncode = 128
    mock_result.stdout = ""
    mock_result.stderr = "branch already exists"

    with patch("factory.agents.grinder_agent.subprocess.run", return_value=mock_result):
        result = await executor.execute(
            "git_create_branch", {"branch_name": "existing-branch"}
        )

    assert "Error:" in result
    assert "branch already exists" in result


# ---------------------------------------------------------------------------
# grind_subtask: integration tests with mocked Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grind_subtask_transitions_to_in_progress() -> None:
    """grind_subtask calls meshwiki_client.transition_task with 'in_progress'."""
    state = _make_state()
    subtask = _make_subtask(token_budget=5000)

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    # end_turn immediately — no PR created
    end_response = _make_response(
        content=[_make_text_block("Nothing to do.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(return_value=end_response)
    mock_messages = MagicMock()
    mock_messages.create = mock_create
    mock_client = MagicMock()
    mock_client.messages = mock_messages

    with patch(
        "factory.agents.grinder_agent.anthropic.AsyncAnthropic",
        return_value=mock_client,
    ):
        with patch(
            "factory.agents.grinder_agent.get_settings",
            return_value=MagicMock(
                grinder_provider="anthropic",
                grinder_model="claude-haiku-4-5-20251001",
                repo_root="/tmp",
                anthropic_api_key="",
            ),
        ):
            await grind_subtask(state, subtask, meshwiki_client)

    # Must have been called with 'in_progress'
    meshwiki_client.transition_task.assert_called_once_with(
        subtask["wiki_page"], "in_progress"
    )


@pytest.mark.asyncio
async def test_grind_subtask_creates_pr() -> None:
    """grind_subtask sets status='review' and pr_url when create_pr tool succeeds."""
    state = _make_state()
    subtask = _make_subtask(token_budget=10000)

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    pr_url = "https://github.com/owner/repo/pull/99"

    # First response: create_pr tool call
    create_pr_response = _make_response(
        content=[
            _make_tool_use_block(
                "create_pr",
                {
                    "title": "feat: add feature",
                    "body": "Implements the feature.",
                    "branch_name": "factory/task-0042-sub-abc123",
                },
                "tu_001",
            )
        ],
        stop_reason="tool_use",
    )
    # Second response: end_turn
    end_response = _make_response(
        content=[_make_text_block("PR created.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(side_effect=[create_pr_response, end_response])
    mock_messages = MagicMock()
    mock_messages.create = mock_create
    mock_client = MagicMock()
    mock_client.messages = mock_messages

    # _create_pr subprocess result returning the PR URL
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = pr_url
    mock_proc.stderr = ""

    with patch(
        "factory.agents.grinder_agent.anthropic.AsyncAnthropic",
        return_value=mock_client,
    ):
        with patch(
            "factory.agents.grinder_agent.get_settings",
            return_value=MagicMock(
                grinder_provider="anthropic",
                grinder_model="claude-haiku-4-5-20251001",
                repo_root="/tmp",
                anthropic_api_key="",
            ),
        ):
            with patch(
                "factory.agents.grinder_agent.subprocess.run",
                return_value=mock_proc,
            ):
                result = await grind_subtask(state, subtask, meshwiki_client)

    assert result["status"] == "review"
    assert result["pr_url"] == pr_url


@pytest.mark.asyncio
async def test_grind_subtask_fails_on_budget() -> None:
    """grind_subtask sets status='failed' when token budget is exhausted without a PR."""
    state = _make_state()
    # token_budget=2000 => max 2 tool calls (min(2000//1000, 50) = 2)
    subtask = _make_subtask(token_budget=2000)

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    # Always return a tool_use block (not create_pr, no end_turn)
    def _make_indefinite_response(_):
        return _make_response(
            content=[
                _make_tool_use_block("read_file", {"path": "README.md"}, f"tu_{_}")
            ],
            stop_reason="tool_use",
        )

    # Use a counter to return tool_use repeatedly
    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.content = [
            _make_tool_use_block("read_file", {"path": "README.md"}, f"tu_{call_count}")
        ]
        resp.stop_reason = "tool_use"
        resp.usage = MagicMock()
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 50
        return resp

    mock_messages = MagicMock()
    mock_messages.create = AsyncMock(side_effect=_side_effect)
    mock_client = MagicMock()
    mock_client.messages = mock_messages

    # _read_file returns "File not found" for anything
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    with patch(
        "factory.agents.grinder_agent.anthropic.AsyncAnthropic",
        return_value=mock_client,
    ):
        with patch(
            "factory.agents.grinder_agent.get_settings",
            return_value=MagicMock(
                grinder_provider="anthropic",
                grinder_model="claude-haiku-4-5-20251001",
                repo_root="/tmp",
                anthropic_api_key="",
            ),
        ):
            result = await grind_subtask(state, subtask, meshwiki_client)

    assert result["status"] == "failed"
    assert result["pr_url"] is None


# ---------------------------------------------------------------------------
# Module-level sanity checks
# ---------------------------------------------------------------------------


def test_grinder_system_prompt_not_empty() -> None:
    """GRINDER_SYSTEM_PROMPT is a non-empty string."""
    assert isinstance(GRINDER_SYSTEM_PROMPT, str)
    assert len(GRINDER_SYSTEM_PROMPT) > 100


def test_grinder_tools_list() -> None:
    """GRINDER_TOOLS contains the expected tool names."""
    names = {t["name"] for t in GRINDER_TOOLS}
    expected = {
        "read_file",
        "write_file",
        "list_directory",
        "search_code",
        "git_create_branch",
        "git_commit",
        "git_push",
        "run_tests",
        "run_lint",
        "run_autofix",
        "create_pr",
        "meshwiki_update_task",
    }
    assert names == expected


# ---------------------------------------------------------------------------
# grind_subtask: E2B routing and integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grind_subtask_routes_to_e2b() -> None:
    """grind_subtask calls grind_subtask_e2b when grinder_provider == 'e2b'."""
    state = _make_state()
    subtask = _make_subtask()
    meshwiki_client = AsyncMock()

    mock_settings = MagicMock(
        grinder_provider="e2b",
        grinder_model="MiniMax-M2.7",
        e2b_api_key="e2b-test",
        github_token="ghp_test",
        github_repo="owner/repo",
        minimax_api_key="mm-test",
    )

    expected_result = _make_subtask(
        status="review", pr_url="https://github.com/owner/repo/pull/1"
    )

    with patch(
        "factory.agents.grinder_agent.get_settings",
        return_value=mock_settings,
    ):
        with patch(
            "factory.agents.grinder_agent.grind_subtask_e2b",
            new=AsyncMock(return_value=expected_result),
        ) as mock_e2b:
            result = await grind_subtask(state, subtask, meshwiki_client)

    mock_e2b.assert_called_once_with(state, subtask, meshwiki_client)
    assert result["status"] == "review"


def _make_sandbox_mock(commands_side_effect: list, pty_output: str = "") -> AsyncMock:
    """Build a mock AsyncSandbox returned directly from AsyncSandbox.create()."""
    mock_commands = AsyncMock()
    mock_commands.run = AsyncMock(side_effect=commands_side_effect)
    mock_sandbox = AsyncMock()
    mock_sandbox.commands = mock_commands
    mock_sandbox.files = AsyncMock()
    mock_sandbox.kill = AsyncMock(return_value=None)

    # PTY mock: capture on_data callback and fire it with pty_output bytes.
    mock_pty_handle = AsyncMock()
    mock_pty_handle.pid = 1234
    mock_pty_handle.wait = AsyncMock(return_value=None)

    async def _create_pty(**kwargs):
        on_data = kwargs.get("on_data")
        if on_data and pty_output:
            await on_data(pty_output.encode())
        return mock_pty_handle

    mock_pty = AsyncMock()
    mock_pty.create = _create_pty
    mock_pty.send_stdin = AsyncMock(return_value=None)
    mock_sandbox.pty = mock_pty

    return mock_sandbox


def _e2b_command_sequence() -> list:
    """Return the expected commands.run side_effect list for grind_subtask_e2b.

    Sequence:
      1. git config (combined: email + name + credential helper)
      2. Node.js bootstrap (nodesource + apt + npm install -g kilo)
      3. git clone
      4. pip install
    Kilo now runs via PTY (not commands.run), so there is no 5th entry.
    """
    ok = MagicMock(exit_code=0, stdout="", stderr="")
    return [ok, ok, MagicMock(exit_code=0, stdout="", stderr=""), ok]


@pytest.mark.asyncio
async def test_grind_subtask_e2b_extracts_pr_url() -> None:
    """grind_subtask_e2b sets status='review' and pr_url when PTY output contains a PR URL."""
    state = _make_state()
    subtask = _make_subtask()
    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    pr_url = "https://github.com/owner/repo/pull/42"
    mock_sandbox = _make_sandbox_mock(
        _e2b_command_sequence(), pty_output=f"Running kilo...\nDone!\n{pr_url}"
    )

    mock_settings = MagicMock(
        e2b_api_key="e2b-test",
        github_token="ghp_test",
        github_repo="owner/repo",
        minimax_api_key="mm-test",
        grinder_model="MiniMax-M2.7",
    )

    with patch("factory.agents.grinder_agent.get_settings", return_value=mock_settings):
        with patch("e2b_code_interpreter.AsyncSandbox") as mock_cls:
            mock_cls.create = AsyncMock(return_value=mock_sandbox)
            result = await grind_subtask_e2b(state, subtask, meshwiki_client)

    assert result["status"] == "review"
    assert result["pr_url"] == pr_url


@pytest.mark.asyncio
async def test_grind_subtask_e2b_no_pr_url_fails() -> None:
    """grind_subtask_e2b sets status='failed' when PTY output has no PR URL."""
    state = _make_state()
    subtask = _make_subtask()
    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    mock_sandbox = _make_sandbox_mock(
        _e2b_command_sequence(),
        pty_output="Running kilo...\nSomething went wrong.\nNo PR was created.",
    )

    mock_settings = MagicMock(
        e2b_api_key="e2b-test",
        github_token="ghp_test",
        github_repo="owner/repo",
        minimax_api_key="mm-test",
        grinder_model="MiniMax-M2.7",
    )

    with patch("factory.agents.grinder_agent.get_settings", return_value=mock_settings):
        with patch("e2b_code_interpreter.AsyncSandbox") as mock_cls:
            mock_cls.create = AsyncMock(return_value=mock_sandbox)
            result = await grind_subtask_e2b(state, subtask, meshwiki_client)

    assert result["status"] == "failed"
    assert result["pr_url"] is None


@pytest.mark.asyncio
async def test_grind_subtask_e2b_sandbox_error() -> None:
    """grind_subtask_e2b handles Sandbox.create raising an exception gracefully."""
    state = _make_state()
    subtask = _make_subtask()
    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value={"content": "# Task spec"})
    meshwiki_client.transition_task = AsyncMock(return_value={})

    mock_settings = MagicMock(
        e2b_api_key="e2b-invalid",
        github_token="ghp_test",
        github_repo="owner/repo",
        minimax_api_key="mm-test",
        grinder_model="MiniMax-M2.7",
    )

    with patch("factory.agents.grinder_agent.get_settings", return_value=mock_settings):
        with patch("e2b_code_interpreter.AsyncSandbox") as mock_cls:
            mock_cls.create = AsyncMock(side_effect=RuntimeError("sandbox auth failed"))
            result = await grind_subtask_e2b(state, subtask, meshwiki_client)

    assert result["status"] == "failed"
    assert result["pr_url"] is None
