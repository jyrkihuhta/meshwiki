"""Configuration for the factory orchestrator service."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

FACTORY_MAX_CONCURRENT_SANDBOXES: int = int(
    os.getenv("FACTORY_MAX_CONCURRENT_SANDBOXES", "3")
)


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
    repo_root: str = "/Users/jhuhta/meshwiki"  # FACTORY_REPO_ROOT
    grinder_provider: str = "e2b"  # FACTORY_GRINDER_PROVIDER
    grinder_model: str = "MiniMax-M2.7"  # FACTORY_GRINDER_MODEL
    checkpoint_db: str = "/data/checkpoints.db"  # FACTORY_CHECKPOINT_DB
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

    model_config = SettingsConfigDict(env_prefix="FACTORY_")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
