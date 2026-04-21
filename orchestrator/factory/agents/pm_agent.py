"""PM/Architect agent using Claude Opus 4 via the Anthropic API."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

import anthropic

from ..config import get_settings
from ..cost import tokens_to_usd
from ..integrations.github_client import _extract_pr_number
from ..state import FactoryState, SubTask

if TYPE_CHECKING:
    from ..integrations.github_client import GitHubClient
    from ..integrations.meshwiki_client import MeshWikiClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anthropic circuit breaker
# ---------------------------------------------------------------------------
# When Anthropic returns a billing/hard-limit error we block further calls
# for a cooldown window.  The scheduler and PM callers check this before
# dispatching so we don't burn E2B slots on work that can't be reviewed.

_anthropic_blocked_until: float = 0.0


def _is_anthropic_blocked() -> bool:
    return time.monotonic() < _anthropic_blocked_until


def anthropic_blocked_seconds_remaining() -> float:
    """Return seconds remaining on the Anthropic circuit breaker (0 = not blocked)."""
    return max(0.0, _anthropic_blocked_until - time.monotonic())


def _block_anthropic(seconds: float = 900.0) -> None:
    global _anthropic_blocked_until
    _anthropic_blocked_until = time.monotonic() + seconds
    logger.warning(
        "pm_agent: Anthropic circuit breaker engaged — blocked for %.0fs", seconds
    )


def _is_billing_error(exc: anthropic.APIStatusError) -> bool:
    """Return True if the error is a hard billing/spend limit (not a transient overload)."""
    if exc.status_code == 402:
        return True
    if exc.status_code == 429:
        msg = str(exc).lower()
        return any(w in msg for w in ("credit", "spend", "billing", "quota", "limit"))
    return False


PM_SYSTEM_PROMPT = """
You are the PM/Architect for MeshWiki, an autonomous software development factory.

Your responsibilities:
1. Decompose high-level tasks into concrete, independently implementable subtasks
2. Review grinder-produced code for correctness, style, and adherence to requirements
3. Handle escalations when grinders fail

MeshWiki tech stack: FastAPI, Jinja2, HTMX, Python 3.12+, Rust (graph engine via PyO3).
All code must follow PEP 8, have type hints, use async/await for storage, and include tests.

When decomposing:
- Subtasks must be FLAT — never create subtasks of subtasks. Every subtask's
  `wiki_page` must use the format "{parent_page_name}_TASK{N:03d}_{Short_title}"
  (underscores, no slashes), e.g. "Epic_0001_graph_view_TASK001_Add_search".
  Never nest further: set `parent_task` to the parent epic page, not another subtask.
- Prefer SMALL, ATOMIC subtasks. One subtask = one focused file change. Smaller
  scope means fewer things the grinder can get wrong.
- The task requirements specify how many subtasks to create — follow that exactly.
  Do not split further unless the requirements explicitly ask for it.
- Subtasks must be as independent as possible (minimize file overlap)
- Include file paths you expect will be touched in each subtask
- Write clear acceptance criteria
- **Always use `github_read_file` to read relevant source files before writing subtask
  descriptions.** When a subtask requires mirroring an existing pattern (e.g. a new
  Preprocessor, Extension, API route, or storage method), read the file, find the closest
  existing example, and paste it verbatim into the `code_skeleton` field of
  `meshwiki_create_subtask`. The grinder will adapt this skeleton — don't just describe
  the pattern in prose, show it. This is the most important thing you can do to ensure
  grinder success.
- If the subtask adds a new wiki macro (<<MacroName>>) that needs async data
  (storage, database), include this constraint in the description:
  "Preprocessors run inside FastAPI's event loop — never use asyncio.run().
  Pre-fetch data in the async route handler and pass it as a constructor
  parameter to the Extension class. Follow the RecentChanges/PageList pattern
  in parser.py."

