"""Multi-subtask pipeline tests through the real compiled factory graph.

Every test here drives ``build_graph(MemorySaver())`` end to end and mocks only
the outside world (grinder sandbox, PM model, MeshWiki API, GitHub API).  They
cover the fan-in after per-subtask PM review: the join node (``collect``) must
see the *merged* state of all parallel branches, finalize the parent once every
subtask is terminal, and loop back to ``assign_grinders`` for subtasks that
were held back by the concurrency cap or by file-conflict serialization.

Rounds are identified by the LangGraph superstep in which a grinder ran, read
from the runnable config inside the mocked grinder, so the assertions do not
depend on asyncio scheduling order.

The last sections cover what happens around a run rather than inside one: an
orchestrator restart while grinders are in flight (startup recovery must
resume the checkpoint, not discard it) and a webhook that re-enters a thread
from START.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_config

from factory import webhook_server
from factory.graph import build_graph
from factory.state import FactoryState, SubTask

PARENT = "Task_0042_test"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subtask(index: int, **kwargs: Any) -> SubTask:
    """Return a pending SubTask ``sub-<index>`` touching its own file."""
    defaults: dict[str, Any] = {
        "id": f"sub-{index}",
        "wiki_page": f"Task_0042_Sub_{index:02d}",
        "parent_task": PARENT,
        "title": f"Subtask {index}",
        "description": "Do the thing.",
        "status": "pending",
        "assigned_grinder": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
        "attempt": 0,
        "max_attempts": 3,
        "error_log": [],
        "files_touched": [f"src/module_{index}.py"],
        "acceptance_criteria": [],
        "token_budget": 50000,
        "tokens_used": 0,
        "review_feedback": None,
        "code_skeleton": None,
    }
    defaults.update(kwargs)
    return SubTask(**defaults)


def _make_initial_state(subtasks: list[SubTask]) -> FactoryState:
    """Return a pre-decomposed FactoryState (skips the PM decompose node)."""
    return FactoryState(
        thread_id=PARENT,
        task_wiki_page=PARENT,
        title="Test Task",
        requirements="Implement a test feature.",
        subtasks=subtasks,
        decomposition_approved=True,
        active_grinders=[],
        completed_subtask_ids=[],
        failed_subtask_ids=[],
        pm_messages=[],
        human_approval_response=None,
        human_feedback=None,
        cost_usd=0.0,
        incremental_costs_usd=[],
        graph_status="intake",
        error=None,
        escalation_decision=None,
        _current_subtask_id=None,
    )


def _mock_meshwiki_client() -> AsyncMock:
    """Return an AsyncMock MeshWikiClient usable as an async context manager."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    page_data = {
        "name": PARENT,
        "content": "# Test Task\n\nRequirements here.",
        "metadata": {"title": "Test Task", "status": "planned", "assignee": "factory"},
    }
    client.get_page = AsyncMock(return_value=page_data)
    client.transition_task = AsyncMock(return_value=page_data)
    client.list_tasks = AsyncMock(return_value=[])
    return client


