"""Tests for the RSVP (respond) endpoint and its pure attendee transform."""

from unittest.mock import AsyncMock, patch

from api_proxy.calendar.handlers import build_rsvp_attendees


class TestBuildRsvpAttendees:
    """Pure transform: set only the self attendee's responseStatus."""

    def test_sets_self_status_and_preserves_others(self):
        attendees = [
            {"email": "org@example.com", "responseStatus": "accepted", "organizer": True},
            {"email": "other@example.com", "responseStatus": "tentative", "optional": True},
            {"email": "me@example.com", "responseStatus": "needsAction", "self": True},
        ]
        result = build_rsvp_attendees(attendees, "accepted")
        by_email = {a["email"]: a for a in result}
        assert by_email["me@example.com"]["responseStatus"] == "accepted"
        assert by_email["other@example.com"]["responseStatus"] == "tentative"
        assert by_email["other@example.com"]["optional"] is True
        assert by_email["org@example.com"]["responseStatus"] == "accepted"

    def test_strips_readonly_self_and_organizer_flags(self):
        attendees = [
            {"email": "org@example.com", "responseStatus": "accepted", "organizer": True},
            {"email": "me@example.com", "responseStatus": "needsAction", "self": True},
        ]
        result = build_rsvp_attendees(attendees, "declined")
        for a in result:
            assert "self" not in a
            assert "organizer" not in a

    def test_returns_none_when_no_self_attendee(self):
        attendees = [{"email": "someone@example.com", "responseStatus": "accepted"}]
        assert build_rsvp_attendees(attendees, "accepted") is None

    def test_returns_none_for_empty_or_missing(self):
        assert build_rsvp_attendees([], "accepted") is None
        assert build_rsvp_attendees(None, "accepted") is None


def _event_with_self(status="needsAction"):
    return {
        "id": "e1",
        "summary": "GMDM Directors Meeting",
        "attendees": [
            {"email": "org@example.com", "responseStatus": "accepted", "organizer": True},
            {"email": "me@example.com", "responseStatus": status, "self": True},
        ],
    }


RESPOND_PATH = "/calendar/v3/calendars/robergb@dm.org/events/e1/respond"


class TestRespondEndpoint:
    """POST /calendar/v3/calendars/{id}/events/{id}/respond"""

    def test_accepts_and_forwards_200(self, client, auth_headers, mock_calendar_response):
        get_resp = mock_calendar_response(200, _event_with_self("needsAction"))
        patch_resp = mock_calendar_response(200, _event_with_self("accepted"))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, patch_resp])
            mock_get.return_value = mock_client
            resp = client.post(RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"})

        assert resp.status_code == 200
        # Second backend call is the PATCH; verify it sets self=accepted and sendUpdates=none
        patch_call = mock_client.request.await_args_list[1]
        assert patch_call.args[0] == "PATCH"
        assert patch_call.kwargs["params"] == {"sendUpdates": "none"}
        sent = {a["email"]: a for a in patch_call.kwargs["json_body"]["attendees"]}
        assert sent["me@example.com"]["responseStatus"] == "accepted"
        assert sent["org@example.com"]["responseStatus"] == "accepted"  # preserved

    def test_rejects_invalid_status_without_backend_call(self, client, auth_headers):
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            resp = client.post(RESPOND_PATH, headers=auth_headers, json={"responseStatus": "maybe"})
        assert resp.status_code == 400
        mock_client.request.assert_not_called()

    def test_no_self_attendee_returns_400(self, client, auth_headers, mock_calendar_response):
        event = {"id": "e1", "attendees": [{"email": "x@example.com", "responseStatus": "accepted"}]}
        get_resp = mock_calendar_response(200, event)
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"})
        assert resp.status_code == 400
        assert mock_client.request.await_count == 1  # only the GET, no PATCH

    def test_event_not_found_returns_404(self, client, auth_headers, mock_calendar_response):
        get_resp = mock_calendar_response(404, {"error": {"message": "Not Found"}})
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp])
            mock_get.return_value = mock_client
            resp = client.post(RESPOND_PATH, headers=auth_headers, json={"responseStatus": "accepted"})
        assert resp.status_code == 404

    def test_cannot_smuggle_attendees_via_body(self, client, auth_headers, mock_calendar_response):
        """A smuggled 'attendees' field in the body is ignored: the patch is built
        from the event's own attendees, so no attendee injection can occur."""
        get_resp = mock_calendar_response(200, _event_with_self("needsAction"))
        patch_resp = mock_calendar_response(200, _event_with_self("accepted"))
        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=[get_resp, patch_resp])
            mock_get.return_value = mock_client
            resp = client.post(
                RESPOND_PATH,
                headers=auth_headers,
                json={
                    "responseStatus": "accepted",
                    "attendees": [{"email": "victim@example.com", "responseStatus": "accepted"}],
                },
            )
        assert resp.status_code == 200
        patch_call = mock_client.request.await_args_list[1]
        sent_emails = {a["email"] for a in patch_call.kwargs["json_body"]["attendees"]}
        assert "victim@example.com" not in sent_emails
        assert sent_emails == {"org@example.com", "me@example.com"}
        assert patch_call.kwargs["params"]["sendUpdates"] == "none"
