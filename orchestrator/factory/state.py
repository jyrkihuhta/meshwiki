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


class SubTask(TypedDict):
    """Represents a single unit of work assigned to a grinder agent."""

    id: str  # unique within parent task, e.g. "task-0042-sub-01"
    wiki_page: str  # MeshWiki page name
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
    token_budget: int  # max tokens for the grinder session
    tokens_used: int
    review_feedback: str | None  # PM feedback for rejected subtasks
    code_skeleton: (
        str | None
    )  # starter code template provided by PM during decomposition


class FactoryState(TypedDict):
    """Full state for a single factory task run (one LangGraph thread)."""

    # Identity
    thread_id: str  # LangGraph thread ID = parent task wiki page name
    task_wiki_page: str
    title: str
    requirements: str  # full page content (markdown)

    # Decomposition
    subtasks: Annotated[list[SubTask], _merge_subtasks]
    decomposition_approved: bool

    # Execution
    active_grinders: dict[str, str]  # subtask_id -> grinder session id
    completed_subtask_ids: Annotated[list[str], _union_ids]
    failed_subtask_ids: Annotated[list[str], _union_ids]

    # PM conversation history (accumulates via add_messages reducer)
    pm_messages: Annotated[list[BaseMessage], add_messages]

    # Human-in-the-loop
    human_approval_response: Literal["approve", "reject", "modify"] | None
    human_feedback: str | None

    # Cost
    cost_usd: float

    # Status
    graph_status: Literal[
        "intake",
        "decomposing",
        "awaiting_approval",
        "grinding",
        "reviewing",
        "completed",
        "failed",
        "escalated",
    ]
    error: str | None
    escalation_decision: Literal["retry", "redecompose", "abandon"] | None
