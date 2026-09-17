"""Security tests for Google Calendar API proxy."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import ConfirmationOutcome


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

        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events",
                headers=auth_headers,
            )

        # Should succeed without confirmation
        assert response.status_code == 200

    def test_create_event_requires_confirmation_without_send_updates(
        self,
        client,
        auth_headers,
        config_confirm_modify,
        mock_calendar_response,
        mock_created_event,
    ):
        """Bare creates (no sendUpdates) require confirmation in modify mode."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
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

        # Rejected by the operator, and nothing was forwarded to the backend
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
        mock_client.request.assert_not_awaited()


class TestDeleteEventConfirmation:
    """Tests for DELETE event confirmation behavior."""

    def test_delete_requires_confirmation_in_modify_mode(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response
    ):
        """DELETE should require confirmation in modify mode."""
        mock_response = mock_calendar_response(204, None)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
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

        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client:
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
        self,
        client,
        auth_headers,
        config_confirm_modify,
        mock_calendar_response,
        mock_created_event,
    ):
        """Creating event with sendUpdates=all should require confirmation."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
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

    def test_create_with_send_updates_none_still_requires_confirmation(
        self,
        client,
        auth_headers,
        config_confirm_modify,
        mock_calendar_response,
        mock_created_event,
    ):
        """sendUpdates=none no longer skips the gate: a create is still a mutation."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
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

        # Rejected by the operator, and nothing was forwarded to the backend
        assert response.status_code == 403
        mock_client.request.assert_not_awaited()

    def test_update_with_send_updates_external_only_requires_confirmation(
        self, client, auth_headers, config_confirm_modify, mock_calendar_response, mock_event
    ):
        """Updating event with sendUpdates=externalOnly should require confirmation."""
        mock_response = mock_calendar_response(200, mock_event)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
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


EVENT_MUTATIONS = [
    pytest.param("post", "/calendar/v3/calendars/primary/events", id="create"),
    pytest.param("put", "/calendar/v3/calendars/primary/events/event1", id="update"),
    pytest.param("patch", "/calendar/v3/calendars/primary/events/event1", id="patch"),
]

EVENT_BODY = {
    "summary": "Team Sync",
    "start": {"dateTime": "2025-01-21T14:00:00-05:00"},
    "end": {"dateTime": "2025-01-21T15:00:00-05:00"},
}