def _mock_github_client() -> AsyncMock:
    """Return an AsyncMock GitHubClient usable as an async context manager."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get_pr = AsyncMock(return_value={"merged": True, "state": "closed"})
    return client


class FakeFactory:
    """Scripted stand-ins for the grinder sandbox, the PM reviewer and the APIs.

    Set ``graph`` and ``config`` after construction to make each grinder call
    read the latest checkpoint and record the ``active_grinders`` it finds.

    Add ``(subtask_id, attempt)`` pairs to ``hang`` to make those grinder calls
    block until the run is cancelled — a sandbox still working when the
    orchestrator stops.  Blocked calls are listed in ``hung`` and are not
    counted in ``grind_calls``.

    Args:
        reviews: Per-subtask list of PM decisions, consumed in order.  A subtask
            with no (remaining) entry is approved.
        grinds: Per-subtask list of grinder outcomes (``"review"`` or
            ``"failed"``), consumed in order.  Default is ``"review"``.
    """

    def __init__(
        self,
        reviews: dict[str, list[str]] | None = None,
        grinds: dict[str, list[str]] | None = None,
    ) -> None:
        self._reviews = {k: list(v) for k, v in (reviews or {}).items()}
        self._grinds = {k: list(v) for k, v in (grinds or {}).items()}
        self.meshwiki = _mock_meshwiki_client()
        self.github = _mock_github_client()
        self.graph: Any = None
        self.config: dict | None = None
        self.grind_calls: list[tuple[str, int]] = []
        self.grind_steps: dict[int, list[str]] = defaultdict(list)
        self.review_calls: list[str] = []
        self.active_seen: list[list[str]] = []
        self.events: list[str] = []
        self.pr_numbers: dict[str, list[int]] = defaultdict(list)
        self.hang: set[tuple[str, int]] = set()
        self.hung: list[str] = []
        self._pr_number = 100

    async def grind_subtask(
        self, state: FactoryState, subtask: SubTask, meshwiki_client: Any
    ) -> dict:
        """Mocked ``grind_subtask``: record the call and return the outcome."""
        if (subtask["id"], subtask["attempt"]) in self.hang:
            self.hung.append(subtask["id"])
            await asyncio.Event().wait()  # never set: ends when the run is cancelled
        step = get_config()["metadata"]["langgraph_step"]
        self.grind_calls.append((subtask["id"], subtask["attempt"]))
        self.grind_steps[step].append(subtask["id"])
        self.events.append(f"grind:{subtask['id']}")
        if self.graph is not None:
            snapshot = await self.graph.aget_state(self.config)
            self.active_seen.append(list(snapshot.values.get("active_grinders") or []))
        outcome = (self._grinds.get(subtask["id"]) or ["review"]).pop(0)
        if outcome == "failed":
            updated = {**subtask, "status": "failed", "error_log": ["boom"]}
        else:
            self._pr_number += 1
            self.pr_numbers[subtask["id"]].append(self._pr_number)
            updated = {
                **subtask,
                "status": "review",
                "pr_number": self._pr_number,
                "pr_url": f"https://github.com/owner/repo/pull/{self._pr_number}",
            }
        return {"subtask": SubTask(**updated), "incremental_cost_usd": 0.0}

    async def review_with_pm(
        self, state: FactoryState, subtask: SubTask, *_clients: Any
    ) -> dict:
        """Mocked ``review_with_pm``: record the call and return the decision."""
        self.review_calls.append(subtask["id"])
        self.events.append(f"review:{subtask['id']}")
        decision = (self._reviews.get(subtask["id"]) or ["approved"]).pop(0)
        feedback = "LGTM!" if decision == "approved" else "Fix tests."
        return {"decision": decision, "feedback": feedback}

    @property
    def rounds(self) -> list[list[str]]:
        """Subtask IDs ground per superstep, in superstep order."""
        return [sorted(self.grind_steps[s]) for s in sorted(self.grind_steps)]

    def ground(self, subtask_id: str) -> int:
        """Return how many times *subtask_id* was sent to the grinder."""
        return sum(1 for sid, _ in self.grind_calls if sid == subtask_id)


@contextmanager
def _patched_world(
    fake: FakeFactory,
    *,
    cap: int | None = None,
    auto_merge: bool = False,
) -> Iterator[None]:
    """Patch every external edge of the graph with *fake*."""
    meshwiki, github = fake.meshwiki, fake.github
    settings = MagicMock()
    settings.auto_merge = auto_merge
    with ExitStack() as stack:
        for module in (
            "task_intake",
            "grind",
            "pm_review",
            "pm_diagnose",
            "escalate",
            "finalize",
        ):
            stack.enter_context(
                patch(f"factory.nodes.{module}.MeshWikiClient", return_value=meshwiki)
            )
        for module in ("pm_review", "merge_check"):
            stack.enter_context(
                patch(f"factory.nodes.{module}.GitHubClient", return_value=github)
            )
        stack.enter_context(
            patch("factory.nodes.grind.grind_subtask", new=fake.grind_subtask)
        )
        stack.enter_context(
            patch("factory.nodes.pm_review.review_with_pm", new=fake.review_with_pm)
        )
        stack.enter_context(
            patch(
                "factory.nodes.pm_diagnose.diagnose_with_pm",
                new=AsyncMock(
                    return_value={
                        "revised_description": "Revised: do the thing properly.",
                        "incremental_cost_usd": 0.0,
                    }
                ),
            )
        )
        stack.enter_context(patch("factory.graph.get_settings", return_value=settings))
        if cap is not None:
            stack.enter_context(
                patch("factory.nodes.assign.FACTORY_MAX_CONCURRENT_SANDBOXES", cap)
            )
        yield


async def _run_until_human_review(graph: Any, state: FactoryState, config: dict) -> Any:
    """Invoke the graph and return the snapshot it stopped at."""
    await graph.ainvoke(state, config=config)
    return await graph.aget_state(config)


async def _approve_and_finish(graph: Any, config: dict) -> dict:
    """Inject the human approval and resume the graph to its end."""
    await graph.aupdate_state(
        config,
        {"human_approval_response": "approve", "human_feedback": None},
        as_node="human_review_code",
    )
    return await graph.ainvoke(None, config=config)


def _statuses(values: dict) -> dict[str, str]:
    return {s["id"]: s["status"] for s in values["subtasks"]}


def _done_transitions(fake: FakeFactory) -> list[Any]:
    """Return the ``transition_task(PARENT, "done", ...)`` calls."""
    return [
        c
        for c in fake.meshwiki.transition_task.call_args_list
        if c.args[:2] == (PARENT, "done")
    ]


# ---------------------------------------------------------------------------
# Fan-in: N parallel subtasks, all approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2, 3])
async def test_parallel_subtasks_all_approved_reach_done(count: int) -> None:
    """1, 2 and 3 parallel subtasks all reach the same completed end state."""
    subtasks = [_make_subtask(i) for i in range(1, count + 1)]
    ids = [s["id"] for s in subtasks]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": f"fan-in-{count}"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        # The run must pause at the human review gate — not end silently with
        # every subtask merged and the parent never finalized.
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {sid: "merged" for sid in ids}
    assert final_state["active_grinders"] == []
    assert fake.rounds == [sorted(ids)]  # one round, all in parallel
    assert sorted(fake.review_calls) == sorted(ids)
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_parallel_subtasks_auto_merge_skips_human_review() -> None:
    """With auto_merge the fan-in goes straight to merge_check → finalize."""
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "fan-in-auto-merge"}}

    with _patched_world(fake, auto_merge=True):
        final_state = await graph.ainvoke(_make_initial_state(subtasks), config=config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {"sub-1": "merged", "sub-2": "merged"}
    assert len(_done_transitions(fake)) == 1


# ---------------------------------------------------------------------------
# Deferred subtasks: concurrency cap and file-conflict serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [1, 2, 3])
async def test_more_subtasks_than_cap_are_all_dispatched(cap: int) -> None:
    """cap+1 subtasks: all are ground and reviewed, never more than cap per round."""
    subtasks = [_make_subtask(i) for i in range(1, cap + 2)]
    ids = [s["id"] for s in subtasks]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": f"cap-{cap}"}}
    fake.graph, fake.config = graph, config

    with _patched_world(fake, cap=cap):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {sid: "merged" for sid in ids}
    # Every subtask ground exactly once, in rounds of at most `cap`.
    assert sorted(sid for sid, _ in fake.grind_calls) == sorted(ids)
    assert len(fake.rounds) >= 2
    assert all(len(r) <= cap for r in fake.rounds), fake.rounds
    assert sorted(fake.review_calls) == sorted(ids)
    # While a grinder runs, the checkpoint lists exactly the grinders in flight.
    for active, step in zip(fake.active_seen, _steps_per_call(fake)):
        assert sorted(active) == sorted(fake.grind_steps[step])
    assert final_state["active_grinders"] == []
    assert len(_done_transitions(fake)) == 1


def _steps_per_call(fake: FakeFactory) -> list[int]:
    """Return, for each recorded grind call in order, the superstep it ran in."""
    remaining = {step: list(ids) for step, ids in fake.grind_steps.items()}
    steps: list[int] = []
    for subtask_id, _attempt in fake.grind_calls:
        for step in sorted(remaining):
            if subtask_id in remaining[step]:
                remaining[step].remove(subtask_id)
                steps.append(step)
                break
    return steps


@pytest.mark.asyncio
async def test_file_conflicting_subtasks_are_serialized_and_both_complete() -> None:
    """Two subtasks touching the same file run in separate rounds; both merge."""
    shared = ["src/meshwiki/main.py"]
    subtasks = [
        _make_subtask(1, files_touched=shared),
        _make_subtask(2, files_touched=shared),
    ]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "file-conflict"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {"sub-1": "merged", "sub-2": "merged"}
    assert fake.rounds == [["sub-1"], ["sub-2"]]
    # The second subtask only starts after the first one was reviewed.
    assert fake.events.index("review:sub-1") < fake.events.index("grind:sub-2")
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_conflict_and_cap_together() -> None:
    """Five subtasks, cap 2, two sharing a file: all complete, rules hold."""
    shared = ["src/shared.py"]
    subtasks = [
        _make_subtask(1, files_touched=shared),
        _make_subtask(2, files_touched=shared),
        _make_subtask(3),
        _make_subtask(4),
        _make_subtask(5),
    ]
    ids = [s["id"] for s in subtasks]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "conflict-and-cap"}}

    with _patched_world(fake, cap=2):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {sid: "merged" for sid in ids}
    assert sorted(sid for sid, _ in fake.grind_calls) == sorted(ids)
    assert all(len(r) <= 2 for r in fake.rounds), fake.rounds
    assert not any({"sub-1", "sub-2"} <= set(r) for r in fake.rounds), fake.rounds


# ---------------------------------------------------------------------------
# Rework and failure paths with siblings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_approval_and_changes_requested_reworks_then_completes() -> None:
    """One subtask approved, its sibling sent back once: rework runs, run completes."""
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory(reviews={"sub-2": ["changes_requested", "approved"]})
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "mixed-rework"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {"sub-1": "merged", "sub-2": "merged"}
    # sub-1 is ground and reviewed once; sub-2 twice (attempt 0, then rework).
    assert fake.grind_calls.count(("sub-1", 0)) == 1 and fake.ground("sub-1") == 1
    assert [a for sid, a in fake.grind_calls if sid == "sub-2"] == [0, 1]
    assert fake.review_calls.count("sub-1") == 1
    assert fake.review_calls.count("sub-2") == 2
    assert fake.rounds == [["sub-1", "sub-2"], ["sub-2"]]
    assert final_state["active_grinders"] == []
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_rework_budget_exhausted_escalates_from_the_join() -> None:
    """A subtask rejected on its last attempt escalates; the parent is not finalized."""
    subtasks = [_make_subtask(1), _make_subtask(2, attempt=2, review_feedback="again")]
    fake = FakeFactory(reviews={"sub-2": ["changes_requested"]})
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "rework-exhausted"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )

    assert snapshot.next == ()
    assert snapshot.values["graph_status"] == "escalated"
    assert snapshot.values["escalation_decision"] == "abandon"
    assert _statuses(snapshot.values) == {
        "sub-1": "merged",
        "sub-2": "changes_requested",
    }
    assert fake.ground("sub-2") == 1  # not re-ground after the budget ran out
    assert not _done_transitions(fake)


@pytest.mark.asyncio
async def test_join_waits_for_sibling_still_being_retried() -> None:
    """A sibling in the diagnose → re-grind loop keeps the parent from finalizing.

    ``sub-2`` fails its first grind, is diagnosed by the PM and ground again
    while ``sub-1`` has already been approved.  The join must not treat the
    transient ``failed`` status as terminal: the parent only moves on once
    ``sub-2`` has been reviewed too.

    ``sub-2`` carries ``review_feedback`` because ``grind_node`` refuses a
    retry (attempt > 0) without it.
    """
    subtasks = [_make_subtask(1), _make_subtask(2, review_feedback="earlier notes")]
    fake = FakeFactory(grinds={"sub-2": ["failed", "review"]})
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "sibling-retry"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        # Human review was only reached after sub-2's second grind was reviewed.
        assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "merged"}
        assert snapshot.values["active_grinders"] == []
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert [a for sid, a in fake.grind_calls if sid == "sub-2"] == [0, 1]
    assert fake.review_calls.count("sub-2") == 1
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_abandoned_sibling_does_not_finalize_parent() -> None:
    """A subtask whose grinder fails for good escalates; no premature finalize.

    ``sub-2`` fails, is diagnosed, and its retry fails fast (no review feedback),
    which escalates and — as escalation stands today — abandons.  ``sub-1`` was
    approved in the meantime, but the parent must not be marked done.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory(grinds={"sub-2": ["failed"]})
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "sibling-abandoned"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )

    assert snapshot.next == ()
    assert snapshot.values["graph_status"] == "escalated"
    assert snapshot.values["escalation_decision"] == "abandon"
    assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "failed"}
    assert snapshot.values["active_grinders"] == []
    assert not _done_transitions(fake)


