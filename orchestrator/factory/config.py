"""Configuration for the factory orchestrator service."""

import logging
import os
import platform
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

FACTORY_MAX_CONCURRENT_SANDBOXES: int = int(
    os.getenv("FACTORY_MAX_CONCURRENT_SANDBOXES", "3")
)


def _default_checkpoint_db() -> str:
    if platform.system() == "Linux":
        return "/data/checkpoints.db"
    return str(Path.home() / ".factory" / "checkpoints.db")


class Settings(BaseSettings):
    """Settings loaded from FACTORY_* environment variables.

    With ``env_prefix="FACTORY_"`` each field maps to ``FACTORY_<FIELD_NAME>``
    (uppercased).  For example ``webhook_secret`` → ``FACTORY_WEBHOOK_SECRET``.

    The field names intentionally do *not* repeat the prefix so that the
    env-var names stay human-friendly (``FACTORY_WEBHOOK_SECRET`` not
    ``FACTORY_FACTORY_WEBHOOK_SECRET``).
    """

    webhook_secret: str = ""  # FACTORY_WEBHOOK_SECRET (from MeshWiki)
    meshwiki_url: str = "http://localhost:8000"  # FACTORY_MESHWIKI_URL
    meshwiki_api_key: str = ""  # FACTORY_MESHWIKI_API_KEY
    anthropic_api_key: str = ""  # FACTORY_ANTHROPIC_API_KEY
    minimax_api_key: str = ""  # FACTORY_MINIMAX_API_KEY
    e2b_api_key: str = ""  # FACTORY_E2B_API_KEY
    github_token: str = ""  # FACTORY_GITHUB_TOKEN (for sandbox git clone/push)
    github_repo: str = ""  # FACTORY_GITHUB_REPO e.g. "jyrkihuhta/meshwiki"
    host: str = "0.0.0.0"  # FACTORY_HOST
    port: int = 8001  # FACTORY_PORT
    log_level: str = "info"  # FACTORY_LOG_LEVEL
    repo_root: str = Field(default_factory=lambda: str(Path.cwd()))  # FACTORY_REPO_ROOT
    grinder_provider: str = "e2b"  # FACTORY_GRINDER_PROVIDER
    grinder_model: str = "MiniMax-M2.7"  # FACTORY_GRINDER_MODEL
    checkpoint_db: str = Field(
        default_factory=_default_checkpoint_db
    )  # FACTORY_CHECKPOINT_DB
    pr_base_branch: str = "main"  # FACTORY_PR_BASE_BRANCH — branch grinders target
    auto_merge: bool = (
        False  # FACTORY_AUTO_MERGE — merge PRs after PM approval, skip human review
    )
    pm_decompose_model: str = (
        "claude-sonnet-4-6"  # FACTORY_PM_DECOMPOSE_MODEL — task decomposition
    )
    pm_review_model: str = (
        "claude-sonnet-4-6"  # FACTORY_PM_REVIEW_MODEL — full review model
    )
    pm_triage_model: str = (
        "claude-haiku-4-5-20251001"  # FACTORY_PM_TRIAGE_MODEL — fast triage; empty = skip triage
    )
    pm_review_max_diff_lines: int = (
        500  # FACTORY_PM_REVIEW_MAX_DIFF_LINES — truncate diff beyond this
    )
    bookkeeper_interval_seconds: int = (
        300  # FACTORY_BOOKKEEPER_INTERVAL_SECONDS — how often the bookkeeper runs
    )
    graph_shutdown_timeout_seconds: float = (
        30.0  # FACTORY_GRAPH_SHUTDOWN_TIMEOUT_SECONDS — on shutdown, wait this
        # long for in-flight graph asyncio tasks to reach a checkpoint
        # boundary before cancelling. LangGraph writes a checkpoint after
        # every node, so a hard cancel after timeout still leaves resumable
        # state — this is purely to reduce wasted work on a planned restart.
    )
    bookkeeper_stale_hours: float = (
        2.0  # FACTORY_BOOKKEEPER_STALE_HOURS — age threshold for stuck in_progress tasks
    )
    terminal_log_max_chars: int = (
        25000  # FACTORY_TERMINAL_LOG_MAX_CHARS — max chars to persist from terminal output
    )
    terminal_review_interval_seconds: int = (
        3600  # FACTORY_TERMINAL_REVIEW_INTERVAL_SECONDS — how often the terminal review bot runs
    )
    terminal_review_batch_size: int = (
        5  # FACTORY_TERMINAL_REVIEW_BATCH_SIZE — max pages to analyze per run
    )
    terminal_review_model: str = (
        "claude-haiku-4-5-20251001"  # FACTORY_TERMINAL_REVIEW_MODEL — LLM model for analysis
    )
    scheduler_enabled: bool = (
        False  # FACTORY_SCHEDULER_ENABLED — enable autonomous backlog scheduler
    )
    scheduler_interval_seconds: int = (
        60  # FACTORY_SCHEDULER_INTERVAL_SECONDS — scheduler tick cadence
    )
    max_concurrent_parent_tasks: int = (
        3  # FACTORY_MAX_CONCURRENT_PARENT_TASKS — global cap across all repos
    )
    minimax_token_threshold: int = (
        0  # FACTORY_MINIMAX_TOKEN_THRESHOLD — pause dispatch below this remaining-token count; 0=disabled
    )
    default_repo: str = (
        ""  # FACTORY_DEFAULT_REPO — fallback repo when task has no `repo:` frontmatter field
    )
    openrouter_api_key: str = (
        ""  # FACTORY_OPENROUTER_API_KEY — fallback PM provider when Anthropic is unavailable
    )
    pm_openrouter_model: str = (
        "anthropic/claude-sonnet-4-5"  # FACTORY_PM_OPENROUTER_MODEL — model to use via OpenRouter
    )
    dry_run: bool = (
        False  # FACTORY_DRY_RUN — skip E2B sandbox and LLM calls; use short sleeps instead
    )
    dry_run_step_delay_seconds: float = (
        3.0  # FACTORY_DRY_RUN_STEP_DELAY_SECONDS — simulated delay per pipeline step
    )
    ci_fixer_enabled: bool = False  # FACTORY_CI_FIXER_ENABLED — enable the CI fixer bot
    ci_fixer_interval_seconds: int = (
        120  # FACTORY_CI_FIXER_INTERVAL_SECONDS — how often to scan for failing CI
    )
    ci_fixer_max_attempts: int = (
        2  # FACTORY_CI_FIXER_MAX_ATTEMPTS — max annotation attempts per PR
    )
    ci_fixer_model: str = (
        "claude-haiku-4-5-20251001"  # FACTORY_CI_FIXER_MODEL — LLM for failure analysis
    )
    insight_enabled: bool = (
        False  # FACTORY_INSIGHT_ENABLED — enable weekly insight/proposal bot
    )
    insight_interval_seconds: int = (
        604800  # FACTORY_INSIGHT_INTERVAL_SECONDS — weekly by default
    )
    insight_model: str = (
        "claude-haiku-4-5-20251001"  # FACTORY_INSIGHT_MODEL — LLM for synthesis
    )
    class_gap_researcher_enabled: bool = (
        False  # FACTORY_CLASS_GAP_RESEARCHER_ENABLED — enable the gap-research bot
    )
    class_gap_researcher_interval_seconds: int = (
        604800  # FACTORY_CLASS_GAP_RESEARCHER_INTERVAL_SECONDS — weekly by default
    )
    class_gap_researcher_model: str = (
        "MiniMax-M2.7"  # FACTORY_CLASS_GAP_RESEARCHER_MODEL — non-Anthropic by default
    )
    class_gap_researcher_suggestions_per_run: int = (
        3  # FACTORY_CLASS_GAP_RESEARCHER_SUGGESTIONS_PER_RUN — how many gaps per tick
    )
    daily_budget_usd: float = (
        0.0  # FACTORY_DAILY_BUDGET_USD — max USD to spend per calendar day (0 = disabled)
    )
    stale_pr_enabled: bool = (
        False  # FACTORY_STALE_PR_ENABLED — enable stale-PR fix-task bot
    )
    stale_pr_interval_seconds: int = (
        300  # FACTORY_STALE_PR_INTERVAL_SECONDS — how often the bot scans PRs
    )
    stale_pr_failure_minutes: int = (
        30  # FACTORY_STALE_PR_FAILURE_MINUTES — min age of a CI failure before acting
    )
    stale_pr_max_attempts: int = (
        2  # FACTORY_STALE_PR_MAX_ATTEMPTS — max fix tasks created per PR
    )
    molly_url: str = (
        ""  # FACTORY_MOLLY_URL — Molly HTTP API base URL, e.g. http://molly:8780
    )
    molly_api_token: str = (
        ""  # FACTORY_MOLLY_API_TOKEN — Bearer token for Molly /armory/reload
    )
    armory_repo: str = (
        ""  # FACTORY_ARMORY_REPO — molly-armory GitHub repo, e.g. jyrkihuhta/molly-armory
    )

    model_config = SettingsConfigDict(env_prefix="FACTORY_")


def validate_settings(settings: Settings) -> None:
    """Fail fast on missing required config; warn on insecure defaults.

    Call this once in the server lifespan before starting any work.

    Raises:
        RuntimeError: If any required field is missing.
    """
    missing = []
    if not settings.github_token:
        missing.append("FACTORY_GITHUB_TOKEN")
    if not settings.github_repo:
        missing.append("FACTORY_GITHUB_REPO")
    if missing:
        raise RuntimeError(
            f"Factory cannot start — required env vars not set: {', '.join(missing)}"
        )

    if not settings.webhook_secret:
        logger.warning(
            "factory: FACTORY_WEBHOOK_SECRET is empty — webhook signature "
            "verification is disabled (safe for local dev, not for production)"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