When reviewing:
- Check that tests cover the new code
- Verify the implementation matches the acceptance criteria
- Confirm no regressions to existing functionality
- Flag any security issues immediately
""".strip()

PM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "github_read_file",
        "description": (
            "Read a raw source file from the GitHub repository (staging branch). "
            "Use this during decomposition to read existing source files and extract "
            "code patterns to include as skeletons in subtask descriptions. "
            "Always read the relevant files before writing subtasks that touch them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root, e.g. 'src/meshwiki/core/parser.py'.",
                },
                "ref": {
                    "type": "string",
                    "description": "Branch or commit ref. Defaults to 'staging'.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "meshwiki_read_page",
        "description": "Read a MeshWiki wiki page to gather context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "The wiki page name to read.",
                },
            },
            "required": ["page_name"],
        },
    },
    {
        "name": "meshwiki_create_subtask",
        "description": "Create a subtask for a grinder agent to implement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "The MeshWiki page name for this subtask. Use the format '{parent_page_name}_TASK{N:03d}_{Short_descriptive_title}' (underscores, no slashes) where parent_page_name is the wiki page being decomposed and N starts at 001 for each epic (e.g. if decomposing 'Epic_0001_graph_view', create 'Epic_0001_graph_view_TASK001_Add_search_feature'). Scan existing pages with the parent prefix to find the highest N and increment by 1.",
                },
                "title": {
                    "type": "string",
                    "description": "Short, descriptive title of the subtask.",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be implemented.",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of acceptance criteria that must be met.",
                },
                "parent_task": {
                    "type": "string",
                    "description": "Parent task wiki page name.",
                },
                "estimation": {
                    "type": "string",
                    "enum": ["xs", "s", "m", "l", "xl"],
                    "description": "Effort estimation (xs=<1h, s=1-2h, m=2-4h, l=4-8h, xl=8h+).",
                },
                "expected_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths expected to be created or modified.",
                },
                "code_skeleton": {
                    "type": "string",
                    "description": (
                        "Optional starter code skeleton the grinder should adapt. "
                        "Paste the most relevant existing implementation from the codebase "
                        "(e.g. a similar Preprocessor, Extension, or route handler) verbatim, "
                        "then annotate with comments like '# TODO: change X to Y'. "
                        "This is shown in a code block on the subtask wiki page."
                    ),
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Token budget for the grinder session (default 50000).",
                },
            },
            "required": [
                "page_name",
                "title",
                "description",
                "acceptance_criteria",
                "parent_task",
                "estimation",
                "expected_files",
            ],
        },
    },
    {
        "name": "github_get_pr_diff",
        "description": "Fetch the diff for a GitHub pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {
                    "type": "integer",
                    "description": "The pull request number.",
                },
            },
            "required": ["pr_number"],
        },
    },
    {
        "name": "pm_approve_pr",
        "description": "Approve a subtask PR — the implementation meets acceptance criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subtask_id": {
                    "type": "string",
                    "description": "The subtask ID being approved.",
                },
                "comment": {
                    "type": "string",
                    "description": "Approval comment / summary of review.",
                },
            },
            "required": ["subtask_id", "comment"],
        },
    },
    {
        "name": "pm_request_changes",
        "description": "Request changes on a subtask PR — the implementation does not meet acceptance criteria. You MUST provide non-empty feedback: the grinder has no other way to know what to fix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subtask_id": {
                    "type": "string",
                    "description": "The subtask ID being reviewed.",
                },
                "feedback": {
                    "type": "string",
                    "description": "Detailed, actionable feedback describing exactly what needs to change. Must be non-empty — the grinder will fail immediately if this is blank.",
                },
            },
            "required": ["subtask_id", "feedback"],
        },
    },
]


class _ToolUseBlock:
    """Duck-typed Anthropic ToolUseBlock backed by an OpenAI tool_call."""

    type = "tool_use"

    def __init__(self, tool_call: Any) -> None:
        self.id: str = tool_call.id
        self.name: str = tool_call.function.name
        self.input: dict[str, Any] = json.loads(tool_call.function.arguments or "{}")


class _TextBlock:
    """Duck-typed Anthropic TextBlock backed by an OpenAI content string."""

    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _OpenAIUsageAdapter:
    """Exposes OpenAI usage in Anthropic's ``input_tokens``/``output_tokens`` shape."""

    def __init__(self, oai_usage: Any) -> None:
        self.input_tokens: int = getattr(oai_usage, "prompt_tokens", 0) or 0
        self.output_tokens: int = getattr(oai_usage, "completion_tokens", 0) or 0


