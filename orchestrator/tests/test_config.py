"""Tests for factory config validation."""

import pytest

from factory.config import Settings, validate_settings


def _settings(**kwargs) -> Settings:
    base = {
        "github_token": "ghp_test",
        "github_repo": "owner/repo",
        "webhook_secret": "secret",
    }
    base.update(kwargs)
    return Settings(**base)


def test_validate_settings_ok():
    validate_settings(_settings())  # no exception


def test_validate_settings_missing_github_token():
    with pytest.raises(RuntimeError, match="FACTORY_GITHUB_TOKEN"):
        validate_settings(_settings(github_token=""))


def test_validate_settings_missing_github_repo():
    with pytest.raises(RuntimeError, match="FACTORY_GITHUB_REPO"):
        validate_settings(_settings(github_repo=""))


def test_validate_settings_missing_both_reported_together():
    with pytest.raises(RuntimeError) as exc_info:
        validate_settings(_settings(github_token="", github_repo=""))
    msg = str(exc_info.value)
    assert "FACTORY_GITHUB_TOKEN" in msg
    assert "FACTORY_GITHUB_REPO" in msg


def test_validate_settings_warns_on_empty_webhook_secret(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="factory.config"):
        validate_settings(_settings(webhook_secret=""))
    assert "FACTORY_WEBHOOK_SECRET" in caplog.text


def test_repo_root_can_be_overridden():
    s = Settings(github_token="t", github_repo="o/r", repo_root="/tmp/myrepo")
    assert s.repo_root == "/tmp/myrepo"


def test_default_checkpoint_db_is_platform_aware():
    import platform

    s = Settings(github_token="t", github_repo="o/r")
    assert s.checkpoint_db.endswith("checkpoints.db")
    if platform.system() == "Linux":
        assert s.checkpoint_db == "/data/checkpoints.db"
    else:
        assert s.checkpoint_db != "/data/checkpoints.db"
