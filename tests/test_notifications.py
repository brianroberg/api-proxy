"""Tests for ntfy approval notifications (issue #5)."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock

import pytest

from api_proxy import notifications
from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import ConfirmationOutcome
from api_proxy.web_confirmation import PendingRequest, WebConfirmationQueue


@pytest.fixture
def web_queue():
    """Fresh queue for each test."""
    return WebConfirmationQueue()


@pytest.fixture
def config_web_confirm(api_keys_file, token_file):
    """Web-confirmation config with a known external base URL."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.MODIFY,
        confirmation_timeout=1.0,
        web_confirmation=True,
        external_base_url="https://proxy.example.com",
    )
    set_config(config)
    return config


@pytest.fixture
def ntfy_send(monkeypatch):
    """Enable notifications and capture sends instead of hitting the network."""
    monkeypatch.setenv("NTFY_TOKEN", "test-token-123")
    send_mock = AsyncMock()
    monkeypatch.setattr(notifications, "_send", send_mock)
    notifications.reset_notification_state()
    yield send_mock
    notifications.reset_notification_state()


def _pending(**overrides) -> PendingRequest:
    """Build a PendingRequest with calendar-ish defaults."""
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
    finally:
        loop.close()
    fields = {
        "id": "req-123",
        "method": "DELETE",
        "path": "/calendars/primary/events/event1",
        "query_params": None,
        "labels_to_add": None,
        "labels_to_remove": None,
        "message_sender": None,
        "message_subject": None,
        "draft_thread_id": None,
        "event_summary": "Team Sync",
        "event_attendees": None,
        "send_updates": None,
        "event_start": None,
        "event_end": None,
        "rsvp_response": None,
        "created_at": time.time(),
        "result_future": future,
    }
    fields.update(overrides)
    return PendingRequest(**fields)


class TestNotifyOnEnqueue:
    """A queued request must push a notification without blocking approval."""

    async def test_notification_fires_when_request_is_queued(
        self, web_queue, config_web_confirm, ntfy_send
    ):
        task = asyncio.create_task(
            web_queue.add_request(
                method="DELETE",
                path="/calendars/primary/events/event1",
                event_summary="Team Sync",
            )
        )
        await asyncio.sleep(0.05)

        assert ntfy_send.await_count == 1
        url, headers, body = ntfy_send.await_args.args
        assert url == config_web_confirm.ntfy_url
        assert headers["Authorization"] == "Bearer test-token-123"
        assert headers["Priority"] == "high"
        assert "Team Sync" in headers["Title"]
        assert "DELETE /calendars/primary/events/event1" in body
        assert "Expires at" in body
        assert "expire" in body  # says what happens on no action

        # Deep link to this exact request on the dashboard
        pending = await web_queue.get_pending()
        request_id = pending[0]["id"]
        assert headers["Actions"] == (
            f"view, Review, https://proxy.example.com/approval/#{request_id}"
        )

        await web_queue.approve(request_id)
        assert await task is ConfirmationOutcome.APPROVED

    async def test_send_failure_does_not_affect_approval_result(
        self, web_queue, config_web_confirm, ntfy_send
    ):
        ntfy_send.side_effect = RuntimeError("ntfy is down")

        task = asyncio.create_task(
            web_queue.add_request(method="POST", path="/calendars/primary/events")
        )
        await asyncio.sleep(0.05)

        pending = await web_queue.get_pending()
        assert len(pending) == 1  # still queued despite the failed send
        await web_queue.approve(pending[0]["id"])
        assert await task is ConfirmationOutcome.APPROVED

    async def test_resolution_follow_up_on_approve(self, web_queue, config_web_confirm, ntfy_send):
        task = asyncio.create_task(
            web_queue.add_request(
                method="DELETE",
                path="/calendars/primary/events/event1",
                event_summary="Team Sync",
            )
        )
        await asyncio.sleep(0.05)
        pending = await web_queue.get_pending()
        await web_queue.approve(pending[0]["id"])
        assert await task is ConfirmationOutcome.APPROVED
        await asyncio.sleep(0.05)

        assert ntfy_send.await_count == 2
        _, headers, body = ntfy_send.await_args_list[1].args
        assert headers["Title"].startswith("Approved:")
        assert headers["Priority"] == "low"
        assert "forwarded to the backend" in body

    async def test_resolution_follow_up_on_expiry(
        self, web_queue, config_web_confirm, ntfy_send, api_keys_file, token_file
    ):
        config = Config(
            api_keys_file=api_keys_file,
            token_file=token_file,
            confirmation_mode=ConfirmationMode.MODIFY,
            confirmation_timeout=0.1,
            web_confirmation=True,
            external_base_url="https://proxy.example.com",
        )
        set_config(config)

        result = await web_queue.add_request(
            method="DELETE",
            path="/calendars/primary/events/event1",
            event_summary="Team Sync",
        )
        assert result is ConfirmationOutcome.EXPIRED
        await asyncio.sleep(0.05)

        assert ntfy_send.await_count == 2
        _, headers, body = ntfy_send.await_args_list[1].args
        assert headers["Title"].startswith("Expired:")
        assert "no operator response" in body
        assert "told it expired" in body