class _OpenAIResponseAdapter:
    """Wraps an OpenAI ChatCompletion to match the Anthropic Messages interface.

    The PM agent loops inspect ``response.stop_reason``, ``response.content``,
    and ``response.usage``. This adapter translates the OpenAI shape so the
    existing loop code works without modification.

    ``_response_model`` is set to the actual model used so callers can price
    the response at the correct rate rather than the Anthropic model's rate.
    """

    _response_model: str = "MiniMax-M2.7"

    def __init__(self, oai_resp: Any) -> None:
        choice = oai_resp.choices[0]
        msg = choice.message

        finish = choice.finish_reason  # "stop" | "tool_calls" | "length"
        self.stop_reason = "end_turn" if finish == "stop" else "tool_use"

        blocks: list[Any] = []
        if msg.content:
            blocks.append(_TextBlock(msg.content))
        for tc in msg.tool_calls or []:
            blocks.append(_ToolUseBlock(tc))
        self.content = blocks

        self.usage = _OpenAIUsageAdapter(oai_resp.usage) if oai_resp.usage else None


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool schema format to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        }
        for t in tools
    ]


def _convert_to_oai_messages(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Anthropic-style messages/system to OpenAI chat format."""
    oai_messages: list[dict[str, Any]] = []
    for msg in kwargs.get("messages", []):
        if msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                text_parts = [b.text for b in content if hasattr(b, "text")]
                tool_calls = [
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {"name": b.name, "arguments": json.dumps(b.input)},
                    }
                    for b in content
                    if hasattr(b, "type") and b.type == "tool_use"
                ]
                oai_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": " ".join(text_parts) or None,
                }
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                oai_messages.append(oai_msg)
            else:
                oai_messages.append({"role": "assistant", "content": content})
        elif msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        oai_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": block["tool_use_id"],
                                "content": str(block.get("content", "")),
                            }
                        )
                    else:
                        oai_messages.append({"role": "user", "content": str(block)})
            else:
                oai_messages.append({"role": "user", "content": content})
    system = kwargs.get("system", "")
    if system:
        oai_messages.insert(0, {"role": "system", "content": system})
    return oai_messages


async def _call_openai_compatible(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 120.0,
    extra_headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    """Call an OpenAI-compatible endpoint with Anthropic-style kwargs."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        default_headers=extra_headers or {},
    )
    oai_messages = _convert_to_oai_messages(kwargs)
    oai_tools = _anthropic_tools_to_openai(kwargs.get("tools", []))
    oai_resp = await client.chat.completions.create(
        model=model,
        max_tokens=kwargs.get("max_tokens", 4096),
        messages=oai_messages,
        tools=oai_tools if oai_tools else openai.NOT_GIVEN,
        tool_choice="auto" if oai_tools else openai.NOT_GIVEN,
    )
    return _OpenAIResponseAdapter(oai_resp)


