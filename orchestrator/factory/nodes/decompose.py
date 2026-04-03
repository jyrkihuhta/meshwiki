"""Decompose node: PM agent breaks the task into subtasks."""

from __future__ import annotations

import logging

from ..agents.pm_agent import SubTask, decompose_with_pm
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState

logger = logging.getLogger(__name__)


def _build_subtask_page(subtask: SubTask, parent_task: str) -> str:
    """Build a Markdown wiki page for a subtask with YAML frontmatter.

    Args:
        subtask: The SubTask to render.
        parent_task: Parent task wiki page name.

    Returns:
        Full page content as a Markdown string.
    """
    criteria_lines = "\n".join(
        f"- [ ] {criterion}" for criterion in (subtask.get("files_touched") or [])
    )
    # files_touched holds expected_files from the PM tool call
    files_lines = "\n".join(f"- `{f}`" for f in (subtask.get("files_touched") or []))

    # We store acceptance_criteria in description for now — the PM agent
    # stores it there via _build_subtask. Render it from description.
    description = subtask.get("description", "")

    # Pull acceptance criteria out of subtask if available (stored via
    # the tool input; _build_subtask merges them into description).
    # We use the description field as-is and leave criteria blank unless
    # the caller passes them explicitly.
    criteria_block = criteria_lines or "- [ ] See description"
    files_block = files_lines or "_(none specified)_"

    estimation_label = "m"

    return (
        f"---\n"
        f'title: "{subtask["title"]}"\n'
        f"type: task\n"
        f"status: planned\n"
        f"skip_decomposition: true\n"
        f'parent_task: "{parent_task}"\n'
        f'estimation: "{estimation_label}"\n'
        f"tags:\n"
        f"  - factory\n"
        f"---\n"
        f"\n"
        f"<<TaskStatus>>\n"
        f"\n"
        f"# {subtask['title']}\n"
        f"\n"
        f"## Description\n"
        f"\n"
        f"{description}\n"
        f"\n"
        f"## Acceptance Criteria\n"
        f"\n"
        f"{criteria_block}\n"
        f"\n"
        f"## Files Expected\n"
        f"\n"
        f"{files_block}\n"
        f"\n"
        f"## Agent Log\n"
        f"\n"
        f"<!-- Agents append progress notes below this line -->\n"
    )


async def decompose_node(state: FactoryState) -> dict:
    """Call the PM agent to decompose the parent task into subtasks.

    Runs the PM agentic loop, writes each subtask as a wiki page, transitions
    each subtask to 'planned', and transitions the parent task to 'decomposed'.

    Args:
        state: Current FactoryState.

    Returns:
        Partial state update with ``subtasks`` and ``graph_status``.
    """
    logger.info(
        "decompose: decomposing task %s", state.get("task_wiki_page", "<unknown>")
    )

    meshwiki_client = MeshWikiClient()
    # github_client is not needed for decomposition
    github_client = None

    subtasks = await decompose_with_pm(state, meshwiki_client, github_client)

    parent_task = state.get("task_wiki_page", "")

    for subtask in subtasks:
        page_content = _build_subtask_page(subtask, parent_task)
        try:
            await meshwiki_client.create_page(subtask["wiki_page"], page_content)
            logger.info("decompose: created wiki page %s", subtask["wiki_page"])
        except Exception as exc:
            logger.error(
                "decompose: failed to create wiki page %s: %s",
                subtask["wiki_page"],
                exc,
            )

        try:
            await meshwiki_client.transition_task(subtask["wiki_page"], "planned")
            logger.info("decompose: transitioned %s to planned", subtask["wiki_page"])
        except Exception as exc:
            logger.error(
                "decompose: failed to transition %s: %s",
                subtask["wiki_page"],
                exc,
            )

    try:
        await meshwiki_client.transition_task(parent_task, "decomposed")
        logger.info("decompose: transitioned parent task %s to decomposed", parent_task)
    except Exception as exc:
        logger.error(
            "decompose: failed to transition parent task %s: %s", parent_task, exc
        )

    return {"subtasks": subtasks, "graph_status": "awaiting_approval"}