@pytest.mark.asyncio
async def test_escalation_retry_is_dispatched_again() -> None:
    """A subtask escalate decides to retry is really ground a second time.

    ``active_grinders`` used to keep every subtask that had ever been ground,
    so the retry was filtered out at dispatch and the run ended.  The subtask
    is listed in ``failed_subtask_ids`` up front because that is what
    ``escalate_node`` bases a ``retry`` decision on.
    """
    subtasks = [_make_subtask(1, attempt=1, review_feedback="earlier notes")]
    fake = FakeFactory(grinds={"sub-1": ["failed", "review"]})
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "escalate-retry"}}
    state = _make_initial_state(subtasks)
    state["failed_subtask_ids"] = ["sub-1"]

    with _patched_world(fake):
        snapshot = await _run_until_human_review(graph, state, config)
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert final_state["escalation_decision"] == "retry"
    assert [a for _sid, a in fake.grind_calls] == [1, 2]
    assert _statuses(final_state) == {"sub-1": "merged"}
    assert final_state["active_grinders"] == []


@pytest.mark.asyncio
async def test_nothing_to_dispatch_ends_the_run_without_grinding() -> None:
    """With every subtask already terminal, assign_grinders dispatches nothing."""
    subtasks = [_make_subtask(1, status="merged"), _make_subtask(2, status="failed")]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "nothing-to-dispatch"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )

    assert snapshot.next == ()
    assert fake.grind_calls == []
    assert snapshot.values["active_grinders"] == []


