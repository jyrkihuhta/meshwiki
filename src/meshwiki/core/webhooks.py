"""Outbound webhook dispatcher for the agent factory.

Emits HMAC-signed HTTP POST events to the configured orchestrator URL
whenever a task state transition occurs.  Uses an asyncio queue for
fire-and-forget delivery so route handlers are never blocked.

Delivery guarantees:
- Up to _MAX_ATTEMPTS tries with exponential backoff before giving up.
- Events that exhaust all retries are appended to a dead-letter JSONL file
  so nothing is silently lost.  An operator can inspect and replay from it.
- Queue overflow is logged at ERROR level (not just WARNING).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from meshwiki.core.logging import get_logger

log = get_logger(__name__)

_MAX_ATTEMPTS = 5
_BASE_DELAY = 1.0  # seconds; actual delays: 1, 2, 4, 8, 16


@dataclass
class WebhookEvent:
    """A single outbound webhook event."""

    event: str
    page_name: str
    data: dict
    canonical_event: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_payload(self) -> dict:
        payload = {
            "event": self.event,
            "page": self.page_name,
            "timestamp": self.timestamp,
            "data": self.data,
            "source": "meshwiki",
        }
        if self.canonical_event:
            payload["canonical_event"] = self.canonical_event
        return payload


class WebhookDispatcher:
    """Fire-and-forget outbound webhook dispatcher with retries and dead-letter logging.

    Events are queued and dispatched asynchronously by a background task.
    Failed deliveries are retried with exponential backoff.  Events that
    exhaust all attempts are written to a dead-letter JSONL file rather than
    silently discarded.
    """

    _QUEUE_SIZE = 1000

    def __init__(self) -> None:
        self._queue: asyncio.Queue[WebhookEvent] = asyncio.Queue(
            maxsize=self._QUEUE_SIZE
        )
        self._task: asyncio.Task | None = None
        self._dead_letter_path: Path | None = None

    async def start(self, dead_letter_path: Path | None = None) -> None:
        """Start the background dispatch loop.

        Args:
            dead_letter_path: Optional path to the dead-letter JSONL file.
                Defaults to ``{data_dir}/.webhook_dead_letter.jsonl`` when
                the factory is configured.
        """
        if dead_letter_path is not None:
            self._dead_letter_path = dead_letter_path
        elif self._dead_letter_path is None:
            try:
                from meshwiki.config import settings

                self._dead_letter_path = (
                    settings.data_dir / ".webhook_dead_letter.jsonl"
                )
            except Exception:
                pass
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        """Cancel the background dispatch loop."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def emit(
        self,
        event: str,
        page_name: str,
        data: dict,
        canonical_event: str | None = None,
    ) -> None:
        """Queue an event for dispatch.  Returns immediately; never raises."""
        from meshwiki.config import settings

        if not settings.factory_enabled or not settings.factory_webhook_url:
            return

        evt = WebhookEvent(
            event=event,
            page_name=page_name,
            data=data,
            canonical_event=canonical_event,
        )
        try:
            self._queue.put_nowait(evt)
        except asyncio.QueueFull:
            log.error(
                "webhook_queue_full",
                webhook_event=event,
                page=page_name,
                queue_size=self._QUEUE_SIZE,
            )

    async def _dispatch_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                evt = await self._queue.get()
                try:
                    await self._send_with_retries(client, evt)
                except Exception as exc:
                    log.error(
                        "webhook_dead_letter",
                        webhook_event=evt.event,
                        page=evt.page_name,
                        attempts=_MAX_ATTEMPTS,
                        error=str(exc),
                    )
                    await self._write_dead_letter(evt, str(exc))
                finally:
                    self._queue.task_done()

    async def _send_with_retries(
        self, client: httpx.AsyncClient, evt: WebhookEvent
    ) -> None:
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            if attempt > 0:
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "webhook_retry",
                    webhook_event=evt.event,
                    page=evt.page_name,
                    attempt=attempt + 1,
                    max_attempts=_MAX_ATTEMPTS,
                    delay_s=round(delay, 1),
                )
                await asyncio.sleep(delay)
            try:
                await self._send(client, evt)
                return
            except Exception as exc:
                last_exc = exc
        raise last_exc  # type: ignore[misc]

    async def _send(self, client: httpx.AsyncClient, evt: WebhookEvent) -> None:
        from meshwiki.config import settings

        payload = evt.to_payload()
        body = json.dumps(
            payload,
            default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o),
        ).encode()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.factory_webhook_secret:
            sig = hmac.new(
                settings.factory_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-MeshWiki-Signature-256"] = f"sha256={sig}"

        resp = await client.post(
            settings.factory_webhook_url, content=body, headers=headers
        )
        resp.raise_for_status()

    async def _write_dead_letter(self, evt: WebhookEvent, error: str) -> None:
        if self._dead_letter_path is None:
            return
        record = {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            **evt.to_payload(),
        }
        try:
            await asyncio.to_thread(_append_jsonl, self._dead_letter_path, record)
        except Exception as exc:
            log.error("webhook_dead_letter_write_failed", error=str(exc))


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


dispatcher = WebhookDispatcher()
