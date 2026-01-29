"""Tests for Gmail API client."""

import json
from unittest.mock import MagicMock, patch

import pytest

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.gmail.client import GmailClient


class TestTokenLoading:
    """Tests for token file loading."""

    def test_loads_credentials_from_token_file(self, temp_dir):
        """Should load credentials from token file."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        client = GmailClient(token_file)
        creds = client._load_credentials()

        assert creds is not None
        assert creds.token == "test_token"
        assert creds.refresh_token == "test_refresh"

    def test_returns_none_when_token_file_missing(self, temp_dir):
        """Should return None when token file doesn't exist."""
        token_file = temp_dir / "nonexistent.json"
        client = GmailClient(token_file)

        creds = client._load_credentials()

        assert creds is None

    def test_returns_none_on_invalid_json(self, temp_dir):
        """Should return None on invalid JSON in token file."""
        token_file = temp_dir / "token.json"
        token_file.write_text("not valid json")

        client = GmailClient(token_file)
        creds = client._load_credentials()

        assert creds is None


class TestTokenRefresh:
    """Tests for token refresh behavior."""

    def test_refreshes_expired_token(self, temp_dir):
        """Should refresh expired tokens."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "expired_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        client = GmailClient(token_file)

        with patch.object(client, "_load_credentials") as mock_load:
            mock_creds = MagicMock()
            mock_creds.expired = True
            mock_creds.refresh_token = "test_refresh"
            mock_creds.token = "refreshed_token"
            mock_load.return_value = mock_creds

            with patch.object(client, "_save_credentials"):
                client._get_credentials()

            # Should have called refresh
            mock_creds.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_401_with_refreshed_token(self, temp_dir):
        """Should retry request with refreshed token when Gmail returns 401."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "stale_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        config = Config(
            token_file=token_file,
            api_keys_file=temp_dir / "keys.json",
            confirmation_mode=ConfirmationMode.NONE,
        )
        set_config(config)

        client = GmailClient(token_file)

        # Set up credentials that appear valid but will be rejected
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.refresh_token = "test_refresh"
        mock_creds.token = "stale_token"

        with patch.object(client, "_get_credentials", return_value=mock_creds):
            http_client = await client.get_http_client()

            # First response is 401, second is 200 (after refresh)
            mock_401_response = MagicMock()
            mock_401_response.status_code = 401

            mock_200_response = MagicMock()
            mock_200_response.status_code = 200

            with patch.object(http_client, "request") as mock_request:
                mock_request.side_effect = [mock_401_response, mock_200_response]

                with patch.object(client, "_force_refresh_credentials") as mock_refresh:
                    refreshed_creds = MagicMock()
                    refreshed_creds.token = "fresh_token"
                    mock_refresh.return_value = refreshed_creds

                    response = await client.request("GET", "/gmail/v1/users/me/labels")

                    # Should have retried after 401
                    assert mock_request.call_count == 2
                    mock_refresh.assert_called_once()
                    assert response.status_code == 200

        await client.close()

    @pytest.mark.asyncio
    async def test_returns_401_when_refresh_fails(self, temp_dir):
        """Should return 401 when token refresh fails."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "stale_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        config = Config(
            token_file=token_file,
            api_keys_file=temp_dir / "keys.json",
            confirmation_mode=ConfirmationMode.NONE,
        )
        set_config(config)

        client = GmailClient(token_file)

        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_creds.refresh_token = "test_refresh"
        mock_creds.token = "stale_token"

        with patch.object(client, "_get_credentials", return_value=mock_creds):
            http_client = await client.get_http_client()

            mock_401_response = MagicMock()
            mock_401_response.status_code = 401

            with patch.object(http_client, "request", return_value=mock_401_response):
                with patch.object(client, "_force_refresh_credentials", return_value=None):
                    response = await client.request("GET", "/gmail/v1/users/me/labels")

                    # Should return the 401 when refresh fails
                    assert response.status_code == 401

        await client.close()


class TestApiCallConstruction:
    """Tests for API call construction."""

    @pytest.mark.asyncio
    async def test_constructs_correct_url(self, temp_dir):
        """Should construct correct URL for API calls."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        # Set up config
        config = Config(
            token_file=token_file,
            api_keys_file=temp_dir / "keys.json",
            confirmation_mode=ConfirmationMode.NONE,
        )
        set_config(config)

        client = GmailClient(token_file)

        with patch.object(client, "_get_credentials") as mock_get_creds:
            mock_creds = MagicMock()
            mock_creds.token = "test_token"
            mock_creds.expired = False
            mock_get_creds.return_value = mock_creds

            http_client = await client.get_http_client()

            with patch.object(http_client, "request") as mock_request:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_request.return_value = mock_response

                await client.request("GET", "/gmail/v1/users/me/messages")

                mock_request.assert_called_once()
                call_kwargs = mock_request.call_args
                assert "https://gmail.googleapis.com/gmail/v1/users/me/messages" in str(
                    call_kwargs
                )

        await client.close()

    @pytest.mark.asyncio
    async def test_includes_authorization_header(self, temp_dir):
        """Should include Authorization header in requests."""
        token_file = temp_dir / "token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        config = Config(
            token_file=token_file,
            api_keys_file=temp_dir / "keys.json",
            confirmation_mode=ConfirmationMode.NONE,
        )
        set_config(config)

        client = GmailClient(token_file)

        with patch.object(client, "_get_credentials") as mock_get_creds:
            mock_creds = MagicMock()
            mock_creds.token = "test_token"
            mock_creds.expired = False
            mock_get_creds.return_value = mock_creds

            http_client = await client.get_http_client()

            with patch.object(http_client, "request") as mock_request:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_request.return_value = mock_response

                await client.request("GET", "/gmail/v1/users/me/messages")

                call_kwargs = mock_request.call_args
                headers = call_kwargs.kwargs.get("headers", {})
                assert "Authorization" in headers
                assert headers["Authorization"] == "Bearer test_token"

        await client.close()


class TestErrorHandling:
    """Tests for error handling in the client."""

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_no_credentials(self, temp_dir):
        """Should raise RuntimeError when credentials unavailable."""
        token_file = temp_dir / "nonexistent.json"

        config = Config(
            token_file=token_file,
            api_keys_file=temp_dir / "keys.json",
            confirmation_mode=ConfirmationMode.NONE,
        )
        set_config(config)

        client = GmailClient(token_file)

        with pytest.raises(RuntimeError, match="Backend authentication failed"):
            await client.request("GET", "/gmail/v1/users/me/messages")

        await client.close()
