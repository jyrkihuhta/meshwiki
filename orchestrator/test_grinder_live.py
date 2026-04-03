"""Live grinder smoke test — runs a real E2B sandbox with Kilo + MiniMax.

Usage:
    cd orchestrator
    python test_grinder_live.py
"""

import asyncio
import logging
from unittest.mock import AsyncMock

from dotenv import load_dotenv

load_dotenv()  # reads orchestrator/.env

from factory.agents.grinder_agent import grind_subtask_e2b  # noqa: E402
from factory.state import FactoryState, SubTask  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


TASK = """\
Add a `slugify(text: str) -> str` utility function to `src/meshwiki/utils.py`
(create the file if it does not exist).

The function should:
- Lowercase the input
- Replace spaces and underscores with hyphens
- Strip any character that is not alphanumeric or a hyphen
- Collapse multiple consecutive hyphens into one
- Strip leading/trailing hyphens

Example: `slugify("Hello World!")` → `"hello-world"`

Also add `src/tests/test_utils.py` with at least 5 pytest test cases covering
normal input, empty string, special characters, and leading/trailing hyphens.

Follow the existing code style (black, isort, ruff). Run the tests and fix any
failures before opening the PR.
"""

state = FactoryState(
    thread_id="smoke-test-001",
    task_wiki_page="Task_smoke_test",
    title="Add slugify utility",
    requirements=TASK,
    subtasks=[],
    decomposition_approved=True,
    active_grinders={},
    completed_subtask_ids=[],
    failed_subtask_ids=[],
    pm_messages=[],
    human_approval_response=None,
    human_feedback=None,
    cost_usd=0.0,
    graph_status="grinding",
    error=None,
    escalation_decision=None,
)

subtask = SubTask(
    id="smoke-test-001",
    wiki_page="Task_smoke_test",
    title="Add slugify utility",
    description=TASK,
    status="pending",
    assigned_grinder=None,
    branch_name=None,
    pr_url=None,
    pr_number=None,
    attempt=0,
    max_attempts=3,
    error_log=[],
    files_touched=["src/meshwiki/utils.py", "src/tests/test_utils.py"],
    token_budget=50000,
    tokens_used=0,
    review_feedback=None,
)

# Mock MeshWiki — not needed for grinder-only test
meshwiki = AsyncMock()
meshwiki.transition_task = AsyncMock(return_value={})
meshwiki.get_page = AsyncMock(return_value={"content": TASK})


async def main() -> None:
    print("Starting grinder smoke test...")
    print(f"Task: {subtask['title']}")
    print("Model: MiniMax-M2.7 via Kilo in E2B sandbox")
    print("-" * 60)

    result = await grind_subtask_e2b(state, subtask, meshwiki)

    print("-" * 60)
    print(f"Status:  {result['status']}")
    print(f"PR URL:  {result.get('pr_url') or '(none)'}")
    print(f"Branch:  {result.get('branch_name') or '(none)'}")


asyncio.run(main())
