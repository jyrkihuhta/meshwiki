"""Outbound webhook dispatcher for the agent factory.

Emits HMAC-signed HTTP POST events to the configured orchestrator URL
whenever a task state transition occurs.  Uses an asyncio queue for
fire-and-forget delivery so route handlers are never blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)


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
    """Fire-and-forget outbound webhook dispatcher.

    Events are queued and dispatched asynchronously by a background task.
    If the queue is full, events are dropped with a warning rather than
    blocking the caller.
    """

    _QUEUE_SIZE = 1000

    def __init__(self) -> None:
        self._queue: asyncio.Queue[WebhookEvent] = asyncio.Queue(
            maxsize=self._QUEUE_SIZE
        )
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background dispatch loop."""
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
            log.warning(
                "webhook_queue_full",
                extra={"event": event, "page": page_name},
            )

    async def _dispatch_loop(self) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                evt = await self._queue.get()
                try:
                    await self._send(client, evt)
                except Exception as exc:
                    log.warning(
                        "webhook_dispatch_failed event=%s error=%s",
                        evt.event,
                        exc,
                        extra={"event": evt.event, "error": str(exc)},
                    )
                finally:
                    self._queue.task_done()

    async def _send(self, client: "httpx.AsyncClient", evt: WebhookEvent) -> None:
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

        await client.post(settings.factory_webhook_url, content=body, headers=headers)


dispatcher = WebhookDispatcher()
