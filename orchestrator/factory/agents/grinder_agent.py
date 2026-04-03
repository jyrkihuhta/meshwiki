"""Grinder agent: autonomous code implementer running in a git worktree."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic

from ..config import get_settings
from ..state import FactoryState, SubTask

if TYPE_CHECKING:
    from ..integrations.meshwiki_client import MeshWikiClient

logger = logging.getLogger(__name__)

GRINDER_SYSTEM_PROMPT = """
You are a software engineer working on MeshWiki. You implement tasks autonomously.

MeshWiki tech stack: FastAPI, Jinja2, HTMX, Python 3.12+, Rust (graph engine via PyO3).

Your workflow:
1. Read the task specification from MeshWiki
2. Explore the codebase to understand context
3. Create a git branch
4. Implement the changes (code + tests)
5. Run linter, fix issues
6. Run tests, fix failures
7. Commit and push
8. Create a PR
9. Update the task status

Rules:
- All new Python functions need type hints
- All new public functions need docstrings
- Tests are required for all new functionality
- Do not break existing tests
- Follow existing code patterns (read nearby files first)
- Keep changes minimal — do not refactor unrelated code
- Commit message format: "feat/fix: description"
""".strip()

GRINDER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a file at the given path relative to the repository root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the repository root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a file at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the repository root.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to the repository root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a pattern in the codebase using ripgrep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (relative to repo root). Defaults to repo root.",
                },
                "file_glob": {
                    "type": "string",
                    "description": "File glob to filter search (e.g. '*.py').",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "git_create_branch",
        "description": "Create and checkout a new branch from origin/main.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": "Name of the branch to create.",
                },
            },
            "required": ["branch_name"],
        },
    },
    {
        "name": "git_commit",
        "description": "Stage specified files and create a commit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths (relative to repo root) to stage.",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message.",
                },
            },
            "required": ["files", "message"],
        },
    },
    {
        "name": "git_push",
        "description": "Push the current branch to origin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch_name": {
                    "type": "string",
                    "description": "Branch name to push.",
                },
            },
            "required": ["branch_name"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest and return the last 100 lines of output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "Test path to run (relative to repo root). Defaults to 'src/tests/'.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_lint",
        "description": "Run black --check and ruff check on src/. Returns linter output.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "run_autofix",
        "description": "Auto-format code: run black, isort, and ruff --fix on src/.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_pr",
        "description": "Create a GitHub pull request and return the PR URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Pull request title.",
                },
                "body": {
                    "type": "string",
                    "description": "Pull request body / description.",
                },
                "branch_name": {
                    "type": "string",
                    "description": "Branch name for the PR.",
                },
            },
            "required": ["title", "body", "branch_name"],
        },
    },
    {
        "name": "meshwiki_update_task",
        "description": "Transition a MeshWiki task page to a new status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_name": {
                    "type": "string",
                    "description": "MeshWiki page name of the task.",
                },
                "status": {
                    "type": "string",
                    "description": "Target status to transition to.",
                },
                "extra_fields": {
                    "type": "object",
                    "description": "Additional frontmatter fields to update.",
                },
            },
            "required": ["page_name", "status"],
        },
    },
]


class GrinderToolExecutor:
    """Executes Grinder tool calls locally in the repository working tree.

    Args:
        repo_root: Absolute path to the repository root directory.
        meshwiki_client: Async client for the MeshWiki JSON API.
    """

    def __init__(self, repo_root: Path, meshwiki_client: "MeshWikiClient") -> None:
        self.repo_root = repo_root
        self.meshwiki_client = meshwiki_client

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string.

        Args:
            tool_name: Name of the tool to execute.
            tool_input: Input parameters for the tool.

        Returns:
            Tool result as a human-readable string.
        """
        try:
            if tool_name == "read_file":
                return self._read_file(tool_input["path"])
            elif tool_name == "write_file":
                return self._write_file(tool_input["path"], tool_input["content"])
            elif tool_name == "list_directory":
                return self._list_directory(tool_input["path"])
            elif tool_name == "search_code":
                return self._search_code(
                    tool_input["pattern"],
                    tool_input.get("path"),
                    tool_input.get("file_glob"),
                )
            elif tool_name == "git_create_branch":
                return self._git_create_branch(tool_input["branch_name"])
            elif tool_name == "git_commit":
                return self._git_commit(tool_input["files"], tool_input["message"])
            elif tool_name == "git_push":
                return self._git_push(tool_input["branch_name"])
            elif tool_name == "run_tests":
                return self._run_tests(tool_input.get("test_path"))
            elif tool_name == "run_lint":
                return self._run_lint()
            elif tool_name == "run_autofix":
                return self._run_autofix()
            elif tool_name == "create_pr":
                return self._create_pr(
                    tool_input["title"],
                    tool_input["body"],
                    tool_input["branch_name"],
                )
            elif tool_name == "meshwiki_update_task":
                return await self._meshwiki_update_task(
                    tool_input["page_name"],
                    tool_input["status"],
                    tool_input.get("extra_fields"),
                )
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as exc:
            logger.exception("GrinderToolExecutor: error in tool %s", tool_name)
            return f"Error executing {tool_name}: {exc}"

    def _read_file(self, path: str) -> str:
        """Read a file relative to repo root."""
        full_path = self.repo_root / path
        if not full_path.exists():
            return "File not found"
        return full_path.read_text(encoding="utf-8")

    def _write_file(self, path: str, content: str) -> str:
        """Write content to a file, creating parent directories as needed."""
        full_path = self.repo_root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return f"Written: {path}"

    def _list_directory(self, path: str) -> str:
        """List directory contents."""
        full_path = self.repo_root / path
        if not full_path.exists():
            return "Directory not found"
        entries = os.listdir(full_path)
        return "\n".join(sorted(entries))

    def _search_code(
        self,
        pattern: str,
        path: str | None = None,
        file_glob: str | None = None,
    ) -> str:
        """Search code using ripgrep."""
        search_path = str(self.repo_root / path) if path else str(self.repo_root)
        cmd = ["rg", pattern, search_path]
        if file_glob:
            cmd += ["--glob", file_glob]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout or "No matches"

    def _git_create_branch(self, branch_name: str) -> str:
        """Create and checkout a new branch from origin/main."""
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name, "origin/main"],
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr}"
        return f"Branch created: {branch_name}"

    def _git_commit(self, files: list[str], message: str) -> str:
        """Stage specified files and commit."""
        add_result = subprocess.run(
            ["git", "add"] + files,
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        if add_result.returncode != 0:
            return f"Error staging files: {add_result.stderr}"
        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        if commit_result.returncode != 0:
            return f"Error committing: {commit_result.stderr}"
        return commit_result.stdout

    def _git_push(self, branch_name: str) -> str:
        """Push the branch to origin."""
        result = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr}"
        return result.stdout or f"Pushed: {branch_name}"

    def _run_tests(self, test_path: str | None = None) -> str:
        """Run pytest and return the last 100 lines of output."""
        path = test_path or "src/tests/"
        result = subprocess.run(
            ["python", "-m", "pytest", path, "-v"],
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        output = result.stdout + result.stderr
        lines = output.splitlines()
        return "\n".join(lines[-100:])

    def _run_lint(self) -> str:
        """Run ruff check and black --check on src/."""
        venv_bin = self.repo_root / ".venv" / "bin"
        ruff = str(venv_bin / "ruff")
        black = str(venv_bin / "black")
        result = subprocess.run(
            f"{ruff} check src/ && {black} --check src/",
            shell=True,
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        return result.stdout + result.stderr

    def _run_autofix(self) -> str:
        """Run black, isort, and ruff --fix on src/."""
        venv_bin = self.repo_root / ".venv" / "bin"
        black = str(venv_bin / "black")
        isort = str(venv_bin / "isort")
        ruff = str(venv_bin / "ruff")
        result = subprocess.run(
            f"{black} src/ && {isort} --profile black src/ && {ruff} --fix src/",
            shell=True,
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        return result.stdout + result.stderr

    def _create_pr(self, title: str, body: str, branch_name: str) -> str:
        """Create a GitHub pull request and return the PR URL."""
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body],
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        if result.returncode != 0:
            return f"Error: {result.stderr}"
        return result.stdout.strip()

    async def _meshwiki_update_task(
        self,
        page_name: str,
        status: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> str:
        """Transition a MeshWiki task to a new status."""
        await self.meshwiki_client.transition_task(page_name, status, extra_fields)
        return f"Task {page_name} transitioned to {status}"


async def grind_subtask_e2b(
    state: FactoryState,
    subtask: SubTask,
    meshwiki_client: "MeshWikiClient",
) -> SubTask:
    """Run grinder using E2B sandbox + Kilo CLI with MiniMax.

    Args:
        state: Current FactoryState with parent task context.
        subtask: The SubTask to implement.
        meshwiki_client: Async HTTP client for MeshWiki.

    Returns:
        Updated SubTask with pr_url, branch_name, and status set.
    """
    import os

    from e2b_code_interpreter import Sandbox

    settings = get_settings()
    subtask = dict(subtask)

    # Transition to in_progress
    try:
        await meshwiki_client.transition_task(subtask["wiki_page"], "in_progress")
    except Exception as exc:
        logger.error(
            "e2b grinder: failed to transition %s to in_progress: %s",
            subtask["wiki_page"],
            exc,
        )

    # Fetch task description
    page_content = ""
    try:
        page = await meshwiki_client.get_page(subtask["wiki_page"])
        if page:
            page_content = page.get("content", "")
    except Exception as exc:
        logger.error("e2b grinder: failed to fetch wiki page: %s", exc)

    task_prompt = (
        f"You are working on the MeshWiki project (FastAPI + Python 3.12 + Rust graph engine). "
        f"Implement the following task and open a GitHub PR when done.\n\n"
        f"## Task: {subtask['title']}\n\n"
        f"{page_content}\n\n"
        f"## Instructions\n"
        f"1. Explore the codebase to understand context\n"
        f"2. Create a branch: factory/{subtask['id']}\n"
        f"3. Implement the changes with tests\n"
        f"4. Run: .venv/bin/black src/ && .venv/bin/isort --profile black src/ && .venv/bin/ruff check src/\n"
        f"5. Run: python -m pytest src/tests/ -x -q\n"
        f"6. Fix any lint/test failures\n"
        f"7. Commit and push the branch\n"
        f"8. Create a PR with: gh pr create --title '...' --body '...'\n"
        f"9. Print the PR URL on the last line of your output"
    )

    pr_url: str | None = None
    branch_name = f"factory/{subtask['id']}"
    status = "failed"

    # Expose E2B_API_KEY so Sandbox.create() picks it up from the environment
    os.environ["E2B_API_KEY"] = settings.e2b_api_key

    # Model string: Kilo expects "provider/model" format
    model_arg = f"minimax/{settings.grinder_model}"

    try:
        with Sandbox.create(
            envs={
                "MINIMAX_API_KEY": settings.minimax_api_key,
                # KILO_API_KEY is what Kilo reads for the minimax provider
                "KILO_API_KEY": settings.minimax_api_key,
                "GITHUB_TOKEN": settings.github_token,
                "GH_TOKEN": settings.github_token,
            }
        ) as sbx:
            logger.info("e2b grinder: sandbox created for subtask %s", subtask["id"])

            # Configure git identity and credential helper
            sbx.commands.run(
                'git config --global user.email "factory@meshwiki" && '
                'git config --global user.name "Factory Grinder" && '
                f'git config --global url."https://x-access-token:{settings.github_token}@github.com/".insteadOf "https://github.com/"',
                timeout=0,
            )

            # Bootstrap Node.js 20 + Kilo CLI (timeout=0 prevents premature kill)
            logger.info("e2b grinder: bootstrapping Node.js + Kilo CLI...")
            sbx.commands.run(
                "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && "
                "sudo apt-get install -y nodejs && "
                "sudo npm install -g @kilocode/cli",
                timeout=0,
            )

            # Clone repo
            repo = settings.github_repo
            clone_url = f"https://x-access-token:{settings.github_token}@github.com/{repo}.git"
            result = sbx.commands.run(
                f"git clone {clone_url} /tmp/repo",
                timeout=0,
            )
            if result.exit_code != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")

            # Install Python deps
            sbx.commands.run(
                "cd /tmp/repo && pip install -e '.[dev]' -q",
                timeout=0,
            )

            # Write task file and run Kilo
            sbx.files.write("/tmp/task.md", task_prompt)

            logger.info(
                "e2b grinder: running Kilo (model=%s) for subtask %s...",
                model_arg,
                subtask["id"],
            )
            result = sbx.commands.run(
                f'cd /tmp/repo && kilo run --auto --model {model_arg} "$(cat /tmp/task.md)"',
                timeout=0,
            )

            output = (result.stdout or "") + (result.stderr or "")
            logger.info("e2b grinder output tail: %s", output[-2000:])

            # Extract PR URL — search anywhere in output (Kilo may print raw JSON)
            import re as _re

            match = _re.search(
                r'https://github\.com/[^/\s"]+/[^/\s"]+/pull/\d+', output
            )
            if match:
                pr_url = match.group(0)

            if pr_url:
                status = "review"
                logger.info("e2b grinder: PR created %s", pr_url)
            else:
                logger.warning("e2b grinder: no PR URL found in output")

    except Exception as exc:
        logger.exception("e2b grinder: sandbox error: %s", exc)

    subtask.update(
        {
            "status": status,
            "branch_name": branch_name,
            "pr_url": pr_url,
        }
    )
    return subtask  # type: ignore[return-value]


async def grind_subtask(
    state: FactoryState,
    subtask: SubTask,
    meshwiki_client: "MeshWikiClient",
) -> SubTask:
    """Run the grinder agentic loop to implement a single subtask.

    1. Transitions the subtask to 'in_progress' via MeshWiki.
    2. Builds an initial message with the task specification.
    3. Runs an agentic loop (max ``token_budget // 1000`` iterations, capped at 50).
    4. Checks if a PR was created and updates the subtask accordingly.

    Args:
        state: Current FactoryState with parent task context.
        subtask: The SubTask to implement.
        meshwiki_client: Async HTTP client for MeshWiki.

    Returns:
        Updated SubTask with pr_url, branch_name, tokens_used, and status set.
    """
    settings = get_settings()

    if settings.grinder_provider == "e2b":
        return await grind_subtask_e2b(state, subtask, meshwiki_client)

    if settings.grinder_provider == "minimax":
        # MiniMax supports the Anthropic Messages API format at a different base URL.
        client = anthropic.AsyncAnthropic(
            api_key=settings.minimax_api_key or None,
            base_url="https://api.minimax.io/v1",
        )
    elif settings.grinder_provider == "anthropic":
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    else:
        logger.warning(
            "grind_subtask: unknown grinder_provider %r, falling back to anthropic",
            settings.grinder_provider,
        )
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)

    executor = GrinderToolExecutor(
        repo_root=Path(settings.repo_root),
        meshwiki_client=meshwiki_client,
    )

    # Transition subtask to in_progress
    try:
        await meshwiki_client.transition_task(subtask["wiki_page"], "in_progress")
        logger.info(
            "grind_subtask: transitioned %s to in_progress", subtask["wiki_page"]
        )
    except Exception as exc:
        logger.error(
            "grind_subtask: failed to transition %s to in_progress: %s",
            subtask["wiki_page"],
            exc,
        )

    # Fetch subtask wiki page for context
    page_content = ""
    try:
        page = await meshwiki_client.get_page(subtask["wiki_page"])
        if page:
            page_content = page.get("content", "")
    except Exception as exc:
        logger.error(
            "grind_subtask: failed to fetch wiki page %s: %s",
            subtask["wiki_page"],
            exc,
        )

    # Build initial user message
    parent_context = (
        f"Parent task: {state.get('task_wiki_page', '')}\n"
        f"Parent requirements: {state.get('requirements', '')[:2000]}"
    )
    user_message = (
        f"## Subtask: {subtask['title']}\n\n"
        f"**Subtask ID:** {subtask['id']}\n"
        f"**Wiki page:** {subtask['wiki_page']}\n\n"
        f"## Task Specification\n\n{page_content}\n\n"
        f"## Parent Task Context\n\n{parent_context}\n\n"
        "Please implement this subtask following the workflow described in your system prompt. "
        "Create a branch, implement the code with tests, run linting and tests, then open a PR. "
        "Finally, call `meshwiki_update_task` to update the task status."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    max_tool_calls = min(subtask["token_budget"] // 1000, 50)
    tool_calls_remaining = max_tool_calls
    tokens_used = 0
    pr_url: str | None = None
    branch_name: str | None = subtask.get("branch_name")

    logger.info(
        "grind_subtask: starting agentic loop for %s (max %d tool calls)",
        subtask["id"],
        max_tool_calls,
    )

    while tool_calls_remaining > 0:
        response = await client.messages.create(
            model=settings.grinder_model,
            max_tokens=4096,
            system=GRINDER_SYSTEM_PROMPT,
            tools=GRINDER_TOOLS,
            messages=messages,
        )

        # Track token usage
        if hasattr(response, "usage") and response.usage:
            tokens_used += getattr(response.usage, "input_tokens", 0)
            tokens_used += getattr(response.usage, "output_tokens", 0)

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            logger.info(
                "grind_subtask: end_turn for %s after %d tool calls",
                subtask["id"],
                max_tool_calls - tool_calls_remaining,
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

            logger.debug(
                "grind_subtask: executing tool %s for subtask %s",
                tool_name,
                subtask["id"],
            )

            result_str = await executor.execute(tool_name, tool_input)

            # Track branch name from git_create_branch
            if tool_name == "git_create_branch" and "Branch created:" in result_str:
                branch_name = tool_input.get("branch_name")

            # Detect PR URL from create_pr result
            if tool_name == "create_pr" and result_str.startswith("http"):
                pr_url = result_str

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                }
            )

        if not has_tool_use:
            break

        messages.append({"role": "user", "content": tool_results})

        if tool_calls_remaining <= 0:
            logger.warning(
                "grind_subtask: tool call budget exhausted for %s", subtask["id"]
            )
            break

    # Determine final status
    if pr_url:
        final_status: str = "review"
    else:
        final_status = "failed"
        logger.warning(
            "grind_subtask: no PR created for subtask %s — marking failed",
            subtask["id"],
        )

    updated: SubTask = SubTask(
        **{
            **subtask,
            "pr_url": pr_url,
            "branch_name": branch_name,
            "tokens_used": tokens_used,
            "status": final_status,
        }
    )
    return updated
