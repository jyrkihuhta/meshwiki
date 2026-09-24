"""Decompose node: PM agent breaks the task into subtasks."""

from __future__ import annotations

import asyncio
import logging

from ..agents.pm_agent import SubTask, decompose_with_pm
from ..config import get_settings
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
    criteria_list = subtask.get("acceptance_criteria") or []
    criteria_lines = "\n".join(f"- [ ] {c}" for c in criteria_list)
    files_lines = "\n".join(f"- `{f}`" for f in (subtask.get("files_touched") or []))

    description = subtask.get("description", "")

    criteria_block = criteria_lines or "- [ ] See description"
    files_block = files_lines or "_(none specified)_"

    estimation_label = "m"

    code_skeleton = subtask.get("code_skeleton", "")
    skeleton_section = (
        f"\n## Code Skeleton\n\nAdapt the following existing implementation:\n\n"
        f"```python\n{code_skeleton}\n```\n"
        if code_skeleton
        else ""
    )

    return (
        f"---\n"
        f'title: "{subtask["title"]}"\n'
        f"type: task\n"
        f"status: planned\n"
        f"assignee: factory\n"
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
        f"{skeleton_section}\n"
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
    settings = get_settings()
    logger.info(
        "decompose: decomposing task %s", state.get("task_wiki_page", "<unknown>")
    )

    if settings.dry_run:
        return await _decompose_dry_run(state, settings.dry_run_step_delay_seconds)

    redecompose_context: str | None = state.get("redecompose_context")

    async with MeshWikiClient() as meshwiki_client:
        result = await decompose_with_pm(
            state, meshwiki_client, None, redecompose_context=redecompose_context
        )
        subtasks = result["subtasks"]
        incremental_cost = result.get("incremental_cost_usd", 0.0)

        parent_task = state.get("task_wiki_page", "")

        # Guard: reject any subtask whose parent_task field doesn't point directly
        # to this epic — the PM must not create nested subtasks (two-level chains).
        # Normalise underscores↔spaces (storage uses spaces; PM may emit underscores).
        def _norm(s: str) -> str:
            return s.replace("_", " ").strip()

        valid_subtasks = []
        for subtask in subtasks:
            subtask_parent = subtask.get("parent_task", "")
            if _norm(subtask_parent) != _norm(parent_task):
                logger.error(
                    "decompose: rejecting subtask %s — parent_task '%s' does not "
                    "match epic '%s'; nested subtasks are not allowed",
                    subtask["wiki_page"],
                    subtask_parent,
                    parent_task,
                )
                continue
            valid_subtasks.append(subtask)

        if len(valid_subtasks) != len(subtasks):
            logger.warning(
                "decompose: dropped %d nested subtask(s) out of %d",
                len(subtasks) - len(valid_subtasks),
                len(subtasks),
            )
        subtasks = valid_subtasks

        dispatched: list[SubTask] = []
        for subtask in subtasks:
            page_content = _build_subtask_page(subtask, parent_task)
            try:
                await meshwiki_client.create_page(subtask["wiki_page"], page_content)
                logger.info("decompose: created wiki page %s", subtask["wiki_page"])
                dispatched.append(subtask)
            except Exception as exc:
                logger.error(
                    "decompose: failed to create wiki page %s — skipping dispatch: %s",
                    subtask["wiki_page"],
                    exc,
                )

        if subtasks and not dispatched:
            raise RuntimeError(
                f"decompose: all {len(subtasks)} subtask page(s) failed to create "
                f"for parent task {parent_task!r} — aborting dispatch"
            )

        if len(dispatched) < len(subtasks):
            logger.warning(
                "decompose: only %d/%d subtask pages created; dispatching partial set",
                len(dispatched),
                len(subtasks),
            )

        try:
            await meshwiki_client.transition_task(parent_task, "decomposed")
            logger.info(
                "decompose: transitioned parent task %s to decomposed", parent_task
            )
        except Exception as exc:
            logger.error(
                "decompose: failed to transition parent task %s: %s", parent_task, exc
            )

    return {
        "subtasks": dispatched,
        "graph_status": "dispatching",
        "incremental_costs_usd": [incremental_cost],
        "redecompose_context": None,  # consumed; clear so it doesn't linger
    }


async def _decompose_dry_run(state: FactoryState, delay: float) -> dict:
    """Return a single canned subtask without calling the PM agent."""
    parent_task = state.get("task_wiki_page", "dry-run-task")
    title = state.get("title") or parent_task
    subtask_id = f"{parent_task}/DRY001"
    subtask_page = subtask_id

    logger.info(
        "decompose: DRY RUN — simulating decomposition for %s (delay=%.1fs)",
        parent_task,
        delay,
    )
    await asyncio.sleep(delay)

    subtask: SubTask = SubTask(
        id=subtask_id,
        title=f"[dry-run] {title}",
        wiki_page=subtask_page,
        token_budget=1000,
        status="pending",
        parent_task=parent_task,
    )

    async with MeshWikiClient() as meshwiki_client:
        page_content = _build_subtask_page(subtask, parent_task)
        try:
            await meshwiki_client.create_page(subtask_page, page_content)
        except Exception as exc:
            logger.warning("decompose dry-run: failed to create subtask page: %s", exc)

        try:
            await meshwiki_client.transition_task(parent_task, "decomposed")
        except Exception as exc:
            logger.warning("decompose dry-run: failed to transition parent task: %s", exc)

    return {
        "subtasks": [subtask],
        "graph_status": "dispatching",
        "incremental_costs_usd": [0.0],
    }
