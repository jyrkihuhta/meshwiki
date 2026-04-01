"""Tests for the factory orchestrator webhook server."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from factory.webhook_server import app


@pytest.fixture
def client():
    """Return a synchronous TestClient for the webhook server."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    """Compute the HMAC-SHA256 signature expected by the server."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    """GET /health returns 200 with status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_webhook_ignored_event(client: TestClient, monkeypatch) -> None:
    """POST /webhook with an unknown event returns status=ignored."""
    # Ensure no secret is configured so HMAC check is skipped
    monkeypatch.setenv("FACTORY_WEBHOOK_SECRET", "")
    # Reset the cached settings so the monkeypatch takes effect
    from factory import config as cfg

    cfg.get_settings.cache_clear()

    payload = json.dumps(
        {"event": "task.unknown_event", "page": "Task_0001", "data": {}}
    ).encode()
    resp = client.post(
        "/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}

    cfg.get_settings.cache_clear()


def test_webhook_invalid_hmac(client: TestClient, monkeypatch) -> None:
    """POST /webhook returns 403 when secret is set but signature is wrong."""
    monkeypatch.setenv("FACTORY_WEBHOOK_SECRET", "supersecret")
    from factory import config as cfg

    cfg.get_settings.cache_clear()

    payload = json.dumps(
        {"event": "task.assigned", "page": "Task_0001", "data": {}}
    ).encode()
    resp = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Meshwiki-Signature": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 403

    cfg.get_settings.cache_clear()


def test_webhook_no_secret_passes(client: TestClient, monkeypatch) -> None:
    """POST /webhook succeeds without a signature when FACTORY_WEBHOOK_SECRET is empty."""
    monkeypatch.setenv("FACTORY_WEBHOOK_SECRET", "")
    from factory import config as cfg

    cfg.get_settings.cache_clear()

    payload = json.dumps(
        {"event": "task.pr_merged", "page": "Task_0001", "data": {}}
    ).encode()
    resp = client.post(
        "/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] != "error"

    cfg.get_settings.cache_clear()


def test_webhook_valid_hmac(client: TestClient, monkeypatch) -> None:
    """POST /webhook succeeds when a valid HMAC signature is provided."""
    secret = "mysecret"
    monkeypatch.setenv("FACTORY_WEBHOOK_SECRET", secret)
    from factory import config as cfg

    cfg.get_settings.cache_clear()

    payload = json.dumps(
        {"event": "task.pr_merged", "page": "Task_0001", "data": {}}
    ).encode()
    sig = _sign(payload, secret)
    resp = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Meshwiki-Signature": sig,
        },
    )
    assert resp.status_code == 200

    cfg.get_settings.cache_clear()
