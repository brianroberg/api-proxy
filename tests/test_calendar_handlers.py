"""Tests for Google Calendar API handlers."""

from unittest.mock import patch, AsyncMock


class TestListCalendars:
    """Tests for GET /calendar/v3/users/me/calendarList."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_calendar_list
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_calendar_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/users/me/calendarList",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_returns_calendar_list_response(
        self, client, auth_headers, mock_calendar_response, mock_calendar_list
    ):
        """Should return the calendar list from the API."""
        mock_response = mock_calendar_response(200, mock_calendar_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/users/me/calendarList",
                headers=auth_headers,
            )

        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 2


class TestGetCalendar:
    """Tests for GET /calendar/v3/calendars/{calendarId}."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_calendar
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_calendar)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_returns_calendar_response(
        self, client, auth_headers, mock_calendar_response, mock_calendar
    ):
        """Should return the calendar metadata."""
        mock_response = mock_calendar_response(200, mock_calendar)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary",
                headers=auth_headers,
            )

        data = response.json()
        assert data["id"] == "primary"
        assert data["summary"] == "My Calendar"


class TestListEvents:
    """Tests for GET /calendar/v3/calendars/{calendarId}/events."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_events_list
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_events_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_forwards_query_parameters(
        self, client, auth_headers, mock_calendar_response, mock_events_list
    ):
        """Should forward query parameters to Calendar API."""
        mock_response = mock_calendar_response(200, mock_events_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events",
                params={
                    "maxResults": 10,
                    "timeMin": "2025-01-01T00:00:00Z",
                    "singleEvents": True,
                },
                headers=auth_headers,
            )

        # Verify the client was called with correct parameters
        call_args = mock_client.request.call_args
        assert call_args[1]["params"]["maxResults"] == 10
        assert call_args[1]["params"]["timeMin"] == "2025-01-01T00:00:00Z"
        assert response.status_code == 200

    def test_returns_events_list_response(
        self, client, auth_headers, mock_calendar_response, mock_events_list
    ):
        """Should return the events list."""
        mock_response = mock_calendar_response(200, mock_events_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events",
                headers=auth_headers,
            )

        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 2


class TestGetEvent:
    """Tests for GET /calendar/v3/calendars/{calendarId}/events/{eventId}."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_event
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events/event1",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_returns_event_response(
        self, client, auth_headers, mock_calendar_response, mock_event
    ):
        """Should return the event details."""
        mock_response = mock_calendar_response(200, mock_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events/event1",
                headers=auth_headers,
            )

        data = response.json()
        assert data["id"] == "event1"
        assert data["summary"] == "Meeting"
        assert "attendees" in data


class TestCreateEvent:
    """Tests for POST /calendar/v3/calendars/{calendarId}/events."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_created_event
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json={
                    "summary": "New Meeting",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_forwards_request_body(
        self, client, auth_headers, mock_calendar_response, mock_created_event
    ):
        """Should forward request body to Calendar API."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json={
                    "summary": "New Meeting",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        # Verify the client was called with correct body
        call_args = mock_client.request.call_args
        assert call_args[1]["json_body"]["summary"] == "New Meeting"
        assert response.status_code == 200

    def test_returns_created_event(
        self, client, auth_headers, mock_calendar_response, mock_created_event
    ):
        """Should return the created event."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json={
                    "summary": "New Meeting",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        data = response.json()
        assert data["id"] == "newevent123"
        assert data["summary"] == "New Meeting"


class TestUpdateEvent:
    """Tests for PUT /calendar/v3/calendars/{calendarId}/events/{eventId}."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_event
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.put(
                "/calendar/v3/calendars/primary/events/event1",
                json={
                    "summary": "Updated Meeting",
                    "start": {"dateTime": "2025-01-20T10:00:00-05:00"},
                    "end": {"dateTime": "2025-01-20T11:00:00-05:00"},
                },
                headers=auth_headers,
            )

        assert response.status_code == 200


class TestPatchEvent:
    """Tests for PATCH /calendar/v3/calendars/{calendarId}/events/{eventId}."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, mock_event
    ):
        """Should return 200 OK for valid request."""
        mock_response = mock_calendar_response(200, mock_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.patch(
                "/calendar/v3/calendars/primary/events/event1",
                json={"summary": "Patched Meeting"},
                headers=auth_headers,
            )

        assert response.status_code == 200


class TestDeleteEvent:
    """Tests for DELETE /calendar/v3/calendars/{calendarId}/events/{eventId}."""

    def test_returns_correct_status_code(
        self, client, auth_headers, mock_calendar_response, config_no_confirm
    ):
        """Should return 204 No Content for valid request."""
        mock_response = mock_calendar_response(204, None)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.delete(
                "/calendar/v3/calendars/primary/events/event1",
                headers=auth_headers,
            )

        assert response.status_code == 204


class TestCalendarApiErrors:
    """Tests for Calendar API error handling."""

    def test_forwards_404_error(
        self, client, auth_headers, mock_calendar_response
    ):
        """Should forward 404 errors from Calendar API."""
        error_response = {
            "error": {
                "code": 404,
                "message": "Event not found",
            }
        }
        mock_response = mock_calendar_response(404, error_response)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events/nonexistent",
                headers=auth_headers,
            )

        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "backend_error"

    def test_forwards_403_error(
        self, client, auth_headers, mock_calendar_response
    ):
        """Should forward 403 errors from Calendar API."""
        error_response = {
            "error": {
                "code": 403,
                "message": "Access denied",
            }
        }
        mock_response = mock_calendar_response(403, error_response)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/restricted@example.com/events",
                headers=auth_headers,
            )

        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "backend_error"


class TestCalendarIdValidation:
    """Tests for calendarId parameter validation."""

    def test_accepts_primary_as_calendar_id(
        self, client, auth_headers, mock_calendar_response, mock_calendar
    ):
        """Should accept 'primary' as calendarId."""
        mock_response = mock_calendar_response(200, mock_calendar)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary",
                headers=auth_headers,
            )

        assert response.status_code == 200

    def test_accepts_email_as_calendar_id(
        self, client, auth_headers, mock_calendar_response, mock_calendar
    ):
        """Should accept email address as calendarId."""
        mock_response = mock_calendar_response(200, mock_calendar)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/user@example.com",
                headers=auth_headers,
            )

        assert response.status_code == 200


class TestCalendarAuthenticationEnforcement:
    """Tests that authentication is enforced on Calendar endpoints."""

    def test_all_calendar_endpoints_require_auth(self, client):
        """All Calendar endpoints should require authentication."""
        endpoints = [
            ("GET", "/calendar/v3/users/me/calendarList"),
            ("GET", "/calendar/v3/calendars/primary"),
            ("GET", "/calendar/v3/calendars/primary/events"),
            ("GET", "/calendar/v3/calendars/primary/events/event1"),
            ("POST", "/calendar/v3/calendars/primary/events"),
            ("PUT", "/calendar/v3/calendars/primary/events/event1"),
            ("PATCH", "/calendar/v3/calendars/primary/events/event1"),
            ("DELETE", "/calendar/v3/calendars/primary/events/event1"),
        ]

        for method, path in endpoints:
            if method == "GET":
                response = client.get(path)
            elif method == "POST":
                response = client.post(path, json={})
            elif method == "PUT":
                response = client.put(path, json={})
            elif method == "PATCH":
                response = client.patch(path, json={})
            elif method == "DELETE":
                response = client.delete(path)

            assert response.status_code == 401, f"Expected 401 for {method} {path}"
