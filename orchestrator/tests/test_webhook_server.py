"""Tests for the factory orchestrator webhook server."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from factory.webhook_server import _clear_stuck_grinders, app


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
            "X-Meshwiki-Signature-256": sig,
        },
    )
    assert resp.status_code == 200

    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /tasks
# ---------------------------------------------------------------------------


_FAKE_TASKS = [
    {"name": "Task_0001", "metadata": {
        "status": "planned", "title": "A", "modified": "2026-05-10T00:00:00",
    }},
    {"name": "Task_0002", "metadata": {
        "status": "in_progress", "title": "B", "modified": "2026-05-15T07:42:00",
        "repository": "jyrkihuhta/molly-armory",
    }},
    {"name": "Task_0003", "metadata": {
        "status": "planned", "title": "C", "modified": "2026-05-14T12:00:00",
    }},
    {"name": "Task_0004", "metadata": {
        "status": "merged", "title": "D", "modified": "2026-05-13T00:00:00",
    }},
]


def test_tasks_summary_and_recent_order(client: TestClient, monkeypatch) -> None:
    """GET /tasks returns counts by status + recent items in modified-desc order."""
    from factory import webhook_server as ws

    class _FakeClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def list_tasks(self, **_kw): return list(_FAKE_TASKS)

    monkeypatch.setattr(ws, "MeshWikiClient", _FakeClient)

    resp = client.get("/tasks?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert body["by_status"] == {"planned": 2, "in_progress": 1, "merged": 1}
    # limit=2, sorted by modified desc → Task_0002 (May 15) then Task_0003 (May 14)
    assert [r["name"] for r in body["recent"]] == ["Task_0002", "Task_0003"]
    assert body["recent"][0]["repo"] == "jyrkihuhta/molly-armory"


def test_tasks_filter_forwarded_to_list_tasks(client: TestClient, monkeypatch) -> None:
    """Query params propagate to MeshWikiClient.list_tasks."""
    from factory import webhook_server as ws

    captured: dict = {}

    class _CapturingClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def list_tasks(self, **kw):
            captured.update(kw)
            return [_FAKE_TASKS[1]]  # only the in_progress one

    monkeypatch.setattr(ws, "MeshWikiClient", _CapturingClient)

    resp = client.get("/tasks?status=in_progress&repo=jyrkihuhta/molly-armory")
    assert resp.status_code == 200
    assert captured == {
        "status": "in_progress",
        "assignee": None,
        "repo": "jyrkihuhta/molly-armory",
        "parent_task": None,
    }
    body = resp.json()
    assert body["total"] == 1
    assert body["by_status"] == {"in_progress": 1}


def test_tasks_handles_missing_metadata(client: TestClient, monkeypatch) -> None:
    """Tasks with no metadata bucket under 'unknown' and don't crash sorting."""
    from factory import webhook_server as ws

    class _SparseClient:
        def __init__(self, *_a, **_kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def list_tasks(self, **_kw):
            return [
                {"name": "Sparse_1"},
                {"name": "Sparse_2", "metadata": {}},
            ]

    monkeypatch.setattr(ws, "MeshWikiClient", _SparseClient)
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert resp.json()["by_status"] == {"unknown": 2}


# ---------------------------------------------------------------------------
# _clear_stuck_grinders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_stuck_grinders_no_stuck_entries() -> None:
    """When no active grinders have pending subtasks, aupdate_state is not called."""
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=MagicMock(
            values={
                "active_grinders": ["g1"],
                "subtasks": [{"id": "g1", "status": "done"}],
            }
        )
    )
    graph.aupdate_state = AsyncMock()

    await _clear_stuck_grinders(graph, {"configurable": {"thread_id": "T"}}, "T")

    graph.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_clear_stuck_grinders_removes_stuck() -> None:
    """Grinders whose subtasks are still pending are removed from active_grinders."""
    graph = MagicMock()
    graph.aget_state = AsyncMock(
        return_value=MagicMock(
            values={
                "active_grinders": ["g1", "g2", "g3"],
                "subtasks": [
                    {"id": "g1", "status": "done"},
                    {"id": "g2", "status": "pending"},
                    {"id": "g3", "status": "changes_requested"},
                ],
            }
        )
    )
    graph.aupdate_state = AsyncMock()
    config = {"configurable": {"thread_id": "T"}}

    await _clear_stuck_grinders(graph, config, "T")

    graph.aupdate_state.assert_awaited_once()
    call_args = graph.aupdate_state.call_args[0]
    remaining = call_args[1]["active_grinders"]
    assert remaining == ["g1"]


@pytest.mark.asyncio
async def test_clear_stuck_grinders_no_snapshot() -> None:
    """When aget_state returns None, the function returns without error."""
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=None)
    graph.aupdate_state = AsyncMock()

    await _clear_stuck_grinders(graph, {"configurable": {"thread_id": "T"}}, "T")

    graph.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_clear_stuck_grinders_aget_state_raises() -> None:
    """Exceptions from aget_state are caught and logged; no re-raise."""
    graph = MagicMock()
    graph.aget_state = AsyncMock(side_effect=RuntimeError("db error"))
    graph.aupdate_state = AsyncMock()

    await _clear_stuck_grinders(graph, {"configurable": {"thread_id": "T"}}, "T")

    graph.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_clear_stuck_grinders_empty_state() -> None:
    """When active_grinders and subtasks are absent, no update is called."""
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
    graph.aupdate_state = AsyncMock()

    await _clear_stuck_grinders(graph, {"configurable": {"thread_id": "T"}}, "T")

    graph.aupdate_state.assert_not_called()
