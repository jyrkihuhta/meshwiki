"""Unit tests for the outbound webhook dispatcher."""

import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest

import meshwiki.config as cfg
from meshwiki.core.webhooks import WebhookDispatcher, WebhookEvent


@pytest.fixture
def factory_settings(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_webhook_url="http://localhost:9999/webhook",
        factory_webhook_secret="",
        graph_watch=False,
    )
    yield cfg.settings
    cfg.settings = original


@pytest.fixture
def signed_settings(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_webhook_url="http://localhost:9999/webhook",
        factory_webhook_secret="super-secret",
        graph_watch=False,
    )
    yield cfg.settings
    cfg.settings = original


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------


def test_event_payload_structure():
    evt = WebhookEvent(
        event="task.draft_to_planned", page_name="Task_0001", data={"status": "planned"}
    )
    payload = evt.to_payload()
    assert payload["event"] == "task.draft_to_planned"
    assert payload["page"] == "Task_0001"
    assert payload["source"] == "meshwiki"
    assert "timestamp" in payload
    assert payload["data"] == {"status": "planned"}
    assert "canonical_event" not in payload  # not set


def test_event_payload_includes_canonical_when_set():
    evt = WebhookEvent(
        event="task.planned_to_decomposed",
        page_name="Task_0001",
        data={},
        canonical_event="task.decomposed",
    )
    payload = evt.to_payload()
    assert payload["canonical_event"] == "task.decomposed"


# ---------------------------------------------------------------------------
# emit() — no-ops when disabled or no URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_noop_when_factory_disabled(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path, factory_enabled=False, graph_watch=False
    )
    try:
        d = WebhookDispatcher()
        await d.emit("test.event", "Page", {})
        assert d._queue.empty()
    finally:
        cfg.settings = original


@pytest.mark.asyncio
async def test_emit_noop_when_no_url(tmp_path):
    original = cfg.settings
    cfg.settings = cfg.Settings(
        data_dir=tmp_path,
        factory_enabled=True,
        factory_webhook_url="",
        graph_watch=False,
    )
    try:
        d = WebhookDispatcher()
        await d.emit("test.event", "Page", {})
        assert d._queue.empty()
    finally:
        cfg.settings = original


# ---------------------------------------------------------------------------
# emit() — queues events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_queues_event(factory_settings):
    d = WebhookDispatcher()
    await d.emit("task.draft_to_planned", "Task_0001", {"status": "planned"})
    assert d._queue.qsize() == 1


@pytest.mark.asyncio
async def test_queue_full_drops_event(factory_settings):
    d = WebhookDispatcher()
    # Fill the queue
    for i in range(d._QUEUE_SIZE):
        d._queue.put_nowait(WebhookEvent(event="test", page_name="P", data={}))
    # emit should not raise even when full
    await d.emit("test.event", "Page", {})
    assert d._queue.qsize() == d._QUEUE_SIZE


# ---------------------------------------------------------------------------
# _send() — HTTP POST and HMAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_posts_to_webhook_url(factory_settings):
    d = WebhookDispatcher()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()

    evt = WebhookEvent(
        event="task.draft_to_planned", page_name="Task_0001", data={"status": "planned"}
    )
    await d._send(mock_client, evt)

    mock_client.post.assert_awaited_once()
    url, *_ = mock_client.post.call_args.args
    assert url == factory_settings.factory_webhook_url


@pytest.mark.asyncio
async def test_send_no_signature_when_no_secret(factory_settings):
    d = WebhookDispatcher()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()

    evt = WebhookEvent(event="test", page_name="P", data={})
    await d._send(mock_client, evt)

    headers = mock_client.post.call_args.kwargs.get("headers", {})
    assert "X-MeshWiki-Signature-256" not in headers


@pytest.mark.asyncio
async def test_send_hmac_signature_present(signed_settings):
    d = WebhookDispatcher()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()

    evt = WebhookEvent(event="test", page_name="P", data={})
    await d._send(mock_client, evt)

    headers = mock_client.post.call_args.kwargs.get("headers", {})
    assert "X-MeshWiki-Signature-256" in headers
    assert headers["X-MeshWiki-Signature-256"].startswith("sha256=")


@pytest.mark.asyncio
async def test_send_hmac_signature_correct(signed_settings):
    d = WebhookDispatcher()
    captured_body = None
    captured_sig = None

    async def fake_post(url, *, content, headers):
        nonlocal captured_body, captured_sig
        captured_body = content
        captured_sig = headers.get("X-MeshWiki-Signature-256", "")

    mock_client = AsyncMock()
    mock_client.post = fake_post

    evt = WebhookEvent(event="test", page_name="P", data={"k": "v"})
    await d._send(mock_client, evt)

    expected_sig = (
        "sha256="
        + hmac.new(
            signed_settings.factory_webhook_secret.encode(),
            captured_body,
            hashlib.sha256,
        ).hexdigest()
    )
    assert captured_sig == expected_sig
