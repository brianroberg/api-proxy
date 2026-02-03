"""Tests for Google Calendar API client."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response

from api_proxy.calendar.client import CalendarClient, SCOPES


class TestTokenLoading:
    """Tests for credential loading from token file."""

    def test_loads_credentials_from_token_file(self, temp_dir):
        """Should load credentials from token.json file."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "test_access_token",
            "refresh_token": "test_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "scopes": SCOPES,
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)
        creds = client._load_credentials()

        assert creds is not None
        assert creds.token == "test_access_token"
        assert creds.refresh_token == "test_refresh_token"

    def test_returns_none_when_token_file_missing(self, temp_dir):
        """Should return None when token file doesn't exist."""
        token_path = temp_dir / "nonexistent.json"
        client = CalendarClient(token_file=token_path)
        creds = client._load_credentials()
        assert creds is None

    def test_returns_none_on_invalid_json(self, temp_dir):
        """Should return None for invalid JSON in token file."""
        token_path = temp_dir / "token.json"
        token_path.write_text("not valid json")

        client = CalendarClient(token_file=token_path)
        creds = client._load_credentials()
        assert creds is None


class TestTokenRefresh:
    """Tests for token refresh logic."""

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(self, temp_dir):
        """Should refresh expired tokens automatically."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "old_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)

        # Mock the credentials object with expired=True
        mock_creds = MagicMock()
        mock_creds.token = "new_token"
        mock_creds.refresh_token = "refresh_token"
        mock_creds.expired = True
        mock_creds.expiry = None

        with patch.object(client, "_load_credentials", return_value=mock_creds):
            # Get credentials should trigger refresh
            creds = client._get_credentials()
            mock_creds.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_401_with_refreshed_token(self, temp_dir):
        """Should retry request with refreshed token on 401."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "old_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)

        # Create mock credentials
        mock_creds = MagicMock()
        mock_creds.token = "refreshed_token"
        mock_creds.refresh_token = "refresh_token"
        mock_creds.expired = False

        # First response is 401, second is 200
        mock_http = AsyncMock()
        response_401 = MagicMock(spec=Response)
        response_401.status_code = 401
        response_200 = MagicMock(spec=Response)
        response_200.status_code = 200
        response_200.json.return_value = {"events": []}
        mock_http.request.side_effect = [response_401, response_200]

        with patch.object(client, "_get_credentials", return_value=mock_creds), \
             patch.object(client, "get_http_client", return_value=mock_http), \
             patch.object(client, "_force_refresh_credentials", return_value=mock_creds), \
             patch("api_proxy.calendar.client.get_config") as mock_config:

            mock_config.return_value.calendar_api_base_url = "https://www.googleapis.com/calendar/v3"

            response = await client.request("GET", "/calendars/primary/events")

            # Should have made two requests (original + retry)
            assert mock_http.request.call_count == 2
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_401_when_refresh_fails(self, temp_dir):
        """Should return 401 when token refresh fails."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "old_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)

        mock_creds = MagicMock()
        mock_creds.token = "old_token"
        mock_creds.refresh_token = "refresh_token"
        mock_creds.expired = False

        # Response is always 401
        mock_http = AsyncMock()
        response_401 = MagicMock(spec=Response)
        response_401.status_code = 401
        mock_http.request.return_value = response_401

        with patch.object(client, "_get_credentials", return_value=mock_creds), \
             patch.object(client, "get_http_client", return_value=mock_http), \
             patch.object(client, "_force_refresh_credentials", return_value=None), \
             patch("api_proxy.calendar.client.get_config") as mock_config:

            mock_config.return_value.calendar_api_base_url = "https://www.googleapis.com/calendar/v3"

            response = await client.request("GET", "/calendars/primary/events")

            assert response.status_code == 401


class TestApiCallConstruction:
    """Tests for API call construction."""

    @pytest.mark.asyncio
    async def test_constructs_correct_url(self, temp_dir):
        """Should construct correct Calendar API URL."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)

        mock_creds = MagicMock()
        mock_creds.token = "test_token"
        mock_creds.expired = False

        mock_http = AsyncMock()
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_http.request.return_value = mock_response

        with patch.object(client, "_get_credentials", return_value=mock_creds), \
             patch.object(client, "get_http_client", return_value=mock_http), \
             patch("api_proxy.calendar.client.get_config") as mock_config:

            mock_config.return_value.calendar_api_base_url = "https://www.googleapis.com/calendar/v3"

            await client.request("GET", "/calendars/primary/events")

            # Verify URL construction
            call_kwargs = mock_http.request.call_args[1]
            assert call_kwargs["url"] == "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    @pytest.mark.asyncio
    async def test_includes_authorization_header(self, temp_dir):
        """Should include Bearer token in Authorization header."""
        token_path = temp_dir / "token.json"
        token_data = {
            "token": "test_access_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_path.write_text(json.dumps(token_data))

        client = CalendarClient(token_file=token_path)

        mock_creds = MagicMock()
        mock_creds.token = "test_access_token"
        mock_creds.expired = False

        mock_http = AsyncMock()
        mock_response = MagicMock(spec=Response)
        mock_response.status_code = 200
        mock_http.request.return_value = mock_response

        with patch.object(client, "_get_credentials", return_value=mock_creds), \
             patch.object(client, "get_http_client", return_value=mock_http), \
             patch("api_proxy.calendar.client.get_config") as mock_config:

            mock_config.return_value.calendar_api_base_url = "https://www.googleapis.com/calendar/v3"

            await client.request("GET", "/calendars/primary")

            # Verify Authorization header
            call_kwargs = mock_http.request.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test_access_token"


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_no_credentials(self, temp_dir):
        """Should raise RuntimeError when credentials are not available."""
        token_path = temp_dir / "nonexistent.json"
        client = CalendarClient(token_file=token_path)

        with pytest.raises(RuntimeError, match="Backend authentication failed"):
            await client.request("GET", "/calendars/primary")


class TestScopes:
    """Tests for OAuth scopes."""

    def test_uses_full_calendar_scope(self):
        """Should use full calendar scope (required for blocking events with attendees)."""
        assert SCOPES == ["https://www.googleapis.com/auth/calendar"]