class TestNotificationPrivacy:
    """No third-party personal data may reach ntfy."""

    def test_pending_notification_contains_no_attendee_or_sender_data(self, config_web_confirm):
        pending = _pending(
            event_attendees=["alice@example.com", "bob@example.com"],
            message_sender="carol@example.com",
        )

        headers, body = notifications.build_pending_notification(pending)

        blob = body + " ".join(headers.values())
        assert "alice@example.com" not in blob
        assert "bob@example.com" not in blob
        assert "carol@example.com" not in blob
        # The user's own event summary is fine and expected
        assert "Team Sync" in headers["Title"]

    def test_resolution_notification_contains_no_attendee_or_sender_data(self, config_web_confirm):
        pending = _pending(
            event_attendees=["alice@example.com"],
            message_sender="carol@example.com",
        )

        headers, body = notifications.build_resolution_notification(pending, "rejected")

        blob = body + " ".join(headers.values())
        assert "alice@example.com" not in blob
        assert "carol@example.com" not in blob

    def test_title_is_header_safe(self, config_web_confirm):
        """Newlines in a summary must not become header injection."""
        pending = _pending(event_summary="Evil\nX-Injected: yes")

        headers, _ = notifications.build_pending_notification(pending)

        assert "\n" not in headers["Title"]
        assert "\r" not in headers["Title"]

    def test_non_latin1_title_is_encoded(self, config_web_confirm):
        pending = _pending(event_summary="Café ☕ planning")

        headers, _ = notifications.build_pending_notification(pending)

        # Must be encodable as an HTTP header
        headers["Title"].encode("latin-1")


class TestTokenHandling:
    """NTFY_TOKEN unset disables notifications cleanly."""

    async def test_unset_token_disables_and_logs_once(
        self, web_queue, config_web_confirm, monkeypatch, caplog
    ):
        monkeypatch.delenv("NTFY_TOKEN", raising=False)
        send_mock = AsyncMock()
        monkeypatch.setattr(notifications, "_send", send_mock)
        notifications.reset_notification_state()

        with caplog.at_level(logging.INFO, logger="api_proxy.notifications"):
            notifications.notify_request_pending(_pending())
            notifications.notify_request_pending(_pending(id="req-456"))
            await asyncio.sleep(0.05)

        send_mock.assert_not_awaited()
        disabled_logs = [r for r in caplog.records if "NTFY_TOKEN" in r.getMessage()]
        assert len(disabled_logs) == 1
        notifications.reset_notification_state()

    def test_token_never_appears_in_built_notification(self, config_web_confirm, monkeypatch):
        monkeypatch.setenv("NTFY_TOKEN", "super-secret-token")

        headers, body = notifications.build_pending_notification(_pending())

        blob = body + " ".join(f"{k}: {v}" for k, v in headers.items())
        assert "super-secret-token" not in blob


class TestDescribeRequest:
    """Titles say what is being approved without leaking who is involved."""

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            (
                {"method": "DELETE", "path": "/calendars/primary/events/e1"},
                "Delete event: Team Sync",
            ),
            (
                {"method": "POST", "path": "/calendars/primary/events"},
                "Create event: Team Sync",
            ),
            (
                {"method": "PATCH", "path": "/calendars/primary/events/e1"},
                "Update event: Team Sync",
            ),
            (
                {
                    "method": "POST",
                    "path": "/calendars/primary/events/e1/respond",
                    "rsvp_response": "declined",
                },
                "RSVP (declined) to event: Team Sync",
            ),
            (
                {
                    "method": "POST",
                    "path": "/gmail/v1/users/me/messages/m1/trash",
                    "event_summary": None,
                    "message_subject": "Lunch plans",
                },
                "Trash message: Lunch plans",
            ),
            (
                {
                    "method": "POST",
                    "path": "/gmail/v1/users/me/drafts",
                    "event_summary": None,
                },
                "Create draft",
            ),
        ],
    )
    def test_descriptions(self, config_web_confirm, overrides, expected):
        assert notifications.describe_request(_pending(**overrides)) == expected
