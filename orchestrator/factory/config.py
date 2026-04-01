"""Configuration for the factory orchestrator service."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    host: str = "0.0.0.0"  # FACTORY_HOST
    port: int = 8001  # FACTORY_PORT
    log_level: str = "info"  # FACTORY_LOG_LEVEL

    model_config = SettingsConfigDict(env_prefix="FACTORY_")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
