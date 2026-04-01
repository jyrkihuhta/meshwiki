"""State schema for the LangGraph factory orchestrator."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


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


class FactoryState(TypedDict):
    """Full state for a single factory task run (one LangGraph thread)."""

    # Identity
    thread_id: str  # LangGraph thread ID = parent task wiki page name
    task_wiki_page: str
    title: str
    requirements: str  # full page content (markdown)

    # Decomposition
    subtasks: list[SubTask]
    decomposition_approved: bool

    # Execution
    active_grinders: dict[str, str]  # subtask_id -> grinder session id
    completed_subtask_ids: list[str]
    failed_subtask_ids: list[str]

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
