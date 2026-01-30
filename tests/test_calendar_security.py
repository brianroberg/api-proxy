"""Security tests for Google Calendar API proxy."""

from unittest.mock import patch, AsyncMock, MagicMock


class TestCalendarAllowlistApproach:
    """Verify Calendar operations follow allowlist approach."""

    def test_unknown_calendar_endpoint_blocked(self, client, auth_headers):
        """Unknown Calendar endpoints should return 403."""
        response = client.get(
            "/calendar/v3/calendars/primary/unknown",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_calendar_settings_not_allowed(self, client, auth_headers):
        """GET settings should not be allowed (not in allowlist)."""
        response = client.get(
            "/calendar/v3/users/me/settings",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_create_calendar_not_allowed(self, client, auth_headers):
        """POST to create calendar should not be allowed."""
        response = client.post(
            "/calendar/v3/calendars",
            json={"summary": "New Calendar"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_delete_calendar_not_allowed(self, client, auth_headers):
        """DELETE calendar should not be allowed."""
        response = client.delete(
            "/calendar/v3/calendars/calendar123",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_acl_not_allowed(self, client, auth_headers):
        """ACL operations should not be allowed."""
        response = client.get(
            "/calendar/v3/calendars/primary/acl",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_freebusy_not_allowed(self, client, auth_headers):
        """FreeBusy query should not be allowed."""
        response = client.post(
            "/calendar/v3/freeBusy",
            json={"items": [{"id": "primary"}]},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestCalendarConfirmationModes:
    """Test confirmation behavior for Calendar operations."""

    def test_read_operation_no_confirm_with_modify_mode(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_events_list
    ):
        """Read operations should not require confirmation in modify mode."""
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

        # Should succeed without confirmation
        assert response.status_code == 200

    def test_create_event_no_confirm_without_invitations(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_created_event
    ):
        """Creating event without sendUpdates should not require confirmation."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            # Create event without sendUpdates (no invitations)
            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json={
                    "summary": "New Meeting",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        # Should succeed without confirmation
        assert response.status_code == 200


class TestDeleteEventConfirmation:
    """Tests for DELETE event confirmation behavior."""

    def test_delete_requires_confirmation_in_modify_mode(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response
    ):
        """DELETE should require confirmation in modify mode."""
        mock_response = mock_calendar_response(204, None)

        # Mock stdin to return 'n' (reject)
        with patch("sys.stdin.readline", return_value="n\n"), \
             patch(
                 "api_proxy.calendar.handlers.get_calendar_client"
             ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.delete(
                "/calendar/v3/calendars/primary/events/event1",
                headers=auth_headers,
            )

        # Should be rejected (403) because operator said no
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "forbidden"

    def test_delete_no_confirm_in_none_mode(
        self, client, auth_headers, config_no_confirm, mock_calendar_response
    ):
        """DELETE should work without confirmation in none mode."""
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


class TestInvitationConfirmation:
    """Tests for invitation (sendUpdates) confirmation behavior."""

    def test_create_with_send_updates_all_requires_confirmation(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_created_event
    ):
        """Creating event with sendUpdates=all should require confirmation."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'n' (reject)
        with patch("sys.stdin.readline", return_value="n\n"), \
             patch(
                 "api_proxy.calendar.handlers.get_calendar_client"
             ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events?sendUpdates=all",
                json={
                    "summary": "Meeting with invitations",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                    "attendees": [{"email": "guest@example.com"}],
                },
                headers=auth_headers,
            )

        # Should be rejected because operator said no
        assert response.status_code == 403

    def test_create_with_send_updates_none_no_confirmation(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_created_event
    ):
        """Creating event with sendUpdates=none should not require confirmation."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events?sendUpdates=none",
                json={
                    "summary": "Meeting without invitations",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        # Should succeed without confirmation
        assert response.status_code == 200

    def test_update_with_send_updates_external_only_requires_confirmation(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_event
    ):
        """Updating event with sendUpdates=externalOnly should require confirmation."""
        mock_response = mock_calendar_response(200, mock_event)

        # Mock stdin to return 'n' (reject)
        with patch("sys.stdin.readline", return_value="n\n"), \
             patch(
                 "api_proxy.calendar.handlers.get_calendar_client"
             ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.put(
                "/calendar/v3/calendars/primary/events/event1?sendUpdates=externalOnly",
                json={
                    "summary": "Updated Meeting",
                    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
                    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
                },
                headers=auth_headers,
            )

        # Should be rejected because operator said no
        assert response.status_code == 403


class TestCalendarBypassAttempts:
    """Test attempted security bypasses for Calendar endpoints."""

    def test_case_variation_blocked_by_routing(self, client, auth_headers):
        """Case variations are blocked by FastAPI's case-sensitive routing.

        Note: The middleware checks paths case-insensitively for the allowlist,
        but FastAPI's router is case-sensitive. So "/calendar/v3/CALENDARS/primary"
        passes the middleware but gets 404 from the router.

        This is acceptable security behavior - case variations don't bypass security,
        they simply don't match any route.
        """
        response = client.get(
            "/calendar/v3/CALENDARS/primary",
            headers=auth_headers,
        )
        # FastAPI returns 404 because no route matches the uppercase path
        # This is NOT a security bypass - the request fails
        assert response.status_code == 404

    def test_trailing_slash_calendar(self, client, auth_headers, mock_calendar_response, mock_events_list):
        """Trailing slashes should be handled correctly."""
        mock_response = mock_calendar_response(200, mock_events_list)

        with patch(
            "api_proxy.calendar.handlers.get_calendar_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events/",
                headers=auth_headers,
            )

        # Should work with trailing slash (normalized) or redirect
        assert response.status_code in [200, 307]  # 307 is redirect without trailing slash
