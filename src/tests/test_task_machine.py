"""Unit tests for the task state machine."""

import pytest

from meshwiki.core.storage import FileStorage
from meshwiki.core.task_machine import (
    TASK_TRANSITIONS,
    InvalidTransitionError,
    transition_task,
)


@pytest.fixture
def storage(tmp_path):
    return FileStorage(tmp_path)


async def _make_task(storage: FileStorage, name: str, status: str = "draft") -> None:
    content = f"---\ntype: task\nstatus: {status}\n---\nTest task."
    await storage.save_page(name, content)


# ---------------------------------------------------------------------------
# State graph sanity
# ---------------------------------------------------------------------------


def test_all_states_have_entries():
    """Every state referenced as a target must also have an entry as a source."""
    all_targets = {s for targets in TASK_TRANSITIONS.values() for s in targets}
    missing = all_targets - set(TASK_TRANSITIONS)
    assert not missing, f"States referenced as targets but not as sources: {missing}"


def test_done_has_no_outgoing():
    assert TASK_TRANSITIONS["done"] == []


def test_blocked_reachable_from_most_states():
    # "merged" and "done" are terminal-ish: once a PR is merged it cannot be blocked.
    for state, targets in TASK_TRANSITIONS.items():
        if state not in ("done", "blocked", "merged"):
            assert "blocked" in targets, f"State {state!r} cannot transition to blocked"


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_transition_draft_to_planned(storage):
    await _make_task(storage, "Task_0001", "draft")
    result = await transition_task(storage, "Task_0001", "planned")
    assert result["status"] == "planned"


@pytest.mark.asyncio
async def test_valid_transition_applies_extra_fields(storage):
    await _make_task(storage, "Task_0002", "approved")
    result = await transition_task(
        storage,
        "Task_0002",
        "in_progress",
        extra_fields={"assignee": "grinder-1", "branch": "factory/task-0002-foo"},
    )
    assert result["assignee"] == "grinder-1"
    assert result["branch"] == "factory/task-0002-foo"


@pytest.mark.asyncio
async def test_full_happy_path(storage):
    """Chain through the main happy-path states."""
    await _make_task(storage, "Task_0003", "draft")
    for new_status in [
        "planned",
        "decomposed",
        "approved",
        "in_progress",
        "review",
        "merged",
        "done",
    ]:
        result = await transition_task(storage, "Task_0003", new_status)
        assert result["status"] == new_status


@pytest.mark.asyncio
async def test_default_status_is_draft(storage):
    """A page without an explicit status field defaults to draft."""
    await storage.save_page("Task_0004", "---\ntype: task\n---\nNo status.")
    result = await transition_task(storage, "Task_0004", "planned")
    assert result["status"] == "planned"


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_transition_raises(storage):
    await _make_task(storage, "Task_0005", "draft")
    with pytest.raises(InvalidTransitionError):
        await transition_task(storage, "Task_0005", "done")


@pytest.mark.asyncio
async def test_done_has_no_transitions(storage):
    await _make_task(storage, "Task_0006", "done")
    with pytest.raises(InvalidTransitionError):
        await transition_task(storage, "Task_0006", "planned")


@pytest.mark.asyncio
async def test_missing_page_raises(storage):
    with pytest.raises(ValueError, match="not found"):
        await transition_task(storage, "NonExistent", "planned")