@pytest.mark.asyncio
async def test_armory_validation_rework_loops_back_through_the_join() -> None:
    """A subtask rejected by the armory gate is re-ground and the run completes.

    ``validate_armory`` runs after the join.  When it rejects ``sub-2``'s PR it
    sends that subtask straight back to ``grind``; the re-ground subtask is
    reviewed, lands at the join again and passes validation the second time.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    fake.meshwiki.get_page.return_value["metadata"]["artifact_type"] = "tool"
    # Keep pm_review's own pre-merge gate out of the way: it lets the PR
    # through when it cannot fetch the files.
    fake.github.get_pr_files = AsyncMock(side_effect=RuntimeError("unavailable"))

    def _tool_file(source: str) -> list[dict]:
        return [
            {
                "filename": "python/molly/tools/my_tool.py",
                "patch": source,
                "status": "added",
            }
        ]

    async def _get_pr_files(pr_number: int) -> list[dict]:
        if pr_number == fake.pr_numbers["sub-2"][0]:
            return _tool_file("+import requests\n")  # forbidden network import
        return _tool_file("+from molly.tools.base import ToolBase\n")

    armory_github = _mock_github_client()
    armory_github.get_pr_files = AsyncMock(side_effect=_get_pr_files)
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "armory-rework"}}

    with (
        _patched_world(fake),
        patch("factory.nodes.validate_armory.GitHubClient", return_value=armory_github),
    ):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        assert snapshot.values["graph_status"] == "armory_validated"
        assert snapshot.values["active_grinders"] == []
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {"sub-1": "merged", "sub-2": "merged"}
    assert fake.ground("sub-1") == 1
    assert [a for sid, a in fake.grind_calls if sid == "sub-2"] == [0, 1]
    assert fake.review_calls.count("sub-2") == 2
    armory_github.request_changes.assert_awaited_once()
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_human_rejection_returns_to_the_human_gate() -> None:
    """A rejected code review goes back through pm_review and the join.

    The human-review interrupt behaves as it did with a single subtask: a
    rejection re-runs ``pm_review``, the result lands at the join, and the run
    pauses at ``human_review_code`` again; an approval then finishes it.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "human-reject"}}

    with _patched_world(fake):
        snapshot = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)

        await graph.aupdate_state(
            config,
            {"human_approval_response": "reject", "human_feedback": "not yet"},
            as_node="human_review_code",
        )
        await graph.ainvoke(None, config=config)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        assert len(fake.review_calls) == 3  # one subtask was reviewed again
        assert not _done_transitions(fake)

        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {"sub-1": "merged", "sub-2": "merged"}
    assert len(fake.grind_calls) == 2  # nothing was ground again
    assert len(_done_transitions(fake)) == 1