async def _messages_create_with_retry(
    client: anthropic.AsyncAnthropic,
    *,
    max_overload_attempts: int = 5,
    **kwargs: Any,
) -> Any:
    """Call client.messages.create with retry, circuit breaker, and provider fallback.

    Retry strategy:
    - 529 overloaded: up to ``max_overload_attempts`` times with 30s backoff.
    - 402/429 billing: trip the circuit breaker and fall through to fallbacks.
    - Circuit breaker active: skip Anthropic entirely on this call.

    Fallback chain (first available wins):
    1. OpenRouter (``FACTORY_OPENROUTER_API_KEY``)
    2. MiniMax (``FACTORY_MINIMAX_API_KEY``)
    """
    settings = get_settings()
    last_exc: BaseException | None = None

    if not _is_anthropic_blocked():
        for attempt in range(max_overload_attempts):
            try:
                return await client.messages.create(**kwargs)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529 and attempt < max_overload_attempts - 1:
                    wait = 30 * (2**attempt)
                    logger.warning(
                        "pm_agent: Anthropic overloaded (529), retrying in %ds "
                        "(attempt %d/%d)",
                        wait,
                        attempt + 1,
                        max_overload_attempts,
                    )
                    await asyncio.sleep(wait)
                    last_exc = exc
                elif _is_billing_error(exc):
                    _block_anthropic(seconds=900.0)
                    last_exc = exc
                    break  # fall through to fallback chain
                else:
                    raise
    else:
        logger.info("pm_agent: Anthropic circuit breaker active — using fallback")

    # ── Fallback 1: OpenRouter ────────────────────────────────────────────────
    if settings.openrouter_api_key:
        logger.warning(
            "pm_agent: falling back to OpenRouter (model=%s)", settings.pm_openrouter_model
        )
        return await _call_openai_compatible(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            model=settings.pm_openrouter_model,
            timeout=120.0,
            extra_headers={
                "HTTP-Referer": settings.meshwiki_url,
                "X-Title": "MeshWiki Factory",
            },
            **kwargs,
        )

    # ── Fallback 2: MiniMax ───────────────────────────────────────────────────
    if settings.minimax_api_key:
        logger.warning("pm_agent: falling back to MiniMax")
        return await _call_openai_compatible(
            api_key=settings.minimax_api_key,
            base_url="https://api.minimax.io/v1",
            model="MiniMax-M2.7",
            timeout=60.0,
            **kwargs,
        )

    if last_exc is not None:
        raise last_exc  # type: ignore[misc]
    raise RuntimeError("No Anthropic, OpenRouter, or MiniMax API key configured")


def _build_subtask(tool_input: dict[str, Any], parent_thread_id: str) -> SubTask:
    """Build a SubTask TypedDict from PM tool call input.

    Args:
        tool_input: The ``input`` dict from a ``meshwiki_create_subtask`` tool call.
        parent_thread_id: The LangGraph thread ID of the parent task.

    Returns:
        A fully populated SubTask with sensible defaults.
    """
    subtask_id = f"{parent_thread_id}-sub-{uuid.uuid4().hex[:6]}"
    return SubTask(
        id=subtask_id,
        wiki_page=tool_input["page_name"],
        parent_task=tool_input.get("parent_task", ""),
        title=tool_input["title"],
        description=tool_input["description"],
        status="pending",
        assigned_grinder=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        attempt=0,
        max_attempts=3,
        error_log=[],
        files_touched=tool_input.get("expected_files", []),
        acceptance_criteria=tool_input.get("acceptance_criteria", []),
        token_budget=tool_input.get("token_budget", 50000),
        tokens_used=0,
        review_feedback=None,
        code_skeleton=tool_input.get("code_skeleton") or None,
    )


