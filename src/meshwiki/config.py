"""Application configuration."""

import secrets
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Former default of ``session_secret``. It is public (it is in the git history),
# so it is never accepted as a signing key.
DEV_SESSION_SECRET = "dev-secret-change-in-production"

# Shortest session signing key accepted when authentication is enabled.
MIN_SESSION_SECRET_LENGTH = 32

# Passwords the env examples in this repository used to ship. They are public,
# so a deployment still using one of them is treated as having no password.
PLACEHOLDER_PASSWORDS = frozenset({"changeme", "staging"})

_GENERATE_SECRET_HINT = (
    'generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    When ``auth_enabled`` is true the credentials are validated at construction
    and a ``pydantic.ValidationError`` is raised if they are unusable, so the
    app refuses to start rather than run with authentication that fails open.
    """

    data_dir: Path = Path("data/pages")
    debug: bool = False
    app_title: str = "MeshWiki"
    graph_watch: bool = True
    auth_enabled: bool = False
    auth_password: str = ""
    # Session cookie signing key. Required (>= MIN_SESSION_SECRET_LENGTH chars)
    # when auth is enabled; otherwise a random per-process key is generated.
    session_secret: str = ""

    # Version history
    history_enabled: bool = True

    # Agent factory
    factory_enabled: bool = False
    factory_api_key: str = ""
    factory_webhook_url: str = ""
    factory_webhook_secret: str = ""
    github_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_prefix="MESHWIKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Validation errors end up in startup logs; never echo configured
        # values (passwords, signing keys, API keys) into them.
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def _require_usable_auth_credentials(self) -> Self:
        """Fail closed on missing, default or weak authentication secrets.

        With auth enabled, an empty password would let an empty login through
        and a blank, default or short session secret would let anyone forge a
        session cookie, so each of those is a startup error. With auth disabled
        nothing is required, but a missing (or publicly known) secret is
        replaced with a random one so the session cookie is never signed with
        a key an outsider knows.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If auth is enabled and the password or session secret
                is unusable. The message names the environment variables to
                fix and never contains their values.
        """
        if not self.auth_enabled:
            if (
                not self.session_secret.strip()
                or self.session_secret == DEV_SESSION_SECRET
            ):
                self.session_secret = secrets.token_urlsafe(48)
            return self

        problems: list[str] = []

        if not self.auth_password.strip():
            problems.append("MESHWIKI_AUTH_PASSWORD must be set to a non-empty value")
        elif self.auth_password in PLACEHOLDER_PASSWORDS:
            problems.append(
                "MESHWIKI_AUTH_PASSWORD is still an example placeholder; "
                "set a real password"
            )

        if not self.session_secret.strip():
            problems.append(
                f"MESHWIKI_SESSION_SECRET must be set ({_GENERATE_SECRET_HINT})"
            )
        elif self.session_secret == DEV_SESSION_SECRET:
            problems.append(
                "MESHWIKI_SESSION_SECRET must not be the built-in development "
                f"default ({_GENERATE_SECRET_HINT})"
            )
        elif len(self.session_secret) < MIN_SESSION_SECRET_LENGTH:
            problems.append(
                "MESHWIKI_SESSION_SECRET must be at least "
                f"{MIN_SESSION_SECRET_LENGTH} characters ({_GENERATE_SECRET_HINT})"
            )

        if problems:
            raise ValueError(
                "MESHWIKI_AUTH_ENABLED=true but authentication is not safely "
                "configured, refusing to start: " + "; ".join(problems)
            )
        return self


settings = Settings()
