"""Unit tests for application configuration."""

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from meshwiki.config import (
    DEV_SESSION_SECRET,
    MIN_SESSION_SECRET_LENGTH,
    PLACEHOLDER_PASSWORDS,
    Settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLES = [
    REPO_ROOT / "deploy" / "vps" / ".env.example",
    REPO_ROOT / "deploy" / "vps" / "staging.env.example",
]

GOOD_SECRET = "k" * MIN_SESSION_SECRET_LENGTH
GOOD_PASSWORD = "8charpwd"


class TestSettings:
    def test_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings()
            assert s.data_dir == Path("data/pages")
            assert s.debug is False
            assert s.app_title == "MeshWiki"
            assert s.graph_watch is True

    def test_from_env(self):
        env = {
            "MESHWIKI_DATA_DIR": "/tmp/wiki",
            "MESHWIKI_DEBUG": "true",
            "MESHWIKI_APP_TITLE": "MyWiki",
            "MESHWIKI_GRAPH_WATCH": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            s = Settings()
            assert s.data_dir == Path("/tmp/wiki")
            assert s.debug is True
            assert s.app_title == "MyWiki"
            assert s.graph_watch is False

    def test_debug_false_values(self):
        with patch.dict("os.environ", {"MESHWIKI_DEBUG": "false"}, clear=True):
            s = Settings()
            assert s.debug is False

    def test_graph_watch_true_default(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings()
            assert s.graph_watch is True


def _auth_settings(**overrides: str) -> Settings:
    """Build auth-enabled Settings from keyword arguments only (no env, no .env)."""
    kwargs = {
        "auth_enabled": True,
        "auth_password": GOOD_PASSWORD,
        "session_secret": GOOD_SECRET,
    }
    kwargs.update(overrides)
    with patch.dict("os.environ", {}, clear=True):
        return Settings(_env_file=None, **kwargs)


class TestAuthStartupValidation:
    """Auth must fail closed: unusable credentials stop the app from starting."""

    def test_accepts_real_password_and_long_secret(self):
        s = _auth_settings()
        assert s.auth_enabled is True
        assert s.auth_password == GOOD_PASSWORD
        assert s.session_secret == GOOD_SECRET

    def test_accepts_eight_character_password_with_64_char_secret(self):
        """The shape of the live deployments' credentials must keep working."""
        assert len(GOOD_PASSWORD) == 8
        s = _auth_settings(session_secret="a1" * 32)
        assert s.auth_password == GOOD_PASSWORD
        assert len(s.session_secret) == 64

    def test_accepts_short_password(self):
        """There is deliberately no minimum password length."""
        assert _auth_settings(auth_password="x").auth_password == "x"

    def test_accepts_secret_of_exactly_minimum_length(self):
        assert len(GOOD_SECRET) == MIN_SESSION_SECRET_LENGTH
        assert _auth_settings().session_secret == GOOD_SECRET

    @pytest.mark.parametrize("password", ["", "   ", "\t\n"])
    def test_rejects_missing_password(self, password):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(auth_password=password)
        assert "MESHWIKI_AUTH_PASSWORD" in str(exc.value)

    def test_rejects_unset_password(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValidationError) as exc:
                Settings(_env_file=None, auth_enabled=True, session_secret=GOOD_SECRET)
        message = str(exc.value)
        assert "MESHWIKI_AUTH_PASSWORD" in message
        assert "MESHWIKI_SESSION_SECRET" not in message

    @pytest.mark.parametrize("password", sorted(PLACEHOLDER_PASSWORDS))
    def test_rejects_example_file_passwords(self, password):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(auth_password=password)
        assert "MESHWIKI_AUTH_PASSWORD" in str(exc.value)

    def test_placeholder_passwords_are_the_ones_the_examples_shipped(self):
        assert PLACEHOLDER_PASSWORDS == {"changeme", "staging"}

    def test_rejects_unset_secret(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValidationError) as exc:
                Settings(_env_file=None, auth_enabled=True, auth_password=GOOD_PASSWORD)
        message = str(exc.value)
        assert "MESHWIKI_SESSION_SECRET" in message
        assert "MESHWIKI_AUTH_PASSWORD" not in message

    @pytest.mark.parametrize("secret", ["", " " * 40])
    def test_rejects_blank_secret(self, secret):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(session_secret=secret)
        assert "MESHWIKI_SESSION_SECRET" in str(exc.value)

    def test_rejects_dev_default_secret(self):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(session_secret=DEV_SESSION_SECRET)
        assert "MESHWIKI_SESSION_SECRET" in str(exc.value)

    def test_rejects_short_secret(self):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(session_secret="k" * (MIN_SESSION_SECRET_LENGTH - 1))
        message = str(exc.value)
        assert "MESHWIKI_SESSION_SECRET" in message
        assert str(MIN_SESSION_SECRET_LENGTH) in message

    def test_reports_every_problem_at_once(self):
        with pytest.raises(ValidationError) as exc:
            _auth_settings(auth_password="", session_secret="")
        message = str(exc.value)
        assert "MESHWIKI_AUTH_PASSWORD" in message
        assert "MESHWIKI_SESSION_SECRET" in message

    def test_error_message_does_not_leak_secret_values(self):
        password = "s3cr3t-pass-do-not-print"
        secret = "short-key-do-not-print"
        api_key = "factory-key-do-not-print"
        with pytest.raises(ValidationError) as exc:
            _auth_settings(
                auth_password=password, session_secret=secret, factory_api_key=api_key
            )
        message = str(exc.value)
        assert "MESHWIKI_SESSION_SECRET" in message
        # pydantic's own echo of the input is what would carry the values; it
        # is truncated, so checking for whole values alone proves nothing.
        assert "input_value" not in message
        assert "input_value" not in repr(exc.value)
        for value in (password, secret, api_key):
            assert value not in message
            assert value not in repr(exc.value)

    def test_input_echo_is_disabled_in_validation_errors(self):
        assert Settings.model_config.get("hide_input_in_errors") is True

    @pytest.mark.parametrize(
        "extra_env, tail_value",
        [
            ({}, "key-probe"),
            ({"MESHWIKI_GITHUB_WEBHOOK_SECRET": "hook-probe"}, "hook-probe"),
        ],
    )
    def test_startup_error_from_env_does_not_leak_secret_values(
        self, extra_env, tail_value
    ):
        """A too-short secret from the environment must not be echoed.

        pydantic would print the head and tail of its input, in field order.
        The value that lands in that tail is short enough to be shown whole,
        so this fails whenever the input echo is switched back on.
        """
        env = {
            "MESHWIKI_AUTH_ENABLED": "true",
            "MESHWIKI_AUTH_PASSWORD": "pw-probe",
            "MESHWIKI_SESSION_SECRET": "key-probe",
            **extra_env,
        }
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValidationError) as exc:
                Settings(_env_file=None)
        message = str(exc.value)
        assert "MESHWIKI_SESSION_SECRET" in message
        assert tail_value not in message
        assert tail_value not in repr(exc.value)
        assert "pw-probe" not in message
        assert "key-probe" not in message
        assert "input_value" not in message

    def test_blank_env_secret_is_rejected(self):
        """`MESHWIKI_SESSION_SECRET=` (as the examples used to ship) must not start."""
        env = {
            "MESHWIKI_AUTH_ENABLED": "true",
            "MESHWIKI_AUTH_PASSWORD": GOOD_PASSWORD,
            "MESHWIKI_SESSION_SECRET": "",
        }
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValidationError) as exc:
                Settings(_env_file=None)
        assert "MESHWIKI_SESSION_SECRET" in str(exc.value)

    def test_valid_env_configuration_is_accepted(self):
        env = {
            "MESHWIKI_AUTH_ENABLED": "true",
            "MESHWIKI_AUTH_PASSWORD": GOOD_PASSWORD,
            "MESHWIKI_SESSION_SECRET": "x" * 64,
        }
        with patch.dict("os.environ", env, clear=True):
            s = Settings(_env_file=None)
        assert s.auth_enabled is True
        assert s.session_secret == "x" * 64


class TestAuthDisabledSessionSecret:
    """With auth off no secret is needed, but the key is never a public one."""

    def test_starts_without_any_secret(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings(_env_file=None)
        assert s.auth_enabled is False
        assert s.auth_password == ""

    def test_unset_secret_becomes_random_per_instance(self):
        with patch.dict("os.environ", {}, clear=True):
            first = Settings(_env_file=None).session_secret
            second = Settings(_env_file=None).session_secret
        assert first != second
        assert first != DEV_SESSION_SECRET
        assert len(first) >= MIN_SESSION_SECRET_LENGTH

    @pytest.mark.parametrize("secret", ["", "   ", DEV_SESSION_SECRET])
    def test_blank_or_dev_default_secret_is_replaced(self, secret):
        with patch.dict("os.environ", {"MESHWIKI_SESSION_SECRET": secret}, clear=True):
            s = Settings(_env_file=None)
        assert s.session_secret.strip()
        assert s.session_secret != DEV_SESSION_SECRET
        assert len(s.session_secret) >= MIN_SESSION_SECRET_LENGTH

    def test_configured_secret_is_kept(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings(_env_file=None, session_secret=GOOD_SECRET)
        assert s.session_secret == GOOD_SECRET

    def test_placeholder_password_is_ignored_while_auth_is_off(self):
        with patch.dict("os.environ", {}, clear=True):
            s = Settings(_env_file=None, auth_password="changeme")
        assert s.auth_enabled is False


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse the KEY=VALUE lines of an env file the way docker ``env_file`` does."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value
    return env


def _wiki_env(path: Path) -> dict[str, str]:
    """Return only the web app's (``MESHWIKI_``) variables from an env file."""
    return {k: v for k, v in _read_env_file(path).items() if k.startswith("MESHWIKI_")}


class TestShippedEnvExamples:
    """Copying an example verbatim must never yield a running, insecure wiki."""

    @pytest.mark.parametrize("path", ENV_EXAMPLES, ids=lambda p: p.name)
    def test_example_refuses_to_start_unedited(self, path):
        env = _wiki_env(path)
        assert env.get("MESHWIKI_AUTH_ENABLED") == "true"
        with patch.dict("os.environ", env, clear=True):
            with pytest.raises(ValidationError) as exc:
                Settings(_env_file=None)
        message = str(exc.value)
        assert "MESHWIKI_AUTH_PASSWORD" in message
        assert "MESHWIKI_SESSION_SECRET" in message

    @pytest.mark.parametrize("path", ENV_EXAMPLES, ids=lambda p: p.name)
    def test_example_starts_once_credentials_are_filled_in(self, path):
        env = _wiki_env(path)
        env["MESHWIKI_AUTH_PASSWORD"] = GOOD_PASSWORD
        env["MESHWIKI_SESSION_SECRET"] = "x" * 64
        with patch.dict("os.environ", env, clear=True):
            s = Settings(_env_file=None)
        assert s.auth_enabled is True

    def test_staging_deploy_does_not_install_the_example_env_file(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for line in workflow.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            installs_example = "env.example" in stripped and stripped.startswith(
                ("cp ", "mv ", "install ", "cat ")
            )
            assert not installs_example, f"CI installs an example env file: {stripped}"
        assert "staging.env is missing" in workflow