async def decompose_with_pm(
    state: FactoryState,
    meshwiki_client: "MeshWikiClient",
    github_client: "GitHubClient | None",
) -> dict[str, Any]:
    """Run the PM agentic loop to decompose a parent task into subtasks.

    1. Reads context pages from MeshWiki.
    2. Builds a user message asking Claude to decompose the task.
    3. Runs the agentic loop (max 20 tool calls).
    4. Returns the list of SubTask objects created via ``meshwiki_create_subtask``.
    5. Tracks incremental cost from Anthropic API responses.

    Args:
        state: Current FactoryState with task details.
        meshwiki_client: Async client for the MeshWiki JSON API.
        github_client: GitHub client (unused during decomposition, may be None).

    Returns:
        Dict with ``subtasks`` list and ``incremental_cost_usd`` float.
    """
    client = anthropic.AsyncAnthropic(
        api_key=get_settings().anthropic_api_key or None, timeout=600.0
    )
    subtasks: list[SubTask] = []
    parent_thread_id = state["thread_id"]
    incremental_cost_usd: float = 0.0
    model = get_settings().pm_decompose_model

    # Read context pages
    context_parts: list[str] = []
    for page_name in ["Architecture_Overview", "CLAUDE", "TODO"]:
        page = await meshwiki_client.get_page(page_name)
        if page:
            content = page.get("content", "")
            context_parts.append(f"### {page_name}\n\n{content}")
            logger.debug("decompose_with_pm: loaded context page %s", page_name)
        else:
            logger.debug("decompose_with_pm: context page %s not found", page_name)

    context_block = "\n\n---\n\n".join(context_parts) if context_parts else "(none)"

    task_wiki_page = state["task_wiki_page"]
    task_title = state.get("title", task_wiki_page)
    user_message = (
        f"## Parent Task: {task_title}\n\n"
        f"**Wiki page path:** `{task_wiki_page}`\n\n"
        f"**Requirements:**\n{state.get('requirements', '')}\n\n"
        f"## Context Pages\n\n{context_block}\n\n"
        "Please decompose this task into concrete, independently implementable subtasks. "
        "Use the `meshwiki_create_subtask` tool for each subtask. "
        f"Subtask page names must use the format `{task_wiki_page}_TASK001_Short_title`, "
        f"`{task_wiki_page}_TASK002_Short_title`, etc. "
        "Use underscores throughout — do NOT use slashes in page names. "
        "Read additional wiki pages with `meshwiki_read_page` if you need more context."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    tool_calls_remaining = 20

    while tool_calls_remaining > 0:
        response = await _messages_create_with_retry(
            client,
            model=model,
            max_tokens=8192,
            system=PM_SYSTEM_PROMPT,
            tools=PM_TOOLS,
            messages=messages,
        )

        if hasattr(response, "usage") and response.usage:
            effective_model = getattr(response, "_response_model", model)
            incremental_cost_usd += tokens_to_usd(response.usage, effective_model)

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            logger.info(
                "decompose_with_pm: end_turn after %d tool calls, %d subtasks created",
                20 - tool_calls_remaining,
                len(subtasks),
            )
            break

        # Process tool use blocks
        tool_results: list[dict[str, Any]] = []
        has_tool_use = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            has_tool_use = True
            tool_calls_remaining -= 1

            tool_name: str = block.name
            tool_input: dict[str, Any] = block.input

            if tool_name == "meshwiki_create_subtask":
                subtask = _build_subtask(tool_input, parent_thread_id)
                subtasks.append(subtask)
                result_content = f"Subtask created: {subtask['id']}"
                logger.info(
                    "decompose_with_pm: created subtask %s (%s)",
                    subtask["id"],
                    subtask["title"],
                )
            elif tool_name == "meshwiki_read_page":
                page = await meshwiki_client.get_page(tool_input["page_name"])
                if page:
                    result_content = page.get("content", "")
                else:
                    result_content = f"Page '{tool_input['page_name']}' not found."
            elif tool_name == "github_read_file":
                if github_client is not None:
                    try:
                        result_content = await github_client.get_file_content(
                            tool_input["path"],
                            ref=tool_input.get("ref", "staging"),
                        )
                    except Exception as exc:
                        result_content = f"Error reading file: {exc}"
                else:
                    result_content = "GitHub client not available."
            else:
                result_content = "Tool not available during decomposition"

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_content,
                }
            )

        if not has_tool_use:
            # No tool calls in this response — stop
            break

        messages.append({"role": "user", "content": tool_results})

        if tool_calls_remaining <= 0:
            logger.warning(
                "decompose_with_pm: tool call budget exhausted, stopping with %d subtasks",
                len(subtasks),
            )
            break

    return {"subtasks": subtasks, "incremental_cost_usd": incremental_cost_usd}


