"""Application configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    data_dir: Path = Path("data/pages")
    debug: bool = False
    app_title: str = "MeshWiki"
    graph_watch: bool = True
    auth_enabled: bool = False
    auth_password: str = ""
    session_secret: str = "dev-secret-change-in-production"

    model_config = SettingsConfigDict(
        env_prefix="MESHWIKI_",
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
