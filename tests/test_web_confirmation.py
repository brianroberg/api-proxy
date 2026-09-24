"""Tests for web-based confirmation queue."""

import asyncio
import json

import pytest

from api_proxy import notifications
from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import ConfirmationOutcome
from api_proxy.web_confirmation import (
    WebConfirmationQueue,
    get_web_queue,
    reset_web_queue,
)


def _drain(event_queue: asyncio.Queue) -> list[dict]:
    """Collect every event a subscriber has received so far."""
    events = []
    while True:
        try:
            events.append(event_queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


@pytest.fixture
def web_queue():
    """Create a fresh web confirmation queue for each test."""
    reset_web_queue()
    queue = WebConfirmationQueue()
    return queue


@pytest.fixture
def config_web_confirm(temp_dir, api_keys_file, token_file):
    """Configure for web confirmation with short timeout."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.MODIFY,
        confirmation_timeout=1.0,  # 1 second for fast tests
        web_confirmation=True,
    )
    set_config(config)
    return config


class TestWebConfirmationQueue:
    """Tests for WebConfirmationQueue class."""

    @pytest.mark.asyncio
    async def test_add_and_approve_request(self, web_queue, config_web_confirm):
        """Request should be approved when approve is called."""

        # Start the request in background
        async def make_request():
            return await web_queue.add_request(
                method="POST",
                path="/gmail/v1/users/me/messages/123/modify",
                labels_to_add=["STARRED"],
            )

        task = asyncio.create_task(make_request())

        # Wait for it to appear in queue
        await asyncio.sleep(0.05)

        # Check it's in the queue
        pending = await web_queue.get_pending()
        assert len(pending) == 1
        assert pending[0]["method"] == "POST"
        assert pending[0]["path"] == "/gmail/v1/users/me/messages/123/modify"
        assert pending[0]["labels_to_add"] == ["STARRED"]

        # Approve it
        request_id = pending[0]["id"]
        success = await web_queue.approve(request_id)
        assert success is True

        # Wait for request to complete
        result = await task
        assert result is ConfirmationOutcome.APPROVED

    @pytest.mark.asyncio
    async def test_add_and_reject_request(self, web_queue, config_web_confirm):
        """Request should be rejected when reject is called."""

        async def make_request():
            return await web_queue.add_request(
                method="POST",
                path="/gmail/v1/users/me/messages/123/trash",
            )

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.05)

        pending = await web_queue.get_pending()
        assert len(pending) == 1

        # Reject it
        request_id = pending[0]["id"]
        success = await web_queue.reject(request_id)
        assert success is True

        # Wait for request to complete
        result = await task
        assert result is ConfirmationOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_timeout_returns_expired(self, web_queue, config_web_confirm):
        """Request should return EXPIRED (not REJECTED) if it times out."""
        result = await web_queue.add_request(
            method="POST",
            path="/gmail/v1/users/me/messages/123/modify",
        )

        # Should time out and report expiry
        assert result is ConfirmationOutcome.EXPIRED

        # Queue should be empty after timeout
        pending = await web_queue.get_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_fifo_order(self, web_queue, config_web_confirm):
        """Queue should maintain FIFO order."""

        # Add multiple requests
        async def add_request(path: str):
            return await web_queue.add_request(method="GET", path=path)

        task1 = asyncio.create_task(add_request("/path1"))
        await asyncio.sleep(0.01)
        task2 = asyncio.create_task(add_request("/path2"))
        await asyncio.sleep(0.01)
        task3 = asyncio.create_task(add_request("/path3"))
        await asyncio.sleep(0.05)

        # Check order
        pending = await web_queue.get_pending()
        assert len(pending) == 3
        assert pending[0]["path"] == "/path1"
        assert pending[1]["path"] == "/path2"
        assert pending[2]["path"] == "/path3"

        # Cancel tasks to clean up
        task1.cancel()
        task2.cancel()
        task3.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass
        try:
            await task2
        except asyncio.CancelledError:
            pass
        try:
            await task3
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_false(self, web_queue, config_web_confirm):
        """Approving non-existent request should return False."""
        success = await web_queue.approve("nonexistent-id")
        assert success is False

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_false(self, web_queue, config_web_confirm):
        """Rejecting non-existent request should return False."""
        success = await web_queue.reject("nonexistent-id")
        assert success is False

    @pytest.mark.asyncio
    async def test_request_includes_all_fields(self, web_queue, config_web_confirm):
        """All request fields should be captured in pending request."""

        async def make_request():
            return await web_queue.add_request(
                method="POST",
                path="/calendar/v3/calendars/primary/events",
                query_params={"sendUpdates": "all"},
                message_sender="sender@example.com",
                message_subject="Test Subject",
                draft_thread_id="t123",
                event_summary="Test Meeting",
                event_attendees=["user@example.com"],
                send_updates="all",
            )

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.05)

        pending = await web_queue.get_pending()
        assert len(pending) == 1

        req = pending[0]
        assert req["method"] == "POST"
        assert req["path"] == "/calendar/v3/calendars/primary/events"
        assert req["query_params"] == {"sendUpdates": "all"}
        assert req["message_sender"] == "sender@example.com"
        assert req["message_subject"] == "Test Subject"
        assert req["draft_thread_id"] == "t123"
        assert req["event_summary"] == "Test Meeting"
        assert req["event_attendees"] == ["user@example.com"]
        assert req["send_updates"] == "all"
        assert "created_at" in req
        assert "id" in req

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestOperatorDecisionVsExpiry:
    """Regression tests for expiry racing operator decisions (review findings 1-2)."""

    @pytest.mark.asyncio
    async def test_approve_after_future_cancelled_returns_false(
        self, web_queue, config_web_confirm
    ):
        """An approve landing after the wait expired must not report success.

        Reproduces review finding 1: the expiring wait_for cancels the result
        future; approve() then found the id still queued and returned True -
        HTTP 200 "Request approved" plus a request_approved broadcast - while
        the caller was told EXPIRED and nothing was ever forwarded.
        """
        task = asyncio.create_task(web_queue.add_request(method="POST", path="/test"))
        await asyncio.sleep(0.05)
        pending = await web_queue.get_pending()
        request_id = pending[0]["id"]

        subscriber = web_queue.subscribe()
        # Deterministic stand-in for the race: the expiring wait_for has
        # cancelled the future before the operator's click lands.
        web_queue._by_id[request_id].result_future.cancel()

        success = await web_queue.approve(request_id)
        assert success is False  # routes to 404 "not found or already processed"

        # And no phantom request_approved broadcast
        events = _drain(subscriber)
        assert all(e["event"] != "request_approved" for e in events)

        web_queue.unsubscribe(subscriber)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_reject_after_future_cancelled_returns_false(self, web_queue, config_web_confirm):
        """Mirror of the approve defect for reject()."""
        task = asyncio.create_task(web_queue.add_request(method="POST", path="/test"))
        await asyncio.sleep(0.05)
        pending = await web_queue.get_pending()
        request_id = pending[0]["id"]

        subscriber = web_queue.subscribe()
        web_queue._by_id[request_id].result_future.cancel()

        success = await web_queue.reject(request_id)
        assert success is False

        events = _drain(subscriber)
        assert all(e["event"] != "request_rejected" for e in events)

        web_queue.unsubscribe(subscriber)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_decision_recorded_despite_timeout_is_honored(
        self, web_queue, config_web_confirm, monkeypatch
    ):
        """A decision that made it onto the future must win over the timer.

        Reproduces review finding 2: on Python >= 3.12, asyncio.wait_for no
        longer recovers a done future on timeout - when set_result(APPROVED)
        and the timer callback run in the same event-loop batch, wait_for
        still raises TimeoutError and the recorded approval was discarded as
        EXPIRED (with a spurious "expired" follow-up notification).
        """
        resolved_calls = []
        monkeypatch.setattr(
            notifications,
            "notify_request_resolved",
            lambda pending, outcome: resolved_calls.append(outcome),
        )

        async def wait_for_racing_timer(future, timeout):
            # Same observable state as the 3.12 race: the future holds the
            # operator's decision, yet wait_for raises TimeoutError.
            if not future.done():
                future.set_result(ConfirmationOutcome.APPROVED)
            raise TimeoutError

        monkeypatch.setattr("api_proxy.web_confirmation.asyncio.wait_for", wait_for_racing_timer)

        subscriber = web_queue.subscribe()
        result = await web_queue.add_request(method="POST", path="/test")

        assert result is ConfirmationOutcome.APPROVED
        # Only the matching resolution may fire: no "expired" follow-up
        assert "expired" not in resolved_calls
        # No request_timeout broadcast either
        events = _drain(subscriber)
        assert all(e["event"] != "request_timeout" for e in events)
        # The entry must not leak in the queue
        assert await web_queue.get_pending() == []

        web_queue.unsubscribe(subscriber)

    @pytest.mark.asyncio
    async def test_recovery_returns_recorded_result(self, web_queue):
        future = asyncio.get_running_loop().create_future()
        future.set_result(ConfirmationOutcome.REJECTED)
        assert web_queue._recover_operator_decision(future) is ConfirmationOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_recovery_ignores_cancelled_future(self, web_queue):
        future = asyncio.get_running_loop().create_future()
        future.cancel()
        assert web_queue._recover_operator_decision(future) is None

    @pytest.mark.asyncio
    async def test_recovery_ignores_pending_future(self, web_queue):
        future = asyncio.get_running_loop().create_future()
        assert web_queue._recover_operator_decision(future) is None
        future.cancel()


class TestSSESubscription:
    """Tests for SSE event subscription."""

    @pytest.mark.asyncio
    async def test_subscribe_receives_events(self, web_queue, config_web_confirm):
        """Subscriber should receive events on queue changes."""
        received_events = []

        # Subscribe
        event_queue = web_queue.subscribe()

        # Add a request
        async def make_request():
            return await web_queue.add_request(
                method="GET",
                path="/test",
            )

        task = asyncio.create_task(make_request())
        await asyncio.sleep(0.05)

        # Should have received an event
        try:
            event = event_queue.get_nowait()
            received_events.append(event)
        except asyncio.QueueEmpty:
            pass

        assert len(received_events) >= 1
        assert received_events[0]["event"] == "request_added"
        assert len(received_events[0]["pending"]) == 1

        # Clean up
        web_queue.unsubscribe(event_queue)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestGlobalQueue:
    """Tests for global queue management."""

    def test_get_web_queue_returns_singleton(self):
        """get_web_queue should return the same instance."""
        reset_web_queue()
        queue1 = get_web_queue()
        queue2 = get_web_queue()
        assert queue1 is queue2

    def test_reset_web_queue_creates_new_instance(self):
        """reset_web_queue should create new instance on next call."""
        reset_web_queue()
        queue1 = get_web_queue()
        reset_web_queue()
        queue2 = get_web_queue()
        assert queue1 is not queue2


class TestDashboardStream:
    """The dashboard renders only from the SSE stream (EventSource onmessage).
    stream_events never ran in a test, and neither the approve nor the expiry
    broadcast was asserted: dropping the 'data: ' framing (a blank dashboard,
    so every request expires unseen), or either broadcast (stale cards), passed."""

    async def test_first_frame_is_sse_framed_connected_state(self, web_queue, config_web_confirm):
        stream = web_queue.stream_events()
        try:
            frame = await asyncio.wait_for(stream.__anext__(), 5)
        finally:
            await stream.aclose()
        assert frame.startswith("data: ") and frame.endswith("\n\n")
        assert json.loads(frame[len("data: ") :]) == {"event": "connected", "pending": []}

    @pytest.mark.parametrize(
        "decide,event",
        [("approve", "request_approved"), ("reject", "request_rejected")],
    )
    async def test_operator_decision_is_broadcast_with_the_request_removed(
        self, web_queue, config_web_confirm, decide, event
    ):
        events = web_queue.subscribe()
        task = asyncio.create_task(web_queue.add_request(method="DELETE", path="/x/events/1"))
        added = await asyncio.wait_for(events.get(), 5)
        assert added["event"] == "request_added"
        assert await getattr(web_queue, decide)(added["pending"][0]["id"]) is True
        decided = await asyncio.wait_for(events.get(), 5)
        assert decided == {"event": event, "pending": []}
        await asyncio.wait_for(task, 5)

    async def test_expiry_is_broadcast_with_the_request_removed(
        self, web_queue, api_keys_file, token_file
    ):
        set_config(
            Config(
                api_keys_file=api_keys_file,
                token_file=token_file,
                confirmation_mode=ConfirmationMode.MODIFY,
                confirmation_timeout=0.05,
            )
        )
        events = web_queue.subscribe()
        outcome = await asyncio.wait_for(
            web_queue.add_request(method="DELETE", path="/x/events/1"), 5
        )
        assert outcome is ConfirmationOutcome.EXPIRED
        kinds = []
        while not events.empty():
            kinds.append(events.get_nowait())
        assert kinds[-1] == {"event": "request_timeout", "pending": []}
