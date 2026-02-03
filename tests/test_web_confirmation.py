"""Tests for web-based confirmation queue."""

import asyncio

import pytest

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.web_confirmation import (
    PendingRequest,
    WebConfirmationQueue,
    get_web_queue,
    reset_web_queue,
)


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
        assert result is True

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
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, web_queue, config_web_confirm):
        """Request should return False if it times out."""
        result = await web_queue.add_request(
            method="POST",
            path="/gmail/v1/users/me/messages/123/modify",
        )

        # Should timeout and return False
        assert result is False

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
