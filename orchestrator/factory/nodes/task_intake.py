"""Task intake node: validates and loads the parent task from MeshWiki."""

from __future__ import annotations

import logging

from ..config import get_settings
from ..integrations.meshwiki_client import MeshWikiClient
from ..state import FactoryState, SubTask

logger = logging.getLogger(__name__)


async def task_intake_node(state: FactoryState) -> dict:
    """Validate the incoming task and load full requirements from MeshWiki.

    Fetches the task page via MeshWikiClient, extracts the title from
    metadata and requirements from page content.

    If the task metadata contains ``skip_decomposition: true``, a single
    SubTask is built from the parent task itself, ``decomposition_approved``
    is set to ``True``, and graph_status is set to ``"grinding"`` so the
    graph bypasses decompose and human_review_plan entirely.

    Otherwise, graph_status is set to ``"decomposing"`` for the normal flow.

    Args:
        state: Current FactoryState; ``task_wiki_page`` must be set.

    Returns:
        Partial state update with ``title``, ``requirements``, and
        ``graph_status``.  For direct-grind tasks also includes ``subtasks``
        and ``decomposition_approved``.
    """
    task_wiki_page: str = state.get("task_wiki_page", "")
    logger.info("task_intake: loading task %s", task_wiki_page)

    async with MeshWikiClient() as meshwiki_client:
        page = await meshwiki_client.get_page(task_wiki_page)

    if page is None:
        logger.error("task_intake: task page %s not found", task_wiki_page)
        return {
            "title": task_wiki_page,
            "requirements": "",
            "graph_status": "failed",
            "error": f"task page not found: {task_wiki_page}",
        }

    metadata: dict = page.get("metadata") or {}

    # Guardrail: only process pages assigned to the factory.
    if metadata.get("assignee") != "factory":
        logger.warning(
            "task_intake: page %s is not assigned to factory (assignee=%s) — aborting",
            task_wiki_page,
            metadata.get("assignee"),
        )
        return {
            "title": task_wiki_page,
            "requirements": "",
            "graph_status": "failed",
            "error": "page not assigned to factory",
        }

    # Guardrail: only process task/epic page types.
    page_type = metadata.get("type", "task")
    if page_type not in ("task", "epic"):
        logger.warning(
            "task_intake: page %s has unsupported type %r — aborting",
            task_wiki_page,
            page_type,
        )
        return {
            "title": task_wiki_page,
            "requirements": "",
            "graph_status": "failed",
            "error": f"unsupported page type: {page_type}",
        }

    # Guardrail: reject subtask pages to prevent duplicate top-level graph runs.
    # When escalation retries a failed subtask it transitions the page back to
    # in_progress, firing task.assigned — which would start a second graph run
    # alongside the parent graph that already manages the subtask internally.
    if metadata.get("parent_task"):
        logger.warning(
            "task_intake: page %s is a subtask (parent_task=%r) — "
            "subtask pages must not be run as top-level factory tasks; aborting",
            task_wiki_page,
            metadata.get("parent_task"),
        )
        return {
            "title": task_wiki_page,
            "requirements": "",
            "graph_status": "failed",
            "error": "subtask pages must not be run as top-level factory tasks",
        }

    title: str = metadata.get("title", task_wiki_page)
    requirements: str = page.get("content", "")
    task_uuid: str | None = metadata.get("uuid") or None

    # Resolve target repo — prefer `repo:` frontmatter, fall back to FACTORY_DEFAULT_REPO
    # then FACTORY_GITHUB_REPO so existing tasks without the field keep working.
    settings = get_settings()
    task_repo: str = (
        metadata.get("repo") or settings.default_repo or settings.github_repo
    )

    # Sub-path within the repo where the grinder should focus (e.g. "python/molly/tools/").
    # None → work at repo root (MeshWiki default).
    task_repo_root: str | None = metadata.get("repo_root") or None

    # Artifact type — "tool" | "playbook" | "wordlist" | None/"code" (MeshWiki default).
    artifact_type: str | None = metadata.get("artifact_type") or None

    # Normalise: metadata values from YAML may come back as booleans or strings
    raw_skip = metadata.get("skip_decomposition", False)
    skip_decomposition: bool = raw_skip is True or str(raw_skip).lower() == "true"

    logger.info(
        "task_intake: loaded task '%s' (skip_decomposition=%s)",
        title,
        skip_decomposition,
    )

    if not skip_decomposition:
        # Check for pre-seeded subtask pages (manually created before dispatch).
        # If found, use them directly and skip PM decomposition.
        async with MeshWikiClient() as mc:
            pre_seeded = await mc.list_tasks(
                parent_task=task_wiki_page, assignee="factory"
            )
        _done = {"done", "merged", "failed", "rejected"}
        active_subtasks = [
            t
            for t in pre_seeded
            if (t.get("metadata") or {}).get("status") not in _done
        ]
        if active_subtasks:
            subtasks: list[SubTask] = []
            for t_data in active_subtasks:
                t_meta = t_data.get("metadata") or {}
                t_name: str = t_data["name"]
                async with MeshWikiClient() as mc:
                    t_page = await mc.get_page(t_name)
                t_content = (t_page or {}).get("content", "") if t_page else ""
                t_acceptance = str(t_meta.get("acceptance_criteria") or "")
                description = (
                    f"{t_acceptance}\n\n{t_content}".strip()
                    if t_acceptance
                    else t_content
                )
                raw_budget = t_meta.get("token_budget")
                token_budget: int = 50000
                try:
                    token_budget = int(raw_budget)
                except (ValueError, TypeError, Exception):
                    pass
                st: SubTask = {
                    "id": t_name,
                    "wiki_page": t_name,
                    "parent_task": task_wiki_page,
                    "title": str(t_meta.get("title") or t_name),
                    "description": description,
                    "status": "pending",
                    "attempt": 0,
                    "max_attempts": 3,
                    "error_log": [],
                    "files_touched": [],
                    "acceptance_criteria": [t_acceptance] if t_acceptance else [],
                    "token_budget": token_budget,
                    "tokens_used": 0,
                    "assigned_grinder": None,
                    "branch_name": None,
                    "pr_url": None,
                    "pr_number": None,
                    "review_feedback": None,
                    "code_skeleton": None,
                }
                subtasks.append(st)
            logger.info(
                "task_intake: found %d pre-seeded subtask(s) for '%s' — skipping PM decomposition",
                len(subtasks),
                title,
            )
            return {
                "title": title,
                "requirements": requirements,
                "task_repo": task_repo,
                "task_repo_root": task_repo_root,
                "artifact_type": artifact_type,
                "task_uuid": task_uuid,
                "subtasks": subtasks,
                "decomposition_approved": True,
                "graph_status": "grinding",
            }

    if skip_decomposition:
        # Build a single SubTask from the parent task itself
        files_touched: list[str] = []
        raw_files = metadata.get("expected_files")
        if isinstance(raw_files, list):
            files_touched = [str(f) for f in raw_files]
        elif isinstance(raw_files, str) and raw_files.strip():
            files_touched = [f.strip() for f in raw_files.split(",") if f.strip()]

        token_budget: int = 50000
        raw_budget = metadata.get("token_budget")
        if raw_budget is not None:
            try:
                token_budget = int(raw_budget)
            except (ValueError, TypeError):
                pass

        subtask: SubTask = {
            "id": task_wiki_page,
            "wiki_page": task_wiki_page,
            "parent_task": task_wiki_page,
            "title": title,
            "description": requirements,
            "status": "pending",
            "attempt": 0,
            "max_attempts": 3,
            "error_log": [],
            "files_touched": files_touched,
            "acceptance_criteria": [],
            "token_budget": token_budget,
            "tokens_used": 0,
            "assigned_grinder": None,
            "branch_name": None,
            "pr_url": None,
            "pr_number": None,
            "review_feedback": None,
            "code_skeleton": None,
        }

        return {
            "title": title,
            "requirements": requirements,
            "task_repo": task_repo,
            "task_repo_root": task_repo_root,
            "artifact_type": artifact_type,
            "task_uuid": task_uuid,
            "subtasks": [subtask],
            "decomposition_approved": True,
            "graph_status": "grinding",
        }

    return {
        "title": title,
        "requirements": requirements,
        "task_repo": task_repo,
        "task_repo_root": task_repo_root,
        "artifact_type": artifact_type,
        "task_uuid": task_uuid,
        "graph_status": "decomposing",
    }