async def review_with_pm(
    state: FactoryState,
    subtask: SubTask,
    meshwiki_client: "MeshWikiClient",
    github_client: "GitHubClient",
) -> dict[str, Any]:
    """Run the PM agentic loop to review a grinder's PR.

    Args:
        state: Current FactoryState.
        subtask: The SubTask whose PR should be reviewed.
        meshwiki_client: Async client for the MeshWiki JSON API.
        github_client: GitHub client for fetching PR diffs.

    Returns:
        Dict with ``decision`` ("approved" | "changes_requested") and
        optional ``feedback`` string.
    """
    settings = get_settings()
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key or None, timeout=600.0
    )
    incremental_cost_usd: float = 0.0

    pr_number: int | None = subtask.get("pr_number") or _extract_pr_number(
        subtask.get("pr_url", "")
    )
    diff = ""
    if pr_number is not None:
        diff = await github_client.get_pr_diff(pr_number)

    # Read the subtask wiki page for acceptance criteria
    acceptance_criteria = ""
    page = await meshwiki_client.get_page(subtask["wiki_page"])
    if page:
        acceptance_criteria = page.get("content", "")

    branch_name = subtask.get("branch_name") or ""

    # Cap diff size to avoid burning tokens on huge diffs
    diff_lines = diff.splitlines()
    max_lines = settings.pm_review_max_diff_lines
    diff_truncated = False
    if len(diff_lines) > max_lines:
        diff = "\n".join(diff_lines[:max_lines])
        diff_truncated = True

    truncation_notice = (
        f"\n\n*(Diff truncated at {max_lines} lines. "
        "Use `github_read_file` to inspect full files if needed.)*"
        if diff_truncated
        else ""
    )

    user_message = (
        f"## Subtask: {subtask['title']}\n\n"
        f"**Acceptance Criteria (from wiki page):**\n{acceptance_criteria}\n\n"
        f"## PR Diff (branch: `{branch_name}`)\n\n```diff\n{diff}\n```"
        f"{truncation_notice}\n\n"
        "**Important:** The diff above shows only changes relative to the PR base branch "
        "(staging), not relative to main. Changes that were already on staging will NOT "
        "appear in the diff even if they are part of the full implementation. Before "
        "requesting changes for a missing feature, use `github_read_file` with "
        f'`ref: "{branch_name}"` to read the actual current file and verify whether '
        "the feature is already present.\n\n"
        "Please review this PR. "
        "Use `pm_approve_pr` if the implementation meets all acceptance criteria, "
        "or `pm_request_changes` with specific feedback if it does not."
    )

    # ── Triage pass (cheap model) ─────────────────────────────────────────────
    # Run a fast single-shot review with the triage model. If it approves,
    # skip the full agentic review entirely. If it requests changes, fall
    # through to the full Sonnet review so the grinder gets detailed feedback.
    triage_model = settings.pm_triage_model
    if triage_model:
        triage_prompt = (
            f"## Subtask: {subtask['title']}\n\n"
            f"**Acceptance Criteria:**\n{acceptance_criteria}\n\n"
            f"## PR Diff (branch: `{branch_name}`)\n\n```diff\n{diff}\n```"
            f"{truncation_notice}\n\n"
            "Quick triage: does this PR meet its acceptance criteria? "
            "Reply with exactly one of:\n"
            "- APPROVED — implementation is correct and complete\n"
            "- CHANGES_REQUESTED — briefly state what is missing or wrong\n\n"
            "Be lenient on style; flag only functional gaps or missing acceptance criteria."
        )
        try:
            triage_response = await client.messages.create(
                model=triage_model,
                max_tokens=512,
                messages=[{"role": "user", "content": triage_prompt}],
            )
            if hasattr(triage_response, "usage") and triage_response.usage:
                incremental_cost_usd += tokens_to_usd(
                    triage_response.usage, triage_model
                )
            triage_text = "".join(
                b.text for b in triage_response.content if hasattr(b, "text")
            ).strip()
            logger.info(
                "review_with_pm: triage (%s) verdict for %s: %s",
                triage_model,
                subtask["id"],
                triage_text[:120],
            )
            if triage_text.upper().startswith("APPROVED"):
                return {
                    "decision": "approved",
                    "feedback": triage_text,
                    "incremental_cost_usd": incremental_cost_usd,
                }
            # Triage flagged issues — fall through to full review with Sonnet
            # so the grinder gets actionable, detailed feedback.
            logger.info(
                "review_with_pm: triage requested changes — escalating to full review (%s)",
                settings.pm_review_model,
            )
        except Exception as exc:
            logger.warning("review_with_pm: triage pass failed (%s) — skipping", exc)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    decision: str | None = None
    feedback: str | None = None
    tool_calls_remaining = 10

    while tool_calls_remaining > 0:
        response = await client.messages.create(
            model=settings.pm_review_model,
            max_tokens=4096,
            system=PM_SYSTEM_PROMPT,
            tools=PM_TOOLS,
            messages=messages,
        )

        if hasattr(response, "usage") and response.usage:
            incremental_cost_usd += tokens_to_usd(
                response.usage, settings.pm_review_model
            )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        tool_results: list[dict[str, Any]] = []
        has_tool_use = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            has_tool_use = True
            tool_calls_remaining -= 1

            tool_name: str = block.name
            tool_input: dict[str, Any] = block.input

            if tool_name == "pm_approve_pr":
                decision = "approved"
                feedback = tool_input.get("comment")
                result_content = "PR approved."
                logger.info(
                    "review_with_pm: approved subtask %s", tool_input.get("subtask_id")
                )
            elif tool_name == "pm_request_changes":
                decision = "changes_requested"
                feedback = tool_input.get("feedback") or None
                if not feedback:
                    logger.warning(
                        "review_with_pm: pm_request_changes called with empty feedback for subtask %s",
                        tool_input.get("subtask_id"),
                    )
                result_content = "Changes requested."
                logger.info(
                    "review_with_pm: changes requested for subtask %s",
                    tool_input.get("subtask_id"),
                )
            elif tool_name == "meshwiki_read_page":
                page = await meshwiki_client.get_page(tool_input["page_name"])
                if page:
                    result_content = page.get("content", "")
                else:
                    result_content = f"Page '{tool_input['page_name']}' not found."
            elif tool_name == "github_read_file":
                try:
                    ref = tool_input.get("ref") or "staging"
                    result_content = await github_client.get_file_content(
                        tool_input["path"], ref=ref
                    )
                except Exception as exc:
                    result_content = f"Error reading file: {exc}"
            else:
                result_content = "Tool not available during review"

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_content,
                }
            )

        if not has_tool_use:
            break

        messages.append({"role": "user", "content": tool_results})

        # If we got a decision, stop
        if decision is not None:
            break

        if tool_calls_remaining <= 0:
            logger.warning("review_with_pm: tool call budget exhausted")
            break

    return {
        "decision": decision or "changes_requested",
        "feedback": feedback,
        "incremental_cost_usd": incremental_cost_usd,
    }


