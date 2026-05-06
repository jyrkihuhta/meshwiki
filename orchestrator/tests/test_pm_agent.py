"""Tests for the PM/Architect agent (pm_agent.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.agents.pm_agent import (
    _build_subtask,
    _OpenAIResponseAdapter,
    _OpenAIUsageAdapter,
    decompose_with_pm,
    review_with_pm,
)
from factory.cost import tokens_to_usd
from factory.state import FactoryState, SubTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> FactoryState:
    """Return a minimal FactoryState for testing."""
    defaults: dict = {
        "thread_id": "task-0042",
        "task_wiki_page": "Task_0042_test",
        "title": "Test Task",
        "requirements": "Implement a test feature.",
        "subtasks": [],
        "decomposition_approved": False,
        "active_grinders": [],
        "completed_subtask_ids": [],
        "failed_subtask_ids": [],
        "pm_messages": [],
        "human_approval_response": None,
        "human_feedback": None,
        "cost_usd": 0.0,
        "graph_status": "decomposing",
        "error": None,
    }
    defaults.update(kwargs)
    return FactoryState(**defaults)


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


def _make_response(content, stop_reason: str = "tool_use", usage=None):
    """Build a mock Anthropic Messages response."""
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    if usage is not None:
        resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# _build_subtask
# ---------------------------------------------------------------------------


def test_build_subtask_defaults() -> None:
    """_build_subtask sets all required defaults from minimal tool input."""
    tool_input = {
        "page_name": "Task_0042_Sub_01_add_search",
        "title": "Add search endpoint",
        "description": "Implement GET /search route.",
        "acceptance_criteria": ["Returns 200", "Results are sorted"],
        "parent_task": "Task_0042_test",
        "estimation": "m",
        "expected_files": ["src/meshwiki/main.py"],
    }

    subtask = _build_subtask(tool_input, parent_thread_id="task-0042")

    assert subtask["wiki_page"] == "Task_0042_Sub_01_add_search"
    assert subtask["title"] == "Add search endpoint"
    assert subtask["description"] == "Implement GET /search route."
    assert subtask["status"] == "pending"
    assert subtask["attempt"] == 0
    assert subtask["max_attempts"] == 3
    assert subtask["error_log"] == []
    assert subtask["tokens_used"] == 0
    assert subtask["assigned_grinder"] is None
    assert subtask["branch_name"] is None
    assert subtask["pr_url"] is None
    assert subtask["pr_number"] is None
    assert subtask["review_feedback"] is None
    assert subtask["token_budget"] == 50000
    assert subtask["files_touched"] == ["src/meshwiki/main.py"]
    assert subtask["acceptance_criteria"] == ["Returns 200", "Results are sorted"]
    # id is generated; just check it starts with the parent thread id
    assert subtask["id"].startswith("task-0042")


def test_build_subtask_custom_token_budget() -> None:
    """_build_subtask uses token_budget from tool_input when provided."""
    tool_input = {
        "page_name": "Task_0042_Sub_02",
        "title": "Big task",
        "description": "A large piece of work.",
        "acceptance_criteria": [],
        "parent_task": "Task_0042_test",
        "estimation": "xl",
        "expected_files": [],
        "token_budget": 80000,
    }

    subtask = _build_subtask(tool_input, parent_thread_id="task-0042")
    assert subtask["token_budget"] == 80000


# ---------------------------------------------------------------------------
# decompose_with_pm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_with_pm_returns_subtasks() -> None:
    """decompose_with_pm returns subtasks when Claude issues meshwiki_create_subtask."""
    state = _make_state()

    create_subtask_input = {
        "page_name": "Task_0042_Sub_01_search",
        "title": "Add search",
        "description": "Implement search.",
        "acceptance_criteria": ["Returns results"],
        "parent_task": "Task_0042_test",
        "estimation": "s",
        "expected_files": ["src/meshwiki/main.py"],
    }

    # First response: tool call to create_subtask
    first_response = _make_response(
        content=[_make_tool_use_block("meshwiki_create_subtask", create_subtask_input)],
        stop_reason="tool_use",
    )
    # Second response: end_turn
    second_response = _make_response(
        content=[_make_text_block("Decomposition complete.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(side_effect=[first_response, second_response])
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value=None)

    with patch(
        "factory.agents.pm_agent.anthropic.AsyncAnthropic",
        return_value=mock_anthropic_client,
    ):
        result = await decompose_with_pm(state, meshwiki_client, github_client=None)

    subtasks = result["subtasks"]
    assert len(subtasks) == 1
    assert subtasks[0]["title"] == "Add search"
    assert subtasks[0]["status"] == "pending"
    assert subtasks[0]["wiki_page"] == "Task_0042_Sub_01_search"


@pytest.mark.asyncio
async def test_decompose_stops_on_end_turn() -> None:
    """decompose_with_pm returns empty list gracefully when Claude says end_turn immediately."""
    state = _make_state()

    end_turn_response = _make_response(
        content=[_make_text_block("Nothing to decompose.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(return_value=end_turn_response)
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value=None)

    with patch(
        "factory.agents.pm_agent.anthropic.AsyncAnthropic",
        return_value=mock_anthropic_client,
    ):
        result = await decompose_with_pm(state, meshwiki_client, github_client=None)

    assert result["subtasks"] == []


@pytest.mark.asyncio
async def test_decompose_reads_wiki_page_via_tool() -> None:
    """decompose_with_pm returns page content when Claude calls meshwiki_read_page."""
    state = _make_state()

    read_page_input = {"page_name": "Architecture_Overview"}
    create_subtask_input = {
        "page_name": "Task_0042_Sub_01",
        "title": "Sub 1",
        "description": "Do something.",
        "acceptance_criteria": [],
        "parent_task": "Task_0042_test",
        "estimation": "xs",
        "expected_files": [],
    }

    first_response = _make_response(
        content=[_make_tool_use_block("meshwiki_read_page", read_page_input, "tu_001")],
        stop_reason="tool_use",
    )
    second_response = _make_response(
        content=[
            _make_tool_use_block(
                "meshwiki_create_subtask", create_subtask_input, "tu_002"
            )
        ],
        stop_reason="tool_use",
    )
    third_response = _make_response(
        content=[_make_text_block("Done.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(
        side_effect=[first_response, second_response, third_response]
    )
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    # Context page reads return None; the tool call read returns content
    meshwiki_client.get_page = AsyncMock(
        side_effect=lambda name: (
            {"content": "# Architecture Overview\nContent here."}
            if name == "Architecture_Overview"
            else None
        )
    )

    with patch(
        "factory.agents.pm_agent.anthropic.AsyncAnthropic",
        return_value=mock_anthropic_client,
    ):
        result = await decompose_with_pm(state, meshwiki_client, github_client=None)

    subtasks = result["subtasks"]
    assert len(subtasks) == 1


@pytest.mark.asyncio
async def test_decompose_with_pm_includes_redecompose_context() -> None:
    """decompose_with_pm embeds redecompose_context in the user message when provided."""
    state = _make_state()

    end_turn_response = _make_response(
        content=[_make_text_block("Nothing to do.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(return_value=end_turn_response)
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value=None)

    context = "Previous subtask A failed: command not found: black"
    with patch(
        "factory.agents.pm_agent.anthropic.AsyncAnthropic",
        return_value=mock_anthropic_client,
    ):
        await decompose_with_pm(
            state, meshwiki_client, github_client=None, redecompose_context=context
        )

    call_kwargs = mock_create.call_args[1]
    user_content = call_kwargs["messages"][0]["content"]
    assert "Redecompose Notice" in user_content
    assert context in user_content


# ---------------------------------------------------------------------------
# review_with_pm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_with_pm_approved() -> None:
    """review_with_pm returns decision=='approved' when Claude calls pm_approve_pr."""
    state = _make_state()

    subtask = _build_subtask(
        {
            "page_name": "Task_0042_Sub_01",
            "title": "Add search",
            "description": "Implement search.",
            "acceptance_criteria": ["Returns results"],
            "parent_task": "Task_0042_test",
            "estimation": "s",
            "expected_files": [],
        },
        parent_thread_id="task-0042",
    )
    subtask = SubTask(**{**subtask, "pr_number": 99, "status": "review"})

    approve_input = {"subtask_id": subtask["id"], "comment": "LGTM!"}
    approve_response = _make_response(
        content=[_make_tool_use_block("pm_approve_pr", approve_input)],
        stop_reason="tool_use",
    )
    end_response = _make_response(
        content=[_make_text_block("Approved.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(side_effect=[approve_response, end_response])
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(
        return_value={"content": "## Acceptance Criteria\n- Returns results"}
    )

    github_client = AsyncMock()
    github_client.get_pr_diff = AsyncMock(return_value="diff --git ...")

    mock_settings = MagicMock()
    mock_settings.pm_triage_model = (
        ""  # disable triage so mock responses aren't consumed
    )
    mock_settings.pm_review_model = "claude-sonnet-4-6"
    mock_settings.anthropic_api_key = "test-key"
    mock_settings.pm_review_max_diff_lines = 500
    mock_settings.minimax_api_key = None

    with (
        patch(
            "factory.agents.pm_agent.anthropic.AsyncAnthropic",
            return_value=mock_anthropic_client,
        ),
        patch("factory.agents.pm_agent.get_settings", return_value=mock_settings),
    ):
        result = await review_with_pm(state, subtask, meshwiki_client, github_client)

    assert result["decision"] == "approved"
    assert result["feedback"] == "LGTM!"


@pytest.mark.asyncio
async def test_review_with_pm_changes_requested() -> None:
    """review_with_pm returns decision=='changes_requested' and feedback populated."""
    state = _make_state()

    subtask = _build_subtask(
        {
            "page_name": "Task_0042_Sub_02",
            "title": "Add tags",
            "description": "Implement tagging.",
            "acceptance_criteria": ["Tags are stored"],
            "parent_task": "Task_0042_test",
            "estimation": "m",
            "expected_files": [],
        },
        parent_thread_id="task-0042",
    )
    subtask = SubTask(**{**subtask, "pr_number": 100, "status": "review"})

    request_changes_input = {
        "subtask_id": subtask["id"],
        "feedback": "Missing tests for tag deletion.",
    }
    changes_response = _make_response(
        content=[_make_tool_use_block("pm_request_changes", request_changes_input)],
        stop_reason="tool_use",
    )
    end_response = _make_response(
        content=[_make_text_block("Changes requested.")],
        stop_reason="end_turn",
    )

    mock_create = AsyncMock(side_effect=[changes_response, end_response])
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value=None)

    github_client = AsyncMock()
    github_client.get_pr_diff = AsyncMock(return_value="diff --git ...")

    mock_settings = MagicMock()
    mock_settings.pm_triage_model = (
        ""  # disable triage so mock responses aren't consumed
    )
    mock_settings.pm_review_model = "claude-sonnet-4-6"
    mock_settings.anthropic_api_key = "test-key"
    mock_settings.pm_review_max_diff_lines = 500
    mock_settings.minimax_api_key = None

    with (
        patch(
            "factory.agents.pm_agent.anthropic.AsyncAnthropic",
            return_value=mock_anthropic_client,
        ),
        patch("factory.agents.pm_agent.get_settings", return_value=mock_settings),
    ):
        result = await review_with_pm(state, subtask, meshwiki_client, github_client)

    assert result["decision"] == "changes_requested"
    assert result["feedback"] == "Missing tests for tag deletion."


@pytest.mark.asyncio
async def test_review_with_pm_armory_playbook_injects_schema_and_skips_triage() -> None:
    """For artifact_type='playbook' state, the user_message must contain the
    Molly playbook schema, AND the triage pass must be skipped (so a
    diff-only LLM cannot fast-approve a structurally-broken playbook)."""
    state = _make_state(artifact_type="playbook")

    subtask = _build_subtask(
        {
            "page_name": "Task_0099_Sub_01",
            "title": "Add SSRF playbook",
            "description": "Add ssrf-internal-api playbook.",
            "acceptance_criteria": ["File exists at playbooks/ssrf-internal-api.md"],
            "parent_task": "Task_0099_armory",
            "estimation": "s",
            "expected_files": ["playbooks/ssrf-internal-api.md"],
        },
        parent_thread_id="task-0099",
    )
    subtask = SubTask(**{**subtask, "pr_number": 7, "status": "review"})

    approve_response = _make_response(
        content=[_make_tool_use_block(
            "pm_approve_pr",
            {"subtask_id": subtask["id"], "comment": "schema-conformant"},
        )],
        stop_reason="tool_use",
    )
    end_response = _make_response(
        content=[_make_text_block("Approved.")], stop_reason="end_turn"
    )

    mock_create = AsyncMock(side_effect=[approve_response, end_response])
    mock_messages = MagicMock()
    mock_messages.create = mock_create

    mock_anthropic_client = MagicMock()
    mock_anthropic_client.messages = mock_messages

    meshwiki_client = AsyncMock()
    meshwiki_client.get_page = AsyncMock(return_value=None)

    github_client = AsyncMock()
    github_client.get_pr_diff = AsyncMock(return_value="diff --git a/playbooks/...")

    mock_settings = MagicMock()
    # Triage IS configured — the test asserts it gets bypassed for armory PRs.
    mock_settings.pm_triage_model = "google/gemini-flash-1.5-8b"
    mock_settings.pm_review_model = "claude-sonnet-4-6"
    mock_settings.anthropic_api_key = "test-key"
    mock_settings.pm_review_max_diff_lines = 500
    mock_settings.minimax_api_key = None

    with (
        patch(
            "factory.agents.pm_agent.anthropic.AsyncAnthropic",
            return_value=mock_anthropic_client,
        ),
        patch("factory.agents.pm_agent.get_settings", return_value=mock_settings),
    ):
        result = await review_with_pm(state, subtask, meshwiki_client, github_client)

    assert result["decision"] == "approved"

    # The first call must be the full review (not triage) and must carry the
    # armory schema in the user message. We verify both by inspecting the first
    # call's prompt text — the triage prompt starts with "Quick triage:" while
    # the full-review user_message starts with "## Subtask:" + acceptance, and
    # only the latter carries the "Armory Artifact Review" section.
    first_call_kwargs = mock_create.call_args_list[0].kwargs
    first_model = first_call_kwargs.get("model", "")
    user_msg = first_call_kwargs["messages"][0]["content"]
    assert first_model == "claude-sonnet-4-6", (
        f"first call should be full review, got model={first_model!r}"
    )
    assert "Quick triage:" not in user_msg, "triage prompt should not have run"
    assert "Armory Artifact Review" in user_msg
    assert "deterministic" in user_msg
    assert "mode: intruder" in user_msg  # appears in pitfalls section


# ---------------------------------------------------------------------------
# _OpenAIResponseAdapter — model threading + cost pricing
# ---------------------------------------------------------------------------


def _fake_oai_response(
    content: str = "ok",
    finish_reason: str = "stop",
    prompt_tokens: int = 1000,
    completion_tokens: int = 200,
):
    """Build a minimal fake OpenAI ChatCompletion response."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def test_openai_response_adapter_model_is_threaded():
    """_response_model must reflect the model passed at construction, not a class default."""
    fake = _fake_oai_response()
    openrouter = _OpenAIResponseAdapter(fake, "anthropic/claude-sonnet-4-5")
    minimax = _OpenAIResponseAdapter(fake, "MiniMax-M2.7")

    assert openrouter._response_model == "anthropic/claude-sonnet-4-5"
    assert minimax._response_model == "MiniMax-M2.7"
    # Different instances must not share the attribute
    assert openrouter._response_model != minimax._response_model


def test_openai_response_adapter_cost_differs_by_model():
    """OpenRouter-Sonnet and MiniMax costs should differ by ~10× on the same token counts."""
    fake = _fake_oai_response(prompt_tokens=10_000, completion_tokens=1_000)

    sonnet_adapter = _OpenAIResponseAdapter(fake, "anthropic/claude-sonnet-4-5")
    minimax_adapter = _OpenAIResponseAdapter(fake, "MiniMax-M2.7")

    sonnet_cost = tokens_to_usd(sonnet_adapter.usage, sonnet_adapter._response_model)
    minimax_cost = tokens_to_usd(minimax_adapter.usage, minimax_adapter._response_model)

    # Sonnet: 10000*3e-6 + 1000*15e-6 = 0.030 + 0.015 = 0.045
    assert abs(sonnet_cost - 0.045) < 1e-9
    # MiniMax: 10000*0.3e-6 + 1000*1.1e-6 = 0.003 + 0.0011 = 0.0041
    assert abs(minimax_cost - 0.0041) < 1e-9
    # The ratio should be roughly 10× or more
    assert sonnet_cost / minimax_cost > 9
