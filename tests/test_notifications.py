"""Tests for ntfy approval notifications (issue #5)."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from api_proxy import notifications
from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import ConfirmationOutcome

# The autouse conftest fixture replaces notifications._send per test; this
# module-import-time reference keeps the real function reachable for tests
# that exercise its own error handling.
from api_proxy.notifications import _send as real_send
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

    def test_non_ascii_title_is_encoded(self, config_web_confirm):
        pending = _pending(event_summary="Café ☕ planning")

        headers, _ = notifications.build_pending_notification(pending)

        # httpx encodes header values as ASCII; anything beyond must be
        # RFC 2047-encoded or the send raises and is silently dropped.
        assert headers["Title"].isascii()
        httpx.Headers(headers)  # raises UnicodeEncodeError if not ASCII-safe

    def test_latin1_but_non_ascii_title_is_encoded(self, config_web_confirm):
        """Review finding 3: é is Latin-1, so the old Latin-1 threshold let it
        through raw and httpx raised UnicodeEncodeError (notification dropped)."""
        pending = _pending(event_summary="Café planning")

        headers, _ = notifications.build_pending_notification(pending)

        assert headers["Title"].isascii()
        assert headers["Title"].startswith("=?UTF-8?B?")
        httpx.Headers(headers)

    def test_control_characters_are_stripped(self, config_web_confirm):
        """Review finding 3: C0/C1 control chars survived the whitespace
        collapse and made h11 reject the header (notification dropped)."""
        pending = _pending(event_summary="Bell\x07 and \x9c control")

        headers, _ = notifications.build_pending_notification(pending)

        title = headers["Title"]
        assert all(ch.isprintable() for ch in title)
        assert "Bell and control" in title
        httpx.Headers(headers)


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

    async def test_trailing_newline_token_is_stripped(self, config_web_confirm, monkeypatch):
        """Review finding 4: an echo-created token ends in a newline; sent raw
        it makes h11 reject the header - and the failure log embedded the full
        token. The token must be stripped on read and the send succeed."""
        monkeypatch.setenv("NTFY_TOKEN", "tok-abc\n")
        send_mock = AsyncMock()
        monkeypatch.setattr(notifications, "_send", send_mock)
        notifications.reset_notification_state()

        notifications.notify_request_pending(_pending())
        await asyncio.sleep(0.05)

        assert send_mock.await_count == 1
        _, headers, _ = send_mock.await_args.args
        assert headers["Authorization"] == "Bearer tok-abc"
        httpx.Headers(headers)  # header-legal after stripping
        notifications.reset_notification_state()

    async def test_invalid_token_disables_and_never_logs_value(
        self, config_web_confirm, monkeypatch, caplog
    ):
        """A token that can't legally sit in a header disables notifications
        with a log-once message that never contains the token value."""
        monkeypatch.setenv("NTFY_TOKEN", "bad\ntoken-value")
        send_mock = AsyncMock()
        monkeypatch.setattr(notifications, "_send", send_mock)
        notifications.reset_notification_state()

        with caplog.at_level(logging.DEBUG):
            notifications.notify_request_pending(_pending())
            notifications.notify_request_pending(_pending(id="req-456"))
            await asyncio.sleep(0.05)

        send_mock.assert_not_awaited()
        assert "token-value" not in caplog.text
        disabled_logs = [r for r in caplog.records if "disabled" in r.getMessage()]
        assert len(disabled_logs) == 1
        notifications.reset_notification_state()

    async def test_send_exception_text_never_leaks_token(
        self, config_web_confirm, monkeypatch, caplog
    ):
        """Even if the transport raises with the header value embedded (h11's
        'Illegal header value b...' style), the log must not carry the token."""

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, content=None, headers=None):
                auth = headers["Authorization"].encode()
                raise ValueError(f"Illegal header value {auth!r}")

        monkeypatch.setattr(notifications.httpx, "AsyncClient", FakeClient)

        with caplog.at_level(logging.WARNING):
            await real_send(
                "https://ntfy.example.com/topic",
                {"Authorization": "Bearer secret-tok", "Title": "x"},
                "body",
            )

        assert "ntfy notification failed" in caplog.text
        assert "secret-tok" not in caplog.text


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


SENDER = "sentinel-sender@example.com"
ATTENDEE = "sentinel-attendee@example.com"


class TestNoPersonalDataInAnyTitleBranch:
    """describe_request builds the push Title per kind of request. The privacy
    tests above use a calendar event, which returns from the '/events' branch
    before the Gmail and RSVP branches run, so adding the sender to a trash
    title, or attendees to an RSVP title, passed. Every branch is exercised
    here with a sender and attendees present."""

    @pytest.mark.parametrize(
        "method,path,extra",
        [
            ("DELETE", "/calendar/v3/calendars/primary/events/e1", {"event_summary": "Sync"}),
            (
                "POST",
                "/calendar/v3/calendars/primary/events/e1/respond",
                {"event_summary": "Sync", "rsvp_response": "accepted"},
            ),
            ("POST", "/gmail/v1/users/me/messages/m1/trash", {"message_subject": "Hello"}),
            ("POST", "/gmail/v1/users/me/messages/m1/untrash", {"message_subject": "Hello"}),
            ("POST", "/gmail/v1/users/me/messages/m1/modify", {"message_subject": "Hello"}),
            ("POST", "/gmail/v1/users/me/drafts", {}),
            ("GET", "/gmail/v1/users/me/labels", {}),
        ],
    )
    @pytest.mark.parametrize("outcome", [None, "approved", "rejected", "expired"])
    def test_no_sender_or_attendee_anywhere(self, config_web_confirm, method, path, extra, outcome):
        fields = {"event_summary": None, **extra}
        pending = _pending(
            method=method,
            path=path,
            message_sender=SENDER,
            event_attendees=[ATTENDEE],
            **fields,
        )
        if outcome is None:
            headers, body = notifications.build_pending_notification(pending)
        else:
            headers, body = notifications.build_resolution_notification(pending, outcome)
        blob = body + " ".join(headers.values())
        assert SENDER not in blob
        assert ATTENDEE not in blob


class TestResolutionFollowUpOnReject:
    async def test_reject_pushes_a_rejected_follow_up(
        self, web_queue, config_web_confirm, ntfy_send
    ):
        """The approve and expiry follow-ups were pinned; a reject that pushed
        'Approved: ... forwarded to the backend' to the phone passed."""
        task = asyncio.create_task(
            web_queue.add_request(
                method="DELETE",
                path="/calendars/primary/events/event1",
                event_summary="Team Sync",
            )
        )
        await asyncio.sleep(0.05)
        pending = await web_queue.get_pending()
        await web_queue.reject(pending[0]["id"])
        assert await task is ConfirmationOutcome.REJECTED
        await asyncio.sleep(0.05)

        assert ntfy_send.await_count == 2
        _, headers, body = ntfy_send.await_args_list[1].args
        assert headers["Title"].startswith("Rejected:")
        assert "forwarded" not in body
