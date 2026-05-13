"""Terminal Review bot: analyzes grinder terminal logs for improvement opportunities.

On each tick the bot:
1. Lists all task wiki pages with status ``done`` or ``merged``.
2. Finds pages that have a ``## Terminal Log`` section but no
   ``## Grinder Observations`` section (i.e. not yet analyzed).
3. Calls the Anthropic API (Haiku by default — cheap) with a focused
   prompt to identify recurring failures, slow steps, and prompt
   improvement suggestions.
4. Appends a ``## Grinder Observations`` section with the findings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from ..agents.pm_agent import safe_messages_create
from ..config import get_settings
from ..integrations.meshwiki_client import MeshWikiClient
from .base import BaseBot, BotResult

logger = logging.getLogger(__name__)

_TERMINAL_LOG_MARKER = "## Terminal Log"
_OBSERVATIONS_MARKER = "## Grinder Observations"

_ANALYSIS_PROMPT_TEMPLATE = """You are analyzing a grinder agent's terminal output to find improvement opportunities.
Identify (be brief, bullet points only):
- Commands that failed and had to be retried
- Lint/test failures and whether the agent fixed them correctly
- Any signs of confusion (wrong tool, repeated same mistake)
- Bootstrap steps that seem slow or unnecessary
- One concrete suggestion to improve future task prompts

Terminal log:
{log_text}"""


def _extract_terminal_log(content: str) -> str | None:
    """Extract the raw terminal log text from a wiki page's Markdown content.

    Looks for the ``## Terminal Log`` section and extracts the text inside
    the fenced code block within the ``<details>`` block.

    Args:
        content: Full Markdown content of the wiki page.

    Returns:
        The terminal log text, or ``None`` if not found.
    """
    start = content.find(_TERMINAL_LOG_MARKER)
    if start == -1:
        return None

    section = content[start:]
    # Find the opening fence of the code block
    fence_start = section.find("```\n")
    if fence_start == -1:
        return None
    fence_end = section.find("\n```", fence_start + 4)
    if fence_end == -1:
        return None
    return section[fence_start + 4 : fence_end]


class TerminalReviewBot(BaseBot):
    """Periodic bot that analyzes grinder terminal logs for improvement opportunities.

    Reads ``FACTORY_TERMINAL_REVIEW_INTERVAL_SECONDS``,
    ``FACTORY_TERMINAL_REVIEW_BATCH_SIZE``, and
    ``FACTORY_TERMINAL_REVIEW_MODEL`` from the environment (via pydantic
    settings) at construction time.
    """

    name = "terminal-review"
    pauses_on_anthropic_block = True

    def __init__(
        self,
        interval_seconds: int | None = None,
        batch_size: int | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__()
        settings = get_settings()
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else settings.terminal_review_interval_seconds
        )
        self._batch_size = (
            batch_size
            if batch_size is not None
            else settings.terminal_review_batch_size
        )
        self._model = model if model is not None else settings.terminal_review_model

    async def run(self) -> BotResult:
        """Execute one terminal review cycle.

        Returns:
            A :class:`BotResult` with ``actions_taken`` equal to the number
            of pages successfully analyzed.
        """
        started = time.monotonic()
        actions = 0
        errors: list[str] = []

        settings = get_settings()
        anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=60.0,
        )

        async with MeshWikiClient() as wiki:
            unanalyzed = await self._find_unanalyzed_pages(wiki, errors)
            for page_info in unanalyzed[: self._batch_size]:
                page_name: str = page_info["name"]
                log_text: str = page_info["log_text"]
                try:
                    observations = await self._analyze_log(
                        anthropic_client, log_text, page_name
                    )
                    await self._append_observations(wiki, page_name, observations)
                    actions += 1
                    logger.info(
                        "terminal-review: analyzed terminal log for %s", page_name
                    )
                except Exception as exc:
                    err = f"failed to analyze {page_name}: {exc}"
                    logger.warning("terminal-review: %s", err)
                    errors.append(err)

        elapsed = time.monotonic() - started
        return BotResult(
            ran_at=started,
            actions_taken=actions,
            errors=errors,
            details=f"batch_size={self._batch_size} elapsed={elapsed:.2f}s",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _find_unanalyzed_pages(
        self,
        wiki: MeshWikiClient,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        """Find pages with a terminal log that have not yet been analyzed.

        Args:
            wiki: Open :class:`MeshWikiClient` instance.
            errors: List to append error messages to (mutated in place).

        Returns:
            List of dicts with ``name`` and ``log_text`` keys, one per
            unanalyzed page.
        """
        candidates: list[dict[str, Any]] = []

        for status in ("done", "merged"):
            try:
                tasks = await wiki.list_tasks(status=status)
            except Exception as exc:
                errors.append(f"list_tasks({status!r}) failed: {exc}")
                continue

            for task in tasks:
                page_name: str = task.get("name", "")
                if not page_name:
                    continue

                try:
                    page = await wiki.get_page(page_name)
                except Exception as exc:
                    errors.append(f"get_page({page_name!r}) failed: {exc}")
                    continue

                if page is None:
                    continue

                content: str = page.get("content", "")

                if _TERMINAL_LOG_MARKER not in content:
                    continue
                if _OBSERVATIONS_MARKER in content:
                    # Already analyzed — skip.
                    continue

                log_text = _extract_terminal_log(content)
                if not log_text or not log_text.strip():
                    continue

                candidates.append({"name": page_name, "log_text": log_text})

        return candidates

    async def _analyze_log(
        self,
        client: anthropic.AsyncAnthropic,
        log_text: str,
        page_name: str,
    ) -> str:
        """Call the Anthropic API to analyze a terminal log.

        Args:
            client: Async Anthropic API client.
            log_text: Plain-text terminal output to analyze.
            page_name: Page name (used for logging only).

        Returns:
            The LLM's analysis as a Markdown string.
        """
        prompt = _ANALYSIS_PROMPT_TEMPLATE.format(log_text=log_text)
        logger.debug(
            "terminal-review: calling %s for page %s (%d chars)",
            self._model,
            page_name,
            len(log_text),
        )
        response = await safe_messages_create(
            client,
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract the text from the first content block.
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return "(no analysis returned)"

    async def _append_observations(
        self,
        wiki: MeshWikiClient,
        page_name: str,
        observations: str,
    ) -> None:
        """Append a ``## Grinder Observations`` section to the wiki page.

        Args:
            wiki: Open :class:`MeshWikiClient` instance.
            page_name: Name of the wiki page to append to.
            observations: Markdown text from the LLM analysis.
        """
        section = f"## Grinder Observations\n\n{observations.strip()}\n"
        await wiki.append_to_page(page_name, section)
