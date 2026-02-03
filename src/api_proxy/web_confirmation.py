"""Web-based confirmation queue with SSE support."""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from api_proxy.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class PendingRequest:
    """A request awaiting web confirmation."""

    id: str
    method: str
    path: str
    query_params: dict[str, str] | None
    labels_to_add: list[str] | None
    labels_to_remove: list[str] | None
    event_summary: str | None
    event_attendees: list[str] | None
    send_updates: str | None
    created_at: float
    result_future: asyncio.Future


def _pending_to_dict(pending: PendingRequest) -> dict:
    """Convert PendingRequest to serializable dict (excluding future)."""
    return {
        "id": pending.id,
        "method": pending.method,
        "path": pending.path,
        "query_params": pending.query_params,
        "labels_to_add": pending.labels_to_add,
        "labels_to_remove": pending.labels_to_remove,
        "event_summary": pending.event_summary,
        "event_attendees": pending.event_attendees,
        "send_updates": pending.send_updates,
        "created_at": pending.created_at,
    }


class WebConfirmationQueue:
    """FIFO queue for web-based confirmation with SSE support."""

    def __init__(self):
        self._queue: deque[PendingRequest] = deque()
        self._by_id: dict[str, PendingRequest] = {}
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []

    async def _notify_subscribers(self, event_type: str, pending_snapshot: list[dict] | None = None) -> None:
        """Notify all SSE subscribers of a queue change.

        Args:
            event_type: Type of event (e.g., "request_added", "request_approved")
            pending_snapshot: Optional pre-captured queue state. If not provided,
                             will capture current state (should only be used when
                             called while holding the lock).
        """
        if pending_snapshot is None:
            pending_snapshot = self.get_pending_sync()
        message = {"event": event_type, "pending": pending_snapshot}
        dead_subscribers = []

        for subscriber in self._subscribers:
            try:
                subscriber.put_nowait(message)
            except asyncio.QueueFull:
                dead_subscribers.append(subscriber)

        # Clean up dead subscribers
        for dead in dead_subscribers:
            self._subscribers.remove(dead)

    def get_pending_sync(self) -> list[dict]:
        """Get list of pending requests (synchronous, for internal use)."""
        return [_pending_to_dict(p) for p in self._queue]

    async def add_request(
        self,
        method: str,
        path: str,
        query_params: dict[str, str] | None = None,
        labels_to_add: list[str] | None = None,
        labels_to_remove: list[str] | None = None,
        event_summary: str | None = None,
        event_attendees: list[str] | None = None,
        send_updates: str | None = None,
    ) -> bool:
        """
        Add request to queue and wait for approval.

        Returns True if approved, False if rejected or timed out.
        """
        config = get_config()
        timeout = config.confirmation_timeout

        request_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        pending = PendingRequest(
            id=request_id,
            method=method,
            path=path,
            query_params=query_params,
            labels_to_add=labels_to_add,
            labels_to_remove=labels_to_remove,
            event_summary=event_summary,
            event_attendees=event_attendees,
            send_updates=send_updates,
            created_at=time.time(),
            result_future=future,
        )

        async with self._lock:
            self._queue.append(pending)
            self._by_id[request_id] = pending
            pending_snapshot = self.get_pending_sync()

        logger.info(f"Request {request_id} added to web confirmation queue: {method} {path}")
        await self._notify_subscribers("request_added", pending_snapshot)

        try:
            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future
            return result
        except TimeoutError:
            logger.info(f"Request {request_id} timed out")
            # Remove from queue on timeout
            async with self._lock:
                if request_id in self._by_id:
                    pending = self._by_id.pop(request_id)
                    try:
                        self._queue.remove(pending)
                    except ValueError:
                        pass  # Already removed
                pending_snapshot = self.get_pending_sync()
            await self._notify_subscribers("request_timeout", pending_snapshot)
            return False

    async def get_pending(self) -> list[dict]:
        """Get list of pending requests."""
        async with self._lock:
            return self.get_pending_sync()

    async def approve(self, request_id: str) -> bool:
        """Approve a request. Returns True if found and approved."""
        async with self._lock:
            if request_id not in self._by_id:
                return False

            pending = self._by_id.pop(request_id)
            try:
                self._queue.remove(pending)
            except ValueError:
                pass  # Already removed

            if not pending.result_future.done():
                pending.result_future.set_result(True)
                logger.info(f"Request {request_id} APPROVED via web: {pending.method} {pending.path}")

            pending_snapshot = self.get_pending_sync()

        await self._notify_subscribers("request_approved", pending_snapshot)
        return True

    async def reject(self, request_id: str) -> bool:
        """Reject a request. Returns True if found and rejected."""
        async with self._lock:
            if request_id not in self._by_id:
                return False

            pending = self._by_id.pop(request_id)
            try:
                self._queue.remove(pending)
            except ValueError:
                pass  # Already removed

            if not pending.result_future.done():
                pending.result_future.set_result(False)
                logger.info(f"Request {request_id} REJECTED via web: {pending.method} {pending.path}")

            pending_snapshot = self.get_pending_sync()

        await self._notify_subscribers("request_rejected", pending_snapshot)
        return True

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to queue change events. Returns a queue that receives events."""
        event_queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(event_queue)
        return event_queue

    def unsubscribe(self, event_queue: asyncio.Queue) -> None:
        """Unsubscribe from queue change events."""
        try:
            self._subscribers.remove(event_queue)
        except ValueError:
            pass

    async def stream_events(self) -> AsyncGenerator[str, None]:
        """Stream SSE events when queue changes."""
        event_queue = self.subscribe()
        try:
            # Send initial state
            pending = await self.get_pending()
            initial = {"event": "connected", "pending": pending}
            yield f"data: {json.dumps(initial)}\n\n"

            while True:
                try:
                    # Wait for next event with timeout to send keepalive
                    message = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(message)}\n\n"
                except TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
        finally:
            self.unsubscribe(event_queue)


# Global queue instance
_queue: WebConfirmationQueue | None = None


def get_web_queue() -> WebConfirmationQueue:
    """Get the global web confirmation queue instance."""
    global _queue
    if _queue is None:
        _queue = WebConfirmationQueue()
    return _queue


def reset_web_queue() -> None:
    """Reset the global queue (for testing)."""
    global _queue
    _queue = None