# ---------------------------------------------------------------------------
# Orchestrator restart while grinders are in flight
# ---------------------------------------------------------------------------


class _StartupWiki:
    """MeshWikiClient stand-in for ``_resume_interrupted_tasks``.

    Reports the parent task as an ``in_progress`` factory task, which is what
    the wiki holds for a task whose graph run was cut short.
    """

    def __call__(self, *_args: Any, **_kwargs: Any) -> _StartupWiki:
        return self

    async def __aenter__(self) -> _StartupWiki:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def list_tasks(self, status: str | None = None, **_kwargs: Any) -> list[dict]:
        if status != "in_progress":
            return []
        return [{"name": PARENT, "metadata": {"status": status, "assignee": "factory"}}]


async def _kill_run_mid_grind(
    graph: Any,
    state: FactoryState,
    config: dict,
    fake: FakeFactory,
    *,
    pending_grinds: int | None = None,
) -> Any:
    """Start a run, stop the orchestrator while grinders hang, return the snapshot.

    Waits until every grinder in ``fake.hang`` is blocked and — when
    *pending_grinds* is given — until the checkpoint shows exactly that many
    ``grind`` tasks without a saved result, then cancels the run the way
    ``_drain_graph_tasks`` does on shutdown.  ``fake.hang`` is cleared so the
    grinders work again after the "restart".
    """
    expected = len(fake.hang)
    run = asyncio.create_task(graph.ainvoke(state, config=config))

    async def _grinders_blocked() -> None:
        while True:
            assert not run.done(), "run ended before its grinders blocked"
            if len(fake.hung) == expected:
                snapshot = await graph.aget_state(config)
                if pending_grinds is None or snapshot.next == (
                    ("grind",) * pending_grinds
                ):
                    return
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(_grinders_blocked(), timeout=10)
    finally:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
    fake.hang.clear()
    return await graph.aget_state(config)


