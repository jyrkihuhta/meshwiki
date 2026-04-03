"""PM/Architect agent using Claude Opus 4 via the Anthropic API."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import anthropic

from ..config import get_settings
from ..state import FactoryState, SubTask

if TYPE_CHECKING:
    from ..integrations.github_client import GitHubClient
    from ..integrations.meshwiki_client import MeshWikiClient

logger = logging.getLogger(__name__)

PM_SYSTEM_PROMPT = """
You are the PM/Architect for MeshWiki, an autonomous software development factory.

Your responsibilities:
1. Decompose high-level tasks into concrete, independently implementable subtasks
2. Review grinder-produced code for correctness, style, and adherence to requirements
3. Handle escalations when grinders fail

MeshWiki tech stack: FastAPI, Jinja2, HTMX, Python 3.12+, Rust (graph engine via PyO3).
All code must follow PEP 8, have type hints, use async/await for storage, and include tests.

When decomposing:
- Each subtask should be completable in one grinder session (< 50k tokens)
- Subtasks must be as independent as possible (minimize file overlap)
- Include file paths you expect will be touched in each subtask
- Write clear acceptance criteria

When reviewing:
- Check that tests cover the new code
- Verify the implementation matches the acceptance criteria
- Confirm no regressions to existing functionality
- Flag any security issues immediately
""".strip()

PM_TOOLS: list[dict[str, Any]] = [
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
                    "description": "The MeshWiki page name for this subtask. Use the format '{parent_page_name}/TASK{N:03d} - {Short descriptive title}' where parent_page_name is the wiki page being decomposed and N starts at 001 for each epic (e.g. if decomposing 'Factory/GraphViewEnhancements', create 'Factory/GraphViewEnhancements/TASK001 - Add search feature'). Scan existing subpages of the parent to find the highest N and increment by 1.",
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
        "description": "Request changes on a subtask PR — the implementation does not meet acceptance criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subtask_id": {
                    "type": "string",
                    "description": "The subtask ID being reviewed.",
                },
                "feedback": {
                    "type": "string",
                    "description": "Detailed feedback describing what needs to change.",
                },
            },
            "required": ["subtask_id", "feedback"],
        },
    },
]


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
        token_budget=tool_input.get("token_budget", 50000),
        tokens_used=0,
        review_feedback=None,
    )


async def decompose_with_pm(
    state: FactoryState,
    meshwiki_client: "MeshWikiClient",
    github_client: "GitHubClient | None",
) -> list[SubTask]:
    """Run the PM agentic loop to decompose a parent task into subtasks.

    1. Reads context pages from MeshWiki.
    2. Builds a user message asking Claude to decompose the task.
    3. Runs the agentic loop (max 20 tool calls).
    4. Returns the list of SubTask objects created via ``meshwiki_create_subtask``.

    Args:
        state: Current FactoryState with task details.
        meshwiki_client: Async client for the MeshWiki JSON API.
        github_client: GitHub client (unused during decomposition, may be None).

    Returns:
        List of SubTask TypedDicts produced by the PM agent.
    """
    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key or None)
    subtasks: list[SubTask] = []
    parent_thread_id = state["thread_id"]

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

    user_message = (
        f"## Parent Task: {state.get('title', state['task_wiki_page'])}\n\n"
        f"**Requirements:**\n{state.get('requirements', '')}\n\n"
        f"## Context Pages\n\n{context_block}\n\n"
        "Please decompose this task into concrete, independently implementable subtasks. "
        "Use the `meshwiki_create_subtask` tool for each subtask. "
        "Read additional wiki pages with `meshwiki_read_page` if you need more context."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    tool_calls_remaining = 20

    while tool_calls_remaining > 0:
        response = await client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=8192,
            system=PM_SYSTEM_PROMPT,
            tools=PM_TOOLS,
            messages=messages,
        )

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

    return subtasks


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
    client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key or None)

    pr_number: int | None = subtask.get("pr_number")
    diff = ""
    if pr_number is not None:
        diff = await github_client.get_pr_diff(pr_number)

    # Read the subtask wiki page for acceptance criteria
    acceptance_criteria = ""
    page = await meshwiki_client.get_page(subtask["wiki_page"])
    if page:
        acceptance_criteria = page.get("content", "")

    user_message = (
        f"## Subtask: {subtask['title']}\n\n"
        f"**Acceptance Criteria (from wiki page):**\n{acceptance_criteria}\n\n"
        f"## PR Diff\n\n```diff\n{diff}\n```\n\n"
        "Please review this PR. "
        "Use `pm_approve_pr` if the implementation meets all acceptance criteria, "
        "or `pm_request_changes` with specific feedback if it does not."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    decision: str | None = None
    feedback: str | None = None
    tool_calls_remaining = 10

    while tool_calls_remaining > 0:
        response = await client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=4096,
            system=PM_SYSTEM_PROMPT,
            tools=PM_TOOLS,
            messages=messages,
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
                feedback = tool_input.get("feedback")
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
    }
