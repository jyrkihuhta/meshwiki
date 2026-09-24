"""State schema for the LangGraph factory orchestrator."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _merge_subtasks(current: list, update: list) -> list:
    """Merge subtask lists by ID — update wins per-ID.

    Required so parallel grinder nodes can each write back the full subtasks
    list without LangGraph raising INVALID_CONCURRENT_GRAPH_UPDATE.
    """
    merged: dict = {s["id"]: s for s in (current or [])}
    for s in update or []:
        merged[s["id"]] = s
    return list(merged.values())


def _union_ids(current: list[str], update: list[str]) -> list[str]:
    """Union reducer for subtask ID lists (failed or completed)."""
    return list(set(current) | set(update))


def _append_cost(current: list[float], update: list[float]) -> list[float]:
    """Append reducer for incremental cost lists."""
    return current + update


def _merge_active_grinders(current: list[str], update: list[str]) -> list[str]:
    """Reducer for active_grinders supporting both parallel additions and full reset.

    Parallel grinder branches each return their own subtask ID as a single-element
    list.  The reducer unions these additions into the current set.

    A full reset (e.g. from ``collect_results_node``) is signalled by returning
    an empty list ``[]``, which replaces the current value entirely.  This
    avoids the need for a separate clear mechanism while still allowing
    concurrent ``grind_node`` branches to safely add their IDs without clobbering
    each other.
    """
    if not update:
        # Empty list signals a full reset (e.g. after all grinders complete).
        return []
    return list(dict.fromkeys(list(current) + update))


class SubTask(TypedDict):
    """Represents a single unit of work assigned to a grinder agent."""

    id: str  # unique within parent task, e.g. "task-0042-sub-01"
    wiki_page: str  # MeshWiki page name
    parent_task: str  # wiki page name of the parent epic
    title: str
    description: str
    status: Literal[
        "pending",
        "assigned",
        "in_progress",
        "review",
        "changes_requested",
        "merged",
        "failed",
        "skipped",
    ]
    assigned_grinder: str | None
    branch_name: str | None  # factory/task-0042-sub-01-description
    pr_url: str | None
    pr_number: int | None
    attempt: int  # 0-indexed
    max_attempts: int  # default 3
    error_log: list[str]
    files_touched: list[str]  # estimated, filled in by PM during decomposition
    acceptance_criteria: list[str]  # criteria the grinder must satisfy
    token_budget: int  # max tokens for the grinder session
    tokens_used: int
    review_feedback: str | None  # PM feedback for rejected subtasks
    code_skeleton: (
        str | None
    )  # starter code template provided by PM during decomposition


class FactoryState(TypedDict):
    """Full state for a single factory task run (one LangGraph thread)."""

    # Identity
    thread_id: str  # LangGraph thread ID = task UUID (or page name for legacy tasks)
    task_wiki_page: str
    task_uuid: str | None  # UUID from page frontmatter; None for pre-UUID pages
    title: str
    requirements: str  # full page content (markdown)

    # Decomposition
    subtasks: Annotated[list[SubTask], _merge_subtasks]
    decomposition_approved: bool

    # Execution
    active_grinders: Annotated[
        list[str], _merge_active_grinders
    ]  # subtask_ids currently running in a sandbox
    completed_subtask_ids: Annotated[list[str], _union_ids]
    failed_subtask_ids: Annotated[list[str], _union_ids]

    # PM conversation history (accumulates via add_messages reducer)
    pm_messages: Annotated[list[BaseMessage], add_messages]

    # Human-in-the-loop
    human_approval_response: Literal["approve", "reject", "modify"] | None
    human_feedback: str | None

    # Cost
    cost_usd: float
    incremental_costs_usd: Annotated[list[float], _append_cost]

    # Status
    graph_status: Literal[
        "intake",
        "decomposing",
        "dispatching",
        "grinding",
        "reviewing",
        "completed",
        "failed",
        "escalated",
    ]
    error: str | None
    escalation_decision: Literal["retry", "redecompose", "abandon"] | None

    # Target repo for git clone — read from task frontmatter `repo:` field; falls
    # back to FACTORY_GITHUB_REPO when absent.  Set once by task_intake_node.
    task_repo: str | None

    # Sub-path within the cloned repo where the work lives, e.g. "python/molly/tools/".
    # None means the grinder works at the repo root (default for MeshWiki tasks).
    task_repo_root: str | None

    # Artifact type — shapes the grinder's system prompt and tool/test paths.
    # None or "code" → MeshWiki default behaviour.
    # "tool" | "playbook" | "wordlist" → Molly armory artifact types.
    artifact_type: str | None

    # Redecompose loop control — set by escalate_node when decision=="redecompose".
    redecompose_context: str | None  # failure summary passed to PM for the next decompose
    redecompose_attempt: int  # counts redecompose rounds; escalate_node caps at MAX

    # Per-branch routing — echoed back by grind_node / pm_review_node so the
    # conditional routing function can identify which subtask this branch is for.
    # Annotated with a last-write-wins reducer so parallel branches can each echo
    # their own value without raising INVALID_CONCURRENT_GRAPH_UPDATE.
    _current_subtask_id: Annotated[str | None, lambda _cur, new: new]
