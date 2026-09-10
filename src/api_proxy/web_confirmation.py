"""Web-based confirmation queue with SSE support."""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from api_proxy import notifications
from api_proxy.config import get_config
from api_proxy.confirmation import ConfirmationOutcome

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
    message_sender: str | None
    message_subject: str | None
    draft_thread_id: str | None
    event_summary: str | None
    event_attendees: list[str] | None
    send_updates: str | None
    event_start: str | None
    event_end: str | None
    rsvp_response: str | None
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
        "message_sender": pending.message_sender,
        "message_subject": pending.message_subject,
        "draft_thread_id": pending.draft_thread_id,
        "event_summary": pending.event_summary,
        "event_attendees": pending.event_attendees,
        "send_updates": pending.send_updates,
        "event_start": pending.event_start,
        "event_end": pending.event_end,
        "rsvp_response": pending.rsvp_response,
        "created_at": pending.created_at,
    }


class WebConfirmationQueue:
    """FIFO queue for web-based confirmation with SSE support."""

    def __init__(self):
        self._queue: deque[PendingRequest] = deque()
        self._by_id: dict[str, PendingRequest] = {}
        self._lock = asyncio.Lock()
        self._subscribers: list[asyncio.Queue] = []

    async def _notify_subscribers(
        self, event_type: str, pending_snapshot: list[dict] | None = None
    ) -> None:
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
        message_sender: str | None = None,
        message_subject: str | None = None,
        draft_thread_id: str | None = None,
        event_summary: str | None = None,
        event_attendees: list[str] | None = None,
        send_updates: str | None = None,
        event_start: str | None = None,
        event_end: str | None = None,
        rsvp_response: str | None = None,
    ) -> ConfirmationOutcome:
        """
        Add request to queue and wait for the operator's decision.

        Returns APPROVED or REJECTED if an operator acted on the request,
        and EXPIRED if nobody responded within the confirmation timeout.
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
            message_sender=message_sender,
            message_subject=message_subject,
            draft_thread_id=draft_thread_id,
            event_summary=event_summary,
            event_attendees=event_attendees,
            send_updates=send_updates,
            event_start=event_start,
            event_end=event_end,
            rsvp_response=rsvp_response,
            created_at=time.time(),
            result_future=future,
        )

        async with self._lock:
            self._queue.append(pending)
            self._by_id[request_id] = pending
            pending_snapshot = self.get_pending_sync()

        logger.info(f"Request {request_id} added to web confirmation queue: {method} {path}")
        await self._notify_subscribers("request_added", pending_snapshot)
        # Tell the operator a request is waiting even when the dashboard is
        # closed. Fire-and-forget: an ntfy outage can never fail or delay
        # the approval flow.
        notifications.notify_request_pending(pending)

        try:
            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future
            return result
        except TimeoutError:
            # Remove our entry from the queue whether this is a genuine
            # expiry or the lost-decision race recovered below (in the race,
            # approve()/reject() already removed it, making this a no-op).
            async with self._lock:
                if request_id in self._by_id:
                    pending = self._by_id.pop(request_id)
                    try:
                        self._queue.remove(pending)
                    except ValueError:
                        pass  # Already removed
                pending_snapshot = self.get_pending_sync()

            # On Python >= 3.12, asyncio.wait_for no longer recovers a done
            # future on timeout: when the operator's decision and the timer
            # land in the same event-loop batch, set_result() succeeds and
            # wait_for still raises TimeoutError. A decision recorded on the
            # future must win over the timer - otherwise a fully-approved
            # mutation is discarded as expired and never forwarded (and both
            # "approved" and "expired" follow-ups would fire).
            recovered = self._recover_operator_decision(future)
            if recovered is not None:
                logger.info(
                    f"Request {request_id} was decided ({recovered.value}) in the same "
                    "event-loop batch as its timeout; honoring the operator's decision"
                )
                return recovered

            logger.info(f"Request {request_id} expired with no operator response")
            await self._notify_subscribers("request_timeout", pending_snapshot)
            # Follow-up so a stale "approval needed" phone notification is
            # not acted on after the window has already closed.
            notifications.notify_request_resolved(pending, ConfirmationOutcome.EXPIRED.value)
            return ConfirmationOutcome.EXPIRED

    @staticmethod
    def _recover_operator_decision(future: asyncio.Future) -> ConfirmationOutcome | None:
        """
        Return the decision recorded on the result future, if any.

        Used after a TimeoutError from asyncio.wait_for: on Python >= 3.12 a
        future that completed in the same event-loop batch as the timer is
        not recovered by wait_for itself, so a recorded APPROVED/REJECTED
        would otherwise be misreported as expiry. A cancelled or still-pending
        future means no operator ever decided (returns None).
        """
        if future.done() and not future.cancelled():
            return future.result()
        return None

    async def get_pending(self) -> list[dict]:
        """Get list of pending requests."""
        async with self._lock:
            return self.get_pending_sync()

    async def approve(self, request_id: str) -> bool:
        """
        Approve a request. Returns True only if the approval took effect;
        False if the request is unknown or was already resolved (e.g. its
        confirmation window expired first).
        """
        resolved = False
        async with self._lock:
            if request_id not in self._by_id:
                return False

            pending = self._by_id.pop(request_id)
            try:
                self._queue.remove(pending)
            except ValueError:
                pass  # Already removed

            if not pending.result_future.done():
                pending.result_future.set_result(ConfirmationOutcome.APPROVED)
                resolved = True
                logger.info(
                    f"Request {request_id} APPROVED via web: {pending.method} {pending.path}"
                )

            pending_snapshot = self.get_pending_sync()

        if not resolved:
            # The future was already done - typically cancelled by the
            # expiring wait_for an instant before the operator's click
            # landed. The approval did NOT take effect (the caller was told
            # the request expired and nothing was forwarded), so report
            # "not found or already processed" rather than a phantom
            # success, and broadcast no request_approved event.
            logger.info(f"Request {request_id} was already resolved; approve ignored")
            return False

        await self._notify_subscribers("request_approved", pending_snapshot)
        notifications.notify_request_resolved(pending, ConfirmationOutcome.APPROVED.value)
        return True

    async def reject(self, request_id: str) -> bool:
        """
        Reject a request. Returns True only if the rejection took effect;
        False if the request is unknown or was already resolved (e.g. its
        confirmation window expired first).
        """
        resolved = False
        async with self._lock:
            if request_id not in self._by_id:
                return False

            pending = self._by_id.pop(request_id)
            try:
                self._queue.remove(pending)
            except ValueError:
                pass  # Already removed

            if not pending.result_future.done():
                pending.result_future.set_result(ConfirmationOutcome.REJECTED)
                resolved = True
                logger.info(
                    f"Request {request_id} REJECTED via web: {pending.method} {pending.path}"
                )

            pending_snapshot = self.get_pending_sync()

        if not resolved:
            # Mirror of approve(): the rejection did not take effect, so do
            # not report success or broadcast request_rejected.
            logger.info(f"Request {request_id} was already resolved; reject ignored")
            return False

        await self._notify_subscribers("request_rejected", pending_snapshot)
        notifications.notify_request_resolved(pending, ConfirmationOutcome.REJECTED.value)
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
