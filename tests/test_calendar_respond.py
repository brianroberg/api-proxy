"""Tests for the RSVP (respond) endpoint and its pure attendee transform."""

import json
from unittest.mock import AsyncMock, patch

import httpx

from api_proxy.calendar.handlers import build_rsvp_attendees


class TestBuildRsvpAttendees:
    """Pure transform: find the caller's own attendee entry by email.

    The Calendar API's ``self`` flag marks whichever attendee owns the
    calendar an event copy sits on -- on a shared or delegated calendar
    that is not the authenticated caller -- so it must never be used to
    pick the entry that gets patched.
    """

    def test_returns_single_entry_with_only_email_and_status(self):
        attendees = [
            {"email": "org@example.com", "responseStatus": "accepted", "organizer": True},
            {
                "email": "me@example.com",
                "responseStatus": "needsAction",
                "self": True,
                "displayName": "Me",
            },
        ]
        result = build_rsvp_attendees(attendees, "me@example.com", "declined")
        assert result == [{"email": "me@example.com", "responseStatus": "declined"}]

    def test_case_insensitive_email_match(self):
        attendees = [{"email": "me@example.com", "responseStatus": "needsAction"}]
        result = build_rsvp_attendees(attendees, "Me@Example.COM", "accepted")
        assert result == [{"email": "me@example.com", "responseStatus": "accepted"}]

    def test_unicode_lookalike_email_is_not_conflated(self):
        """SECURITY: only ASCII letters are case-folded. Full Unicode folding
        would equate distinct addresses (straße@ folds to strasse@), letting a
        crafted attendee entry capture the caller's RSVP patch."""
        attendees = [{"email": "straße@example.com", "responseStatus": "needsAction"}]
        assert build_rsvp_attendees(attendees, "strasse@example.com", "declined") is None
        attendees = [{"email": "strasse@example.com", "responseStatus": "needsAction"}]
        assert build_rsvp_attendees(attendees, "straße@example.com", "declined") is None

    def test_self_flag_on_a_different_email_is_not_selected(self):
        """SECURITY: a `self`-flagged attendee with another email is ignored."""
        attendees = [
            {"email": "colleague@example.com", "responseStatus": "accepted", "self": True},
            {"email": "org@example.com", "responseStatus": "needsAction"},
        ]
        assert build_rsvp_attendees(attendees, "me@example.com", "accepted") is None

    def test_returns_none_for_empty_or_missing_attendees(self):
        assert build_rsvp_attendees([], "me@example.com", "accepted") is None
        assert build_rsvp_attendees(None, "me@example.com", "accepted") is None

    def test_returns_none_for_empty_or_missing_user_email(self):
        attendees = [{"email": "me@example.com", "responseStatus": "needsAction"}]
        assert build_rsvp_attendees(attendees, "", "accepted") is None
        assert build_rsvp_attendees(attendees, None, "accepted") is None

    def test_tolerates_attendee_entries_missing_email(self):
        attendees = [
            {"responseStatus": "accepted"},
            {"email": "me@example.com", "responseStatus": "needsAction"},
        ]
        result = build_rsvp_attendees(attendees, "me@example.com", "declined")
        assert result == [{"email": "me@example.com", "responseStatus": "declined"}]


def _event(attendees, event_id="e1", summary="Team Sync"):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": "2026-07-15T10:00:00-04:00"},
        "end": {"dateTime": "2026-07-15T11:00:00-04:00"},
        "attendees": attendees,
    }


def _default_attendees():
    """Organizer already accepted; caller still needs to respond."""
    return [
        {"email": "org@example.com", "responseStatus": "accepted", "organizer": True},
        {"email": "me@example.com", "responseStatus": "needsAction", "self": True},
    ]


RESPOND_PATH = "/calendar/v3/calendars/primary/events/e1/respond"


