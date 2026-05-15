"""WebSocket connection manager with event fanout.

Polls the graph engine for events and broadcasts them to all
connected WebSocket clients via per-client asyncio queues.
"""

import asyncio
import time
from typing import Any

from meshwiki.core import page_cache
from meshwiki.core.graph import get_engine
from meshwiki.core.logging import get_logger

log = get_logger(__name__)

# Minimum seconds between broadcasts of the same page_updated event.
# Prevents event floods (e.g. inotify IN_ATTRIB storms on Docker bind mounts)
# from spamming fragment refreshes in connected browsers.
_PAGE_EVENT_DEDUP_SECS = 60.0


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert a GraphEvent to a JSON-serializable dict."""
    d: dict[str, Any] = {"type": event.event_type()}
    if event.page_name() is not None:
        d["page"] = event.page_name()
    if event.link_from() is not None:
        d["from"] = event.link_from()
        d["to"] = event.link_to()
    return d


class ConnectionManager:
    """Manages WebSocket connections and fans out graph events."""

    def __init__(self) -> None:
        self._clients: dict[int, asyncio.Queue[dict[str, Any]]] = {}
        self._next_id: int = 0
        self._poll_task: asyncio.Task | None = None
        self._running: bool = False
        self._last_page_broadcast: dict[str, float] = {}

    def connect(self) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
        """Register a new client. Returns (client_id, queue)."""
        client_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._clients[client_id] = queue
        log.info("ws_client_connected", client_id=client_id, total=len(self._clients))
        return client_id, queue

    def disconnect(self, client_id: int) -> None:
        """Unregister a client."""
        self._clients.pop(client_id, None)
        log.info(
            "ws_client_disconnected", client_id=client_id, total=len(self._clients)
        )

    @property
    def client_count(self) -> int:
        """Number of connected clients."""
        return len(self._clients)

    def start_polling(self, interval: float = 0.5) -> None:
        """Start the background polling task."""
        if self._poll_task is not None:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(interval))

    def stop_polling(self) -> None:
        """Stop the background polling task."""
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self, interval: float) -> None:
        """Poll engine for events and broadcast to all clients."""
        while self._running:
            try:
                engine = get_engine()
                if engine is not None and engine.has_pending_events():
                    events = engine.poll_events()
                    for event in events:
                        page_cache.invalidate()
                        msg = _event_to_dict(event)
                        await self._broadcast(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("ws_poll_error")
            await asyncio.sleep(interval)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        if msg.get("type") == "page_updated":
            page = msg.get("page", "")
            now = time.monotonic()
            if now - self._last_page_broadcast.get(page, 0.0) < _PAGE_EVENT_DEDUP_SECS:
                return
            self._last_page_broadcast[page] = now

        for client_id, queue in list(self._clients.items()):
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("ws_queue_full", client_id=client_id)


# Module-level singleton
manager = ConnectionManager()