async def diagnose_with_pm(
    subtask: "SubTask",
    terminal_log: str,
    meshwiki_client: "MeshWikiClient",
) -> dict[str, Any]:
    """Ask the PM to diagnose a grinder failure and rewrite the task description.

    Sends the terminal log and current task description to the PM in a single
    non-tool-use message and asks it to produce a revised description that will
    unblock the grinder on retry.

    Args:
        subtask: The failed SubTask.
        terminal_log: Raw terminal output from the failed grinder run.
        meshwiki_client: Client for reading the subtask wiki page.

    Returns:
        Dict with ``revised_description`` (str) and ``incremental_cost_usd`` (float).
    """
    settings = get_settings()
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key or None, timeout=600.0
    )

    page = await meshwiki_client.get_page(subtask["wiki_page"])
    wiki_content = page.get("content", "") if page else ""

    user_message = (
        f"## Failed subtask: {subtask['title']}\n\n"
        f"**Current description / acceptance criteria:**\n{subtask.get('description', '')}\n\n"
        f"**Wiki page content:**\n{wiki_content}\n\n"
        f"**Terminal log from failed grinder run (last attempt):**\n```\n{terminal_log[-6000:]}\n```\n\n"
        "Diagnose why the grinder failed and produce a revised task description that will "
        "unblock the next retry. Rewrite the acceptance criteria to remove impossible "
        "constraints (e.g. unavailable tools, wrong test frameworks) and add explicit "
        "guidance based on what went wrong. Be concrete and actionable.\n\n"
        "Reply with ONLY the revised description/acceptance criteria — no preamble, "
        "no explanation, just the updated text the grinder should receive."
    )

    response = await _messages_create_with_retry(
        client,
        model=settings.pm_decompose_model,
        max_tokens=2048,
        system=PM_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    incremental_cost_usd = tokens_to_usd(response.usage, settings.pm_decompose_model)

    revised = ""
    for block in response.content:
        if hasattr(block, "text"):
            revised += block.text

    logger.info(
        "diagnose_with_pm: produced revised description for subtask %s (%d chars)",
        subtask["id"],
        len(revised),
    )
    return {"revised_description": revised.strip(), "incremental_cost_usd": incremental_cost_usd}
