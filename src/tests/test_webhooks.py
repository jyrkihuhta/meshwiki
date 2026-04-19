"""Unit tests for the outbound webhook dispatcher."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import meshwiki.config as cfg
from meshwiki.core.webhooks import WebhookDispatcher, WebhookEvent, _append_jsonl


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
        return MagicMock()

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


@pytest.mark.asyncio
async def test_send_raises_on_http_error(factory_settings):
    """_send must propagate HTTP error status via raise_for_status."""
    d = WebhookDispatcher()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    evt = WebhookEvent(event="test", page_name="P", data={})
    with pytest.raises(httpx.HTTPStatusError):
        await d._send(mock_client, evt)


# ---------------------------------------------------------------------------
# _send_with_retries — exponential backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_with_retries_succeeds_on_first_attempt(factory_settings):
    d = WebhookDispatcher()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock()

    evt = WebhookEvent(event="test", page_name="P", data={})
    await d._send_with_retries(mock_client, evt)

    assert mock_client.post.await_count == 1


@pytest.mark.asyncio
async def test_send_with_retries_retries_on_failure(factory_settings):
    """Fails twice, succeeds on third attempt."""
    d = WebhookDispatcher()
    call_count = 0

    async def flaky_post(url, *, content, headers):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count < 3:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "503", request=MagicMock(), response=MagicMock()
            )
        else:
            resp.raise_for_status.return_value = None
        return resp

    mock_client = AsyncMock()
    mock_client.post = flaky_post

    evt = WebhookEvent(event="test", page_name="P", data={})
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await d._send_with_retries(mock_client, evt)

    assert call_count == 3


@pytest.mark.asyncio
async def test_send_with_retries_raises_after_max_attempts(factory_settings):
    """Exhausts all attempts and re-raises the last exception."""
    from meshwiki.core.webhooks import _MAX_ATTEMPTS

    d = WebhookDispatcher()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    evt = WebhookEvent(event="test", page_name="P", data={})
    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError):
            await d._send_with_retries(mock_client, evt)

    assert mock_client.post.await_count == _MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# _write_dead_letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_dead_letter_creates_jsonl(tmp_path):
    d = WebhookDispatcher()
    d._dead_letter_path = tmp_path / "dl.jsonl"

    evt = WebhookEvent(event="test.event", page_name="Page", data={"k": "v"})
    await d._write_dead_letter(evt, "connection refused")

    assert d._dead_letter_path.exists()
    line = d._dead_letter_path.read_text().strip()
    record = json.loads(line)
    assert record["event"] == "test.event"
    assert record["error"] == "connection refused"
    assert "failed_at" in record


@pytest.mark.asyncio
async def test_write_dead_letter_noop_when_path_none():
    d = WebhookDispatcher()
    d._dead_letter_path = None
    evt = WebhookEvent(event="test", page_name="P", data={})
    await d._write_dead_letter(evt, "err")  # must not raise


@pytest.mark.asyncio
async def test_write_dead_letter_appends_multiple(tmp_path):
    d = WebhookDispatcher()
    d._dead_letter_path = tmp_path / "dl.jsonl"

    for i in range(3):
        evt = WebhookEvent(event=f"event.{i}", page_name="P", data={})
        await d._write_dead_letter(evt, "err")

    lines = d._dead_letter_path.read_text().strip().splitlines()
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# start() — dead-letter path wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_uses_explicit_dead_letter_path(tmp_path, factory_settings):
    d = WebhookDispatcher()
    custom_path = tmp_path / "custom_dl.jsonl"
    await d.start(dead_letter_path=custom_path)
    await d.stop()
    assert d._dead_letter_path == custom_path


@pytest.mark.asyncio
async def test_start_defaults_dead_letter_to_data_dir(tmp_path, factory_settings):
    d = WebhookDispatcher()
    await d.start()
    await d.stop()
    assert (
        d._dead_letter_path == factory_settings.data_dir / ".webhook_dead_letter.jsonl"
    )


# ---------------------------------------------------------------------------
# queue overflow — ERROR level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_full_logs_error(factory_settings, caplog):
    import logging

    d = WebhookDispatcher()
    for i in range(d._QUEUE_SIZE):
        d._queue.put_nowait(WebhookEvent(event="test", page_name="P", data={}))

    with caplog.at_level(logging.ERROR, logger="meshwiki.core.webhooks"):
        await d.emit("overflow.event", "Page", {})

    assert any("webhook_queue_full" in r.message for r in caplog.records)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ---------------------------------------------------------------------------
# _append_jsonl helper
# ---------------------------------------------------------------------------


def test_append_jsonl_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "dl.jsonl"
    _append_jsonl(path, {"key": "value"})
    assert path.exists()
    record = json.loads(path.read_text())
    assert record["key"] == "value"