class TestRespondEndpoint:
    """POST /calendar/v3/calendars/{calendarId}/events/{eventId}/respond"""

    def test_declines_and_patches_only_the_caller_entry(
        self, client, auth_headers, mock_calendar_response
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        patch_resp = mock_calendar_response(200, _event(_default_attendees()))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp, patch_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "declined"}
            )

        assert resp.status_code == 200
        assert mock_client.request.await_count == 3
        get_call, primary_call, patch_call = mock_client.request.await_args_list
        assert get_call.args == ("GET", "/calendars/primary/events/e1")
        assert primary_call.args == ("GET", "/calendars/primary")
        assert patch_call.args == ("PATCH", "/calendars/primary/events/e1")
        assert patch_call.kwargs["params"] == {"sendUpdates": "none"}
        assert patch_call.kwargs["json_body"] == {
            "attendees": [{"email": "me@example.com", "responseStatus": "declined"}],
            "attendeesOmitted": True,
        }

    def test_invalid_response_status_returns_422_with_no_backend_calls(self, client, auth_headers):
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            resp = client.post(RESPOND_PATH, headers=auth_headers, json={"responseStatus": "maybe"})

        assert resp.status_code == 422
        assert resp.json()["error"] == "proxy_error"
        mock_client.request.assert_not_called()

    def test_event_not_found_returns_404(self, client, auth_headers, mock_calendar_response):
        get_resp = mock_calendar_response(404, {"error": {"message": "Not Found"}})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 404
        assert resp.json()["error"] == "backend_error"
        assert mock_client.request.await_count == 1

    def test_event_with_no_attendees_returns_400(
        self, client, auth_headers, mock_calendar_response
    ):
        get_resp = mock_calendar_response(200, _event([]))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "proxy_error"
        assert mock_client.request.await_count == 1

    def test_not_an_attendee_returns_400_after_resolving_caller(
        self, client, auth_headers, mock_calendar_response
    ):
        attendees = [{"email": "org@example.com", "responseStatus": "accepted", "organizer": True}]
        get_resp = mock_calendar_response(200, _event(attendees))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "proxy_error"
        assert mock_client.request.await_count == 2

    def test_self_flag_on_shared_calendar_does_not_redirect_the_patch(
        self, client, auth_headers, mock_calendar_response
    ):
        """SECURITY regression (finding #2): on a shared calendar the `self`
        flag marks the calendar owner, not the caller. The caller
        (me@example.com, resolved via /calendars/primary) is also a plain
        attendee here, and only their entry may be patched."""
        attendees = [
            {"email": "colleague@example.com", "responseStatus": "accepted", "self": True},
            {"email": "me@example.com", "responseStatus": "needsAction"},
        ]
        get_resp = mock_calendar_response(200, _event(attendees))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        patch_resp = mock_calendar_response(200, _event(attendees))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp, patch_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 200
        patch_call = mock_client.request.await_args_list[2]
        assert patch_call.kwargs["json_body"]["attendees"] == [
            {"email": "me@example.com", "responseStatus": "accepted"}
        ]

    def test_self_flag_present_but_caller_not_invited_returns_400(
        self, client, auth_headers, mock_calendar_response
    ):
        """Same shared-calendar shape, but the caller isn't on the guest
        list at all: the `self`-flagged colleague must not be mistaken
        for the caller."""
        attendees = [
            {"email": "colleague@example.com", "responseStatus": "accepted", "self": True},
        ]
        get_resp = mock_calendar_response(200, _event(attendees))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 400
        assert mock_client.request.await_count == 2

    def test_cannot_smuggle_attendees_via_body(self, client, auth_headers, mock_calendar_response):
        """A smuggled 'attendees' field in the body is ignored: the patch is
        built from the event's own attendees, so no attendee injection can
        occur."""
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        patch_resp = mock_calendar_response(200, _event(_default_attendees()))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp, patch_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH,
                headers=auth_headers,
                json={
                    "responseStatus": "declined",
                    "attendees": [{"email": "victim@example.com", "responseStatus": "accepted"}],
                },
            )

        assert resp.status_code == 200
        patch_call = mock_client.request.await_args_list[2]
        sent_emails = {a["email"] for a in patch_call.kwargs["json_body"]["attendees"]}
        assert sent_emails == {"me@example.com"}

    def test_transport_error_during_event_fetch_returns_502(self, client, auth_headers):
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "backend_error"

    def test_transport_error_during_patch_returns_502(
        self, client, auth_headers, mock_calendar_response
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(
                side_effect=[get_resp, primary_resp, httpx.ConnectError("reset")]
            )
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "declined"}
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "backend_error"

    def test_transport_error_during_primary_resolution_returns_502(
        self, client, auth_headers, mock_calendar_response
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, httpx.ConnectError("reset")])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "backend_error"

    def test_non_json_event_body_returns_502(self, client, auth_headers, mock_calendar_response):
        get_resp = mock_calendar_response(200, {})
        get_resp.json.side_effect = json.JSONDecodeError("x", "y", 0)
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "backend_error"

    def test_non_dict_json_event_body_returns_502(
        self, client, auth_headers, mock_calendar_response
    ):
        """A 200 body that parses as JSON but is not an object must still
        yield a tagged 502, not an untagged 500."""
        get_resp = mock_calendar_response(200, {})
        get_resp.json.return_value = ["weird"]
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 502
        assert resp.json()["error"] == "backend_error"

    def test_primary_calendar_resolution_failure_returns_502(
        self, client, auth_headers, mock_calendar_response
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(500, {"error": {"message": "Server Error"}})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"}
            )

        assert resp.status_code == 502
        assert "resolv" in resp.json()["message"].lower()


class TestRespondConfirmation:
    """Confirmation gating for the RSVP write (finding #1)."""

    def test_approved_confirmation_allows_the_patch(
        self, client, auth_headers, mock_calendar_response, config_confirm_modify
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        patch_resp = mock_calendar_response(200, _event(_default_attendees()))
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp, patch_resp])
            mock_get_client.return_value = mock_client

            mock_handler = AsyncMock()
            mock_handler.confirm = AsyncMock(return_value=True)
            mock_get_handler.return_value = mock_handler

            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "declined"}
            )

        assert resp.status_code == 200
        assert mock_client.request.await_count == 3
        mock_handler.confirm.assert_awaited_once()
        confirmation_request = mock_handler.confirm.await_args.args[0]
        assert confirmation_request.method == "POST"
        assert confirmation_request.path.endswith("/respond")
        assert confirmation_request.rsvp_response == "declined"
        assert confirmation_request.event_summary  # is-modify context is populated

    def test_rejected_confirmation_returns_403_without_patching(
        self, client, auth_headers, mock_calendar_response, config_confirm_modify
    ):
        get_resp = mock_calendar_response(200, _event(_default_attendees()))
        primary_resp = mock_calendar_response(200, {"id": "me@example.com"})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, primary_resp])
            mock_get_client.return_value = mock_client

            mock_handler = AsyncMock()
            mock_handler.confirm = AsyncMock(return_value=False)
            mock_get_handler.return_value = mock_handler

            resp = client.post(
                RESPOND_PATH, headers=auth_headers, json={"responseStatus": "declined"}
            )

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        assert mock_client.request.await_count == 2
