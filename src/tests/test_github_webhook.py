"""Integration tests for the POST /api/v1/github/webhook endpoint."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json

import pytest
from httpx import ASGITransport, AsyncClient

import meshwiki.config as cfg
import meshwiki.main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` signature for a raw body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _pr_payload(pr_number: int, action: str = "closed", merged: bool = True) -> dict:
    """Build a minimal GitHub pull_request webhook payload."""
    return {
        "action": action,
        "number": pr_number,
        "pull_request": {
            "number": pr_number,
            "merged": merged,
            "merged_at": "2024-01-15T12:00:00Z",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_settings(tmp_path):
    """Settings with factory enabled and a known webhook secret."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_api_key="test-key-123",
        github_webhook_secret="super-secret",
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
def webhook_settings_no_secret(tmp_path):
    """Settings with factory enabled but *no* webhook secret (dev mode)."""
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_api_key="test-key-123",
        github_webhook_secret="",
        graph_watch=False,
        auth_enabled=False,
    )
    importlib.reload(meshwiki.main)
    yield cfg.settings
    cfg.settings = original
    importlib.reload(meshwiki.main)


@pytest.fixture
async def client(webhook_settings):
    """AsyncClient wired to the app with webhook secret configured."""
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(webhook_settings.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


@pytest.fixture
async def client_no_secret(webhook_settings_no_secret):
    """AsyncClient wired to the app with *no* webhook secret."""
    from meshwiki.core.graph import init_engine, shutdown_engine

    init_engine(webhook_settings_no_secret.data_dir, watch=False)
    meshwiki.main.manager.start_polling()
    async with AsyncClient(
        transport=ASGITransport(app=meshwiki.main.app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        yield c
    meshwiki.main.manager.stop_polling()
    shutdown_engine()


_AUTH = {"Authorization": "Bearer test-key-123"}
_WEBHOOK_URL = "/api/v1/github/webhook"


# ---------------------------------------------------------------------------
# Helper to POST a signed webhook
# ---------------------------------------------------------------------------


async def _post_webhook(
    client: AsyncClient,
    payload: dict,
    secret: str = "super-secret",
    event: str = "pull_request",
    override_signature: str | None = None,
) -> object:
    body = json.dumps(payload).encode()
    sig = override_signature if override_signature is not None else _sign(secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": sig,
    }
    return await client.post(_WEBHOOK_URL, content=body, headers=headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_webhook_ignored_event(client):
    """Non-pull_request events should be ignored with status='ignored'."""
    payload = {"action": "created"}
    body = json.dumps(payload).encode()
    sig = _sign("super-secret", body)
    resp = await client.post(
        _WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_github_webhook_not_merged(client):
    """A closed-but-not-merged PR should be ignored."""
    payload = _pr_payload(pr_number=10, action="closed", merged=False)
    resp = await _post_webhook(client, payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_github_webhook_merges_task(client, webhook_settings):
    """A merged PR should transition the matching task page to 'done'."""
    # Create a task page in 'review' state with pr_number: "42"
    task_content = '---\ntype: task\nstatus: review\npr_number: "42"\n---\nTask body'
    create_resp = await client.post(
        "/api/v1/pages",
        json={"name": "Task_0042_fix_something", "content": task_content},
        headers=_AUTH,
    )
    assert create_resp.status_code == 201

    # POST the merge event for PR #42
    payload = _pr_payload(pr_number=42)
    resp = await _post_webhook(client, payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    # Storage converts underscores to spaces in page names
    assert data["page"] == "Task 0042 fix something"

    # Verify the task is now 'done'
    page_resp = await client.get("/api/v1/pages/Task_0042_fix_something", headers=_AUTH)
    assert page_resp.status_code == 200
    page_data = page_resp.json()
    assert page_data["metadata"]["status"] == "done"
    assert "merged_at" in page_data["metadata"]


@pytest.mark.asyncio
async def test_github_webhook_no_matching_task(client):
    """Merge event for an unknown PR should return 200 with 'no_task_found'."""
    payload = _pr_payload(pr_number=999)
    resp = await _post_webhook(client, payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_task_found"


@pytest.mark.asyncio
async def test_github_webhook_invalid_hmac(client):
    """A wrong HMAC signature should return 403."""
    payload = _pr_payload(pr_number=1)
    resp = await _post_webhook(
        client,
        payload,
        override_signature="sha256=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_github_webhook_no_secret_skips_verification(client_no_secret):
    """When no secret is configured, HMAC verification is skipped (dev mode)."""
    # Create a task in review state
    task_content = '---\ntype: task\nstatus: review\npr_number: "77"\n---\nDev task'
    create_resp = await client_no_secret.post(
        "/api/v1/pages",
        json={"name": "Task_0077_dev_task", "content": task_content},
        headers=_AUTH,
    )
    assert create_resp.status_code == 201

    # POST a merge event without any signature header
    payload = _pr_payload(pr_number=77)
    body = json.dumps(payload).encode()
    resp = await client_no_secret.post(
        _WEBHOOK_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            # Deliberately no X-Hub-Signature-256 header
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
