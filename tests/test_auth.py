"""Tests for API key authentication."""

import json


class TestValidAuthentication:
    """Test valid authentication scenarios."""

    def test_request_with_valid_api_key_succeeds(self, client, auth_headers, httpx_mock):
        """Request with valid API key should succeed."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json={"labels": []},
        )
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 200

    def test_last_used_at_updated_on_successful_request(
        self, client, auth_headers, api_keys_file, httpx_mock
    ):
        """last_used_at should be updated on successful authentication."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json={"labels": []},
        )

        # Check initial state
        with open(api_keys_file) as f:
            data = json.load(f)
        initial_last_used = data["keys"]["aproxy_testkey1234567890abcdefghij"]["last_used_at"]
        assert initial_last_used is None

        # Make request
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 200

        # Check last_used_at was updated
        with open(api_keys_file) as f:
            data = json.load(f)
        updated_last_used = data["keys"]["aproxy_testkey1234567890abcdefghij"]["last_used_at"]
        assert updated_last_used is not None


class TestInvalidAuthentication:
    """Test invalid authentication scenarios."""

    def test_missing_authorization_header_returns_401(self, client):
        """Missing Authorization header should return 401."""
        response = client.get("/gmail/v1/users/me/labels")
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "auth_error"
        assert "Missing Authorization header" in data["message"]

    def test_malformed_header_not_bearer_returns_401(self, client):
        """Authorization header without Bearer prefix should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Basic abc123"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "auth_error"
        assert "Invalid Authorization header format" in data["message"]

    def test_malformed_header_missing_token_returns_401(self, client):
        """Authorization header with only 'Bearer' should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer"},
        )
        assert response.status_code == 401

    def test_unknown_api_key_returns_401(self, client):
        """Unknown API key should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer aproxy_unknownkey1234567890abcdef"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"] == "auth_error"
        assert "Invalid API key" in data["message"]

    def test_disabled_api_key_returns_403(self, client, disabled_api_key):
        """Disabled API key should return 403."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": f"Bearer {disabled_api_key}"},
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "auth_error"
        assert "API key is disabled" in data["message"]


class TestEdgeCases:
    """Test edge cases for authentication."""

    def test_empty_api_key_returns_401(self, client):
        """Empty API key should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401

    def test_whitespace_only_api_key_returns_401(self, client):
        """Whitespace-only API key should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer    "},
        )
        assert response.status_code == 401

    def test_wrong_prefix_api_key_returns_401(self, client):
        """API key with wrong prefix should return 401."""
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": "Bearer wrong_testkey1234567890abcdefgh"},
        )
        assert response.status_code == 401

    def test_health_endpoint_no_auth_required(self, client):
        """Health endpoint should not require authentication."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