async def _restart_orchestrator(graph: Any, saver: MemorySaver, config: dict) -> Any:
    """Run the real startup recovery and wait for the resumed run to stop.

    Calls ``webhook_server._resume_interrupted_tasks`` exactly as the lifespan
    does (``_clear_stuck_grinders`` followed by ``ainvoke(None)`` in a
    background task) and returns the snapshot the thread ends up at.
    """
    settings = MagicMock(meshwiki_url="http://wiki", meshwiki_api_key="key")
    resumed: list[asyncio.Task] = []
    spawn = webhook_server._spawn_graph_task

    def _spawn_and_keep(*args: Any, **kwargs: Any) -> asyncio.Task:
        task = spawn(*args, **kwargs)
        resumed.append(task)
        return task

    with (
        patch.object(webhook_server, "MeshWikiClient", _StartupWiki()),
        patch.object(webhook_server, "_spawn_graph_task", _spawn_and_keep),
    ):
        await webhook_server._resume_interrupted_tasks(graph, saver, settings)
    assert len(resumed) == 1, "startup recovery did not resume the thread"
    await asyncio.gather(*resumed)
    return await graph.aget_state(config)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
async def test_restart_mid_grind_resumes_the_round(count: int) -> None:
    """A restart while the first round is grinding resumes and completes it.

    The dispatched subtasks are ``pending`` and listed in ``active_grinders``
    in the checkpoint — the normal mid-grind state.  Startup recovery must
    leave that checkpoint alone so its pending ``grind`` tasks run again.
    """
    subtasks = [_make_subtask(i) for i in range(1, count + 1)]
    ids = [s["id"] for s in subtasks]
    fake = FakeFactory()
    fake.hang = {(sid, 0) for sid in ids}
    saver = MemorySaver()
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake):
        snapshot = await _kill_run_mid_grind(
            graph, _make_initial_state(subtasks), config, fake
        )
        assert snapshot.next == ("grind",) * count
        assert snapshot.values["active_grinders"] == ids
        assert _statuses(snapshot.values) == {sid: "pending" for sid in ids}

        snapshot = await _restart_orchestrator(graph, saver, config)
        assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
        final_state = await _approve_and_finish(graph, config)

    assert final_state["graph_status"] == "completed"
    assert _statuses(final_state) == {sid: "merged" for sid in ids}
    assert final_state["active_grinders"] == []
    assert sorted(fake.grind_calls) == [(sid, 0) for sid in ids]
    assert sorted(fake.review_calls) == ids
    assert len(_done_transitions(fake)) == 1


