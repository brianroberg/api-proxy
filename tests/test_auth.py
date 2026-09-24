"""Tests for API key authentication."""

import json
import logging

from api_proxy.auth import APIKeyManager


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
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


LABELS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/labels"


class TestKeysFileIsReloadedPerRequest:
    """Invariant 2: the keys file is re-read on every request, so disabling or
    revoking a key cuts an agent off at once, and a new key works at once. No
    test changed the file between two requests, so caching the parsed keys for
    the life of the process passed the whole suite."""

    def _ok(self, client, headers):
        return client.get("/gmail/v1/users/me/labels", headers=headers).status_code

    def test_disable_takes_effect_without_restart(
        self, client, auth_headers, api_keys_file, httpx_mock
    ):
        httpx_mock.add_response(url=LABELS_URL, json={"labels": []})
        assert self._ok(client, auth_headers) == 200
        assert APIKeyManager(api_keys_file).set_enabled("test-key", False) is True

        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 403
        assert response.json() == {"error": "auth_error", "message": "API key is disabled"}

    def test_revoke_takes_effect_without_restart(
        self, client, auth_headers, api_keys_file, httpx_mock
    ):
        httpx_mock.add_response(url=LABELS_URL, json={"labels": []})
        assert self._ok(client, auth_headers) == 200
        assert APIKeyManager(api_keys_file).revoke_key("test-key") is True

        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 401
        assert response.json() == {"error": "auth_error", "message": "Invalid API key"}

    def test_a_key_created_after_startup_is_accepted(
        self, client, auth_headers, api_keys_file, httpx_mock
    ):
        httpx_mock.add_response(url=LABELS_URL, json={"labels": []}, is_reusable=True)
        assert self._ok(client, auth_headers) == 200
        new_key = APIKeyManager(api_keys_file).create_key("late-agent")

        assert self._ok(client, {"Authorization": f"Bearer {new_key}"}) == 200


class TestErrorsNeverLeakKeys:
    """Invariant 4: errors never leak API keys. The 401/403 tests matched a
    substring of the message, so echoing the key into the body passed, and no
    test looked at what the auth path logs (a persistent --log-file)."""

    UNKNOWN_KEY = "aproxy_" + "u" * 32

    def test_unknown_key_body_is_exact_and_does_not_echo_the_key(self, client):
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": f"Bearer {self.UNKNOWN_KEY}"},
        )
        assert response.status_code == 401
        assert response.json() == {"error": "auth_error", "message": "Invalid API key"}
        assert self.UNKNOWN_KEY not in response.text

    def test_disabled_key_body_is_exact_and_does_not_echo_the_key(self, client, disabled_api_key):
        response = client.get(
            "/gmail/v1/users/me/labels",
            headers={"Authorization": f"Bearer {disabled_api_key}"},
        )
        assert response.status_code == 403
        assert response.json() == {"error": "auth_error", "message": "API key is disabled"}
        assert disabled_api_key not in response.text

    def test_rejected_keys_are_never_logged_in_full(self, client, disabled_api_key, caplog):
        caplog.set_level(logging.DEBUG, logger="api_proxy")
        for key in (self.UNKNOWN_KEY, disabled_api_key):
            client.get("/gmail/v1/users/me/labels", headers={"Authorization": f"Bearer {key}"})
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert self.UNKNOWN_KEY not in logged
        assert disabled_api_key not in logged
        # The documented behaviour is a short prefix, enough to tell keys apart.
        assert self.UNKNOWN_KEY[:10] in logged