class TestBareMutationConfirmation:
    """All event mutations are gated on the same footing as DELETE (issue #7).

    Bare creates/updates/patches (no sendUpdates) used to bypass confirmation
    entirely in MODIFY mode while DELETE and /respond always confirmed.
    """

    @pytest.mark.parametrize(("method", "url"), EVENT_MUTATIONS)
    def test_bare_mutation_requires_confirmation_in_modify_mode(
        self,
        client,
        auth_headers,
        config_confirm_modify,
        mock_calendar_response,
        mock_created_event,
        method,
        url,
    ):
        """POST/PUT/PATCH without sendUpdates must prompt in MODIFY mode."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'n' (reject)
        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = getattr(client, method)(url, json=EVENT_BODY, headers=auth_headers)

        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "forbidden"
        assert data["message"] == "Request rejected by operator"
        # The mutation must never reach the backend
        mock_client.request.assert_not_awaited()

    @pytest.mark.parametrize(("method", "url"), EVENT_MUTATIONS)
    def test_bare_mutation_approved_confirmation_forwards(
        self,
        client,
        auth_headers,
        config_confirm_modify,
        mock_calendar_response,
        mock_created_event,
        method,
        url,
    ):
        """Operator approval lets the bare mutation through to the backend."""
        mock_response = mock_calendar_response(200, mock_created_event)

        # Mock stdin to return 'y' (approve)
        with (
            patch("sys.stdin.readline", return_value="y\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = getattr(client, method)(url, json=EVENT_BODY, headers=auth_headers)

        assert response.status_code == 200
        mock_client.request.assert_awaited_once()

    @pytest.mark.parametrize(("method", "url"), EVENT_MUTATIONS)
    def test_bare_mutation_no_confirm_in_none_mode(
        self,
        client,
        auth_headers,
        config_no_confirm,
        mock_calendar_response,
        mock_created_event,
        method,
        url,
    ):
        """NONE mode is unchanged: bare mutations execute without any prompt."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = getattr(client, method)(url, json=EVENT_BODY, headers=auth_headers)

        assert response.status_code == 200
        mock_client.request.assert_awaited_once()

    def test_create_still_prompts_in_all_mode(
        self, client, auth_headers, config_confirm_all, mock_calendar_response, mock_created_event
    ):
        """ALL mode is unchanged: mutations prompt there too."""
        mock_response = mock_calendar_response(200, mock_created_event)

        with (
            patch("sys.stdin.readline", return_value="n\n"),
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_client.request.assert_not_awaited()


class TestCalendarConfirmationOutcomes:
    """Rejection and expiry produce distinguishable 403 details (issue #8)."""

    @staticmethod
    def _mocks(mock_get_client, mock_get_handler, outcome):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client
        mock_handler = AsyncMock()
        mock_handler.confirm = AsyncMock(return_value=outcome)
        mock_get_handler.return_value = mock_handler
        return mock_client

    def test_expired_confirmation_returns_confirmation_expired(
        self, client, auth_headers, config_confirm_modify
    ):
        """An unanswered confirmation returns confirmation_expired, and no write happens."""
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client = self._mocks(
                mock_get_client, mock_get_handler, ConfirmationOutcome.EXPIRED
            )
            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        assert response.json() == {
            "error": "confirmation_expired",
            "message": "Confirmation request expired before an operator responded",
        }
        mock_client.request.assert_not_awaited()

    def test_rejected_confirmation_keeps_forbidden_detail(
        self, client, auth_headers, config_confirm_modify
    ):
        """Genuine rejection keeps the exact pre-existing 403 detail."""
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client = self._mocks(
                mock_get_client, mock_get_handler, ConfirmationOutcome.REJECTED
            )
            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        assert response.json() == {
            "error": "forbidden",
            "message": "Request rejected by operator",
        }
        mock_client.request.assert_not_awaited()


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

    def test_trailing_slash_calendar(
        self, client, auth_headers, mock_calendar_response, mock_events_list
    ):
        """Trailing slashes should be handled correctly."""
        mock_response = mock_calendar_response(200, mock_events_list)

        with patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_get_client.return_value = mock_client

            response = client.get(
                "/calendar/v3/calendars/primary/events/",
                headers=auth_headers,
            )

        # Should work with trailing slash (normalized) or redirect
        assert response.status_code in [200, 307]  # 307 is redirect without trailing slash


# =============================================================================
# PER-CALENDAR APPROVAL EXEMPTION (issue #16)
# =============================================================================

EARMARKS_ID = (
    "c_dee653a1a8d29be6243468773c8c7909bd3936ac2ff477fad3dd3852b3eeb6bf@group.calendar.google.com"
)
EARMARKS_ID_ENCODED = EARMARKS_ID.replace("@", "%40")

EXEMPT_EVENT_MUTATIONS = [
    pytest.param("post", f"/calendar/v3/calendars/{EARMARKS_ID}/events", id="create"),
    pytest.param("put", f"/calendar/v3/calendars/{EARMARKS_ID}/events/event1", id="update"),
    pytest.param("patch", f"/calendar/v3/calendars/{EARMARKS_ID}/events/event1", id="patch"),
    pytest.param("delete", f"/calendar/v3/calendars/{EARMARKS_ID}/events/event1", id="delete"),
]


def _exempt_config(api_keys_file, token_file, exempt, mode=ConfirmationMode.MODIFY):
    """Set a config with the given confirmation mode and exempt-calendar set."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=mode,
        confirmation_timeout=1.0,
        approval_exempt_calendars=frozenset(exempt),
    )
    set_config(config)
    return config


def _gating_mocks(mock_get_client, mock_get_handler, outcome=ConfirmationOutcome.REJECTED):
    """Backend client mock plus a confirmation handler mock returning ``outcome``."""
    mock_client = AsyncMock()
    mock_client.request.return_value = MagicMock(status_code=200, json=lambda: {"id": "e"})
    mock_get_client.return_value = mock_client
    mock_handler = AsyncMock()
    mock_handler.confirm = AsyncMock(return_value=outcome)
    mock_get_handler.return_value = mock_handler
    return mock_client, mock_handler


class TestApprovalExemptCalendars:
    """Writes to a calendar in APPROVAL_EXEMPT_CALENDARS skip the approval gate;
    everything else still queues exactly as before."""

    @pytest.mark.parametrize(("method", "url"), EXEMPT_EVENT_MUTATIONS)
    def test_write_to_exempt_calendar_bypasses_and_is_logged(
        self, client, auth_headers, api_keys_file, token_file, caplog, method, url
    ):
        """Every write on the exempt calendar runs without consulting the gate,
        and the bypass is logged at INFO with the calendar id and method."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
            caplog.at_level(logging.INFO, logger="api_proxy.calendar.handlers"),
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            kwargs = {} if method == "delete" else {"json": EVENT_BODY}
            response = getattr(client, method)(url, headers=auth_headers, **kwargs)

        assert response.status_code == 200
        mock_handler.confirm.assert_not_awaited()
        # DELETE reads the event first, then deletes; the others write once.
        expected_calls = 2 if method == "delete" else 1
        assert mock_client.request.await_count == expected_calls
        assert mock_client.request.await_args_list[-1].args[0] == method.upper()

        bypass_logs = [
            r for r in caplog.records if r.levelno == logging.INFO and "bypass" in r.getMessage()
        ]
        assert len(bypass_logs) == 1, [r.getMessage() for r in caplog.records]
        message = bypass_logs[0].getMessage()
        assert EARMARKS_ID in message
        assert method.upper() in message

    def test_url_encoded_calendar_id_matches_and_logs_decoded_id(
        self, client, auth_headers, api_keys_file, token_file, caplog
    ):
        """A request with '@' percent-encoded still matches the decoded exempt id,
        and the audit line carries the canonical (decoded) id."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
            caplog.at_level(logging.INFO, logger="api_proxy.calendar.handlers"),
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID_ENCODED}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_handler.confirm.assert_not_awaited()
        mock_client.request.assert_awaited_once()
        bypass_logs = [r.getMessage() for r in caplog.records if "bypass" in r.getMessage()]
        assert len(bypass_logs) == 1
        assert EARMARKS_ID in bypass_logs[0]
        assert EARMARKS_ID_ENCODED not in bypass_logs[0]

    def test_primary_write_still_queues_when_exempt_list_set(
        self, client, auth_headers, api_keys_file, token_file
    ):
        """The guard against over-broad matching: with Earmarks exempt, a write to
        the primary calendar is still gated and a rejection still blocks it."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                "/calendar/v3/calendars/primary/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
        mock_handler.confirm.assert_awaited_once()
        mock_client.request.assert_not_awaited()

    @pytest.mark.parametrize(
        "calendar_id",
        [
            pytest.param("x" + EARMARKS_ID, id="superstring-of-exempt-id"),
            pytest.param(EARMARKS_ID.removeprefix("c_"), id="substring-of-exempt-id"),
            pytest.param(EARMARKS_ID.upper(), id="case-variant"),
        ],
    )
    def test_near_miss_calendar_ids_do_not_bypass(
        self, client, auth_headers, api_keys_file, token_file, calendar_id
    ):
        """Matching is exact-string equality: ids that merely contain, are
        contained in, or differ only in case from an exempt id still queue."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{calendar_id}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_handler.confirm.assert_awaited_once()
        mock_client.request.assert_not_awaited()

    def test_exempt_entry_does_not_match_superstring_configured(
        self, client, auth_headers, api_keys_file, token_file
    ):
        """The other direction: an exempt entry that is a superstring of the
        requested id does not exempt the shorter id."""
        _exempt_config(api_keys_file, token_file, {"x" + EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_handler.confirm.assert_awaited_once()
        mock_client.request.assert_not_awaited()

    def test_empty_exempt_list_gates_every_calendar(
        self, client, auth_headers, api_keys_file, token_file
    ):
        """With no exemptions configured, a write to the Earmarks id queues as today."""
        _exempt_config(api_keys_file, token_file, set())
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_handler.confirm.assert_awaited_once()
        mock_client.request.assert_not_awaited()

    def test_rsvp_on_exempt_calendar_still_gated(
        self, client, auth_headers, api_keys_file, token_file, mock_calendar_response
    ):
        """/respond is not a create/update/delete: an RSVP is visible to the
        organizer, so it keeps the gate even on an exempt calendar."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        event = {
            "id": "event1",
            "summary": "Invite",
            "attendees": [{"email": "robergb@dm.org", "responseStatus": "needsAction"}],
        }
        primary = {"id": "robergb@dm.org"}
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            mock_client.request.side_effect = [
                mock_calendar_response(200, event),
                mock_calendar_response(200, primary),
                mock_calendar_response(200, event),
            ]
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events/event1/respond",
                json={"responseStatus": "accepted"},
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_handler.confirm.assert_awaited_once()
        # Only the two reads happened; the PATCH never did.
        assert [c.args[0] for c in mock_client.request.await_args_list] == ["GET", "GET"]

    def test_read_on_exempt_calendar_unaffected_in_modify_mode(
        self, client, auth_headers, api_keys_file, token_file, mock_calendar_response, caplog
    ):
        """Reads never consulted the gate in MODIFY mode; they still don't, and
        no bypass line is logged for them."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID})
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
            caplog.at_level(logging.INFO, logger="api_proxy.calendar.handlers"),
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            mock_client.request.return_value = mock_calendar_response(200, {"items": []})
            response = client.get(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_handler.confirm.assert_not_awaited()
        assert not [r for r in caplog.records if "bypass" in r.getMessage()]

    def test_read_on_exempt_calendar_still_confirms_in_all_mode(
        self, client, auth_headers, api_keys_file, token_file
    ):
        """The exemption is for writes only: in ALL mode a read on the exempt
        calendar is still gated."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID}, mode=ConfirmationMode.ALL)
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.get(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                headers=auth_headers,
            )

        assert response.status_code == 403
        mock_handler.confirm.assert_awaited_once()
        mock_client.request.assert_not_awaited()

    def test_write_to_exempt_calendar_bypasses_in_all_mode(
        self, client, auth_headers, api_keys_file, token_file
    ):
        """The per-calendar exemption is the more specific setting and wins over
        ALL mode for writes on that calendar."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID}, mode=ConfirmationMode.ALL)
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_handler.confirm.assert_not_awaited()
        mock_client.request.assert_awaited_once()

    def test_no_bypass_log_in_none_mode(
        self, client, auth_headers, api_keys_file, token_file, caplog
    ):
        """In NONE mode nothing is gated, so nothing is 'bypassed' and the audit
        line is not emitted (it must mean 'the gate was skipped', not 'the
        calendar is exempt')."""
        _exempt_config(api_keys_file, token_file, {EARMARKS_ID}, mode=ConfirmationMode.NONE)
        with (
            patch("api_proxy.calendar.handlers.get_calendar_client") as mock_get_client,
            patch("api_proxy.calendar.handlers.get_confirmation_handler") as mock_get_handler,
            caplog.at_level(logging.INFO, logger="api_proxy.calendar.handlers"),
        ):
            mock_client, mock_handler = _gating_mocks(mock_get_client, mock_get_handler)
            response = client.post(
                f"/calendar/v3/calendars/{EARMARKS_ID}/events",
                json=EVENT_BODY,
                headers=auth_headers,
            )

        assert response.status_code == 200
        mock_handler.confirm.assert_not_awaited()
        assert not [r for r in caplog.records if "bypass" in r.getMessage()]