@pytest.mark.asyncio
async def test_restart_mid_grind_keeps_results_of_finished_siblings() -> None:
    """A grinder that finished before the restart is not run a second time.

    ``sub-1``'s result is saved with the checkpoint while ``sub-2`` is still
    grinding.  After the restart only ``sub-2`` is ground; ``sub-1`` goes on to
    review with the result it already had.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    fake.hang = {("sub-2", 0)}
    saver = MemorySaver()
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake):
        snapshot = await _kill_run_mid_grind(
            graph, _make_initial_state(subtasks), config, fake, pending_grinds=1
        )
        assert fake.grind_calls == [("sub-1", 0)]
        assert snapshot.values["active_grinders"] == ["sub-1", "sub-2"]

        snapshot = await _restart_orchestrator(graph, saver, config)

    assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
    assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "merged"}
    assert snapshot.values["active_grinders"] == []
    assert sorted(fake.grind_calls) == [("sub-1", 0), ("sub-2", 0)]
    assert fake.pr_numbers["sub-1"] == [snapshot.values["subtasks"][0]["pr_number"]]
    assert sorted(fake.review_calls) == ["sub-1", "sub-2"]


@pytest.mark.asyncio
async def test_restart_during_a_later_round_resumes_it() -> None:
    """A restart while a deferred subtask is grinding does not strand it.

    With a cap of 1, ``sub-1`` is ground, reviewed and merged in round one and
    ``sub-2`` is dispatched from the join.  The restart hits round two.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    fake.hang = {("sub-2", 0)}
    saver = MemorySaver()
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake, cap=1):
        snapshot = await _kill_run_mid_grind(
            graph, _make_initial_state(subtasks), config, fake
        )
        assert snapshot.next == ("grind",)
        assert snapshot.values["active_grinders"] == ["sub-2"]
        assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "pending"}

        snapshot = await _restart_orchestrator(graph, saver, config)

    assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
    assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "merged"}
    assert snapshot.values["active_grinders"] == []
    assert fake.grind_calls == [("sub-1", 0), ("sub-2", 0)]
    assert fake.review_calls == ["sub-1", "sub-2"]


@pytest.mark.asyncio
async def test_restart_during_a_rework_grind_resumes_it() -> None:
    """A restart while a subtask is being re-ground after PM feedback resumes it.

    The subtask is ``changes_requested`` and listed in ``active_grinders`` in
    the checkpoint; it must be re-ground once, on its second attempt.
    """
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory(reviews={"sub-2": ["changes_requested"]})
    fake.hang = {("sub-2", 1)}
    saver = MemorySaver()
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake):
        snapshot = await _kill_run_mid_grind(
            graph, _make_initial_state(subtasks), config, fake
        )
        assert snapshot.next == ("grind",)
        assert snapshot.values["active_grinders"] == ["sub-2"]
        assert _statuses(snapshot.values) == {
            "sub-1": "merged",
            "sub-2": "changes_requested",
        }

        snapshot = await _restart_orchestrator(graph, saver, config)

    assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
    assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "merged"}
    assert snapshot.values["active_grinders"] == []
    assert sorted(fake.grind_calls) == [("sub-1", 0), ("sub-2", 0), ("sub-2", 1)]
    assert fake.review_calls.count("sub-2") == 2


@pytest.mark.asyncio
async def test_restart_repairs_an_idle_thread_with_a_stale_grinder() -> None:
    """An idle thread that still lists a waiting subtask as in flight is repaired.

    Checkpoints written before ``active_grinders`` was tracked at dispatch
    could end like this: nothing pending, the subtask waiting for a grinder,
    its ID left in ``active_grinders``.  Startup recovery removes the stale ID
    through the join, whose router dispatches the subtask again.
    """
    stalled = _make_initial_state(
        [_make_subtask(1, status="merged", pr_number=7), _make_subtask(2)]
    )
    stalled["active_grinders"] = ["sub-1", "sub-2"]
    fake = FakeFactory()
    saver = MemorySaver()
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake):
        # Write the stalled state as the last node of a finished run.
        await graph.aupdate_state(config, stalled, as_node="finalize")
        snapshot = await graph.aget_state(config)
        assert snapshot.next == () and not snapshot.tasks

        await webhook_server._clear_stuck_grinders(graph, config, PARENT)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("assign_grinders",)
        assert snapshot.values["active_grinders"] == ["sub-1"]

        snapshot = await _restart_orchestrator(graph, saver, config)

    assert snapshot.next == ("human_review_code",), _statuses(snapshot.values)
    assert _statuses(snapshot.values) == {"sub-1": "merged", "sub-2": "merged"}
    assert snapshot.values["active_grinders"] == []
    assert fake.grind_calls == [("sub-2", 0)]


@pytest.mark.asyncio
async def test_restart_at_the_human_gate_leaves_bookkeeping_untouched() -> None:
    """Startup recovery does not rewrite a thread parked at the human gate."""
    subtasks = [_make_subtask(1), _make_subtask(2)]
    fake = FakeFactory()
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": PARENT}}

    with _patched_world(fake):
        before = await _run_until_human_review(
            graph, _make_initial_state(subtasks), config
        )
        await webhook_server._clear_stuck_grinders(graph, config, PARENT)
        after = await graph.aget_state(config)

    assert before.next == after.next == ("human_review_code",)
    assert after.config == before.config  # same checkpoint: nothing was written


# ---------------------------------------------------------------------------
# Webhook re-entry from START (TODO F3 — not fixed here, behaviour pinned)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_merge", "payload", "stops_at"),
    [
        (
            True,
            {"human_approval_response": "changes_requested", "human_feedback": "CI"},
            (),
        ),
        (
            False,
            {"human_approval_response": "approve", "human_feedback": None},
            ("human_review_code",),
        ),
    ],
    ids=["task.rework-after-completion", "task.approved-at-human-gate"],
)
async def test_webhook_restart_from_start_grinds_again_and_lands(
    auto_merge: bool, payload: dict, stops_at: tuple[str, ...]
) -> None:
    """Pin what a ``task.rework`` / ``task.approved`` webhook does today.

    Both handlers call ``ainvoke`` with a partial state.  LangGraph treats that
    as new input, so the thread starts again at ``task_intake`` (TODO F3),
    which re-seeds the direct-grind subtask as ``pending``, attempt 0.  The
    subtask is then ground and reviewed once more per delivery and the run
    stops where it stopped before — it neither resumes past the human gate nor
    leaves anything in ``active_grinders``.

    Before ``active_grinders`` was cleaned up, the stale ID blocked this
    dispatch and left the thread idle with a ``pending`` subtask; the next
    orchestrator restart then cleared the ID and ground it anyway.  Fixing F3
    (resume instead of restart) will change this test.
    """
    fake = FakeFactory()
    fake.meshwiki.get_page.return_value["metadata"]["skip_decomposition"] = True
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": PARENT}}
    initial_state = webhook_server._build_initial_state(PARENT, PARENT, {})

    with _patched_world(fake, auto_merge=auto_merge):
        first = await _run_until_human_review(graph, initial_state, config)
        assert first.next == stops_at
        assert fake.grind_calls == [(PARENT, 0)]

        for delivery in (2, 3):
            await graph.ainvoke(payload, config=config)  # as receive_webhook does
            snapshot = await graph.aget_state(config)
            assert snapshot.next == stops_at
            assert fake.grind_calls == [(PARENT, 0)] * delivery
            assert len(fake.review_calls) == delivery
            assert _statuses(snapshot.values) == {PARENT: "merged"}
            assert snapshot.values["active_grinders"] == []
