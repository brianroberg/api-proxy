"""Security tests - verify blocked operations are actually blocked."""

from unittest.mock import AsyncMock, patch

import pytest

from api_proxy.confirmation import ConfirmationHandler
from api_proxy.main import is_blocked_path


class TestBlockedOperations:
    """Test that blocked operations return 403 Forbidden."""

    def test_send_message_blocked(self, client, auth_headers):
        """POST /gmail/v1/users/me/messages/send should be blocked."""
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "forbidden"
        assert "not allowed" in data["message"].lower()

    def test_send_draft_blocked(self, client, auth_headers):
        """POST /gmail/v1/users/me/drafts/send should be blocked."""
        response = client.post(
            "/gmail/v1/users/me/drafts/send",
            json={"id": "draft123"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_import_message_blocked(self, client, auth_headers):
        """POST /gmail/v1/users/me/messages/import should be blocked."""
        response = client.post(
            "/gmail/v1/users/me/messages/import",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_insert_message_blocked(self, client, auth_headers):
        """POST /gmail/v1/users/me/messages/insert should be blocked."""
        response = client.post(
            "/gmail/v1/users/me/messages/insert",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_unknown_endpoint_blocked(self, client, auth_headers):
        """Unknown endpoints should return 403."""
        response = client.get(
            "/gmail/v1/users/me/unknown",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestBypassAttempts:
    """Test attempted security bypasses."""

    def test_url_encoding_send(self, client, auth_headers):
        """URL-encoded 'send' should still be blocked."""
        # %73%65%6e%64 = send
        response = client.post(
            "/gmail/v1/users/me/messages/%73%65%6e%64",
            json={"raw": "..."},
            headers=auth_headers,
        )
        # Should be blocked (403) or not found - either is acceptable
        assert response.status_code in [403, 404]

    def test_case_variation_send(self, client, auth_headers):
        """Case variations of 'send' should still be blocked."""
        response = client.post(
            "/gmail/v1/users/me/messages/SEND",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_case_variation_drafts_send(self, client, auth_headers):
        """Case variations of 'drafts/send' should still be blocked."""
        response = client.post(
            "/gmail/v1/users/me/DRAFTS/send",
            json={"id": "draft123"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_extra_path_segments(self, client, auth_headers):
        """Extra path segments shouldn't bypass blocks."""
        response = client.post(
            "/gmail/v1/users/me/messages/send/extra",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_trailing_slash(self, client, auth_headers):
        """Trailing slashes shouldn't bypass blocks."""
        response = client.post(
            "/gmail/v1/users/me/messages/send/",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_different_user_id(self, client, auth_headers):
        """Blocked operations should be blocked for any user ID."""
        response = client.post(
            "/gmail/v1/users/attacker@example.com/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestBlockedRegardlessOfConfirmation:
    """Test that blocked operations are blocked regardless of confirmation mode."""

    def test_blocked_with_no_confirm(self, client, auth_headers, config_no_confirm):
        """Blocked operations return 403 even with --no-confirm."""
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_blocked_with_confirm_modify(self, client, auth_headers, config_confirm_modify):
        """Blocked operations return 403 with --confirm-modify."""
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_blocked_with_confirm_all(self, client, auth_headers, config_confirm_all):
        """Blocked operations return 403 with --confirm-all."""
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestAuthenticationEnforcement:
    """Test that authentication is enforced on all endpoints."""

    def test_all_gmail_endpoints_require_auth(self, client):
        """All Gmail endpoints should require authentication."""
        endpoints = [
            ("GET", "/gmail/v1/users/me/messages"),
            ("GET", "/gmail/v1/users/me/messages/msg1"),
            ("GET", "/gmail/v1/users/me/labels"),
            ("GET", "/gmail/v1/users/me/labels/label1"),
            ("POST", "/gmail/v1/users/me/messages/msg1/modify"),
            ("POST", "/gmail/v1/users/me/messages/msg1/trash"),
            ("POST", "/gmail/v1/users/me/messages/msg1/untrash"),
            ("GET", "/gmail/v1/users/me/drafts"),
            ("GET", "/gmail/v1/users/me/drafts/draft1"),
            ("POST", "/gmail/v1/users/me/drafts"),
            ("PUT", "/gmail/v1/users/me/drafts/draft1"),
            ("DELETE", "/gmail/v1/users/me/drafts/draft1"),
        ]

        for method, path in endpoints:
            if method == "GET":
                response = client.get(path)
            elif method == "POST":
                response = client.post(path, json={})
            elif method == "PUT":
                response = client.put(path, json={})
            elif method == "DELETE":
                response = client.delete(path)

            assert response.status_code == 401, f"Expected 401 for {method} {path}"

    def test_health_check_no_auth_required(self, client):
        """Health check endpoint should NOT require authentication."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_docs_endpoints_no_auth_required(self, client):
        """Documentation endpoints should NOT require authentication."""
        # /docs returns HTML, /openapi.json returns JSON schema
        docs_response = client.get("/docs")
        assert docs_response.status_code == 200

        openapi_response = client.get("/openapi.json")
        assert openapi_response.status_code == 200

        redoc_response = client.get("/redoc")
        assert redoc_response.status_code == 200

    def test_blocked_returns_403_before_checking_auth(self, client):
        """
        Blocked operations should ideally check auth first (401 before 403).
        However, for security, blocking is done in middleware before auth.
        This test verifies the current behavior.
        """
        # Without auth, blocked operations still return 403 (not 401)
        # This is acceptable - the operation is blocked regardless
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
        )
        # Either 401 (auth first) or 403 (block first) is acceptable
        assert response.status_code in [401, 403]


class TestDraftOperationsAllowed:
    """Test that draft CRUD operations are allowed (not blocked by middleware)."""

    def test_list_drafts_not_blocked(self, client, auth_headers, httpx_mock):
        """GET /gmail/v1/users/me/drafts should pass middleware."""
        httpx_mock.add_response(json={"drafts": [], "resultSizeEstimate": 0})
        response = client.get(
            "/gmail/v1/users/me/drafts",
            headers=auth_headers,
        )
        assert response.status_code != 403

    def test_get_draft_not_blocked(self, client, auth_headers, httpx_mock):
        """GET /gmail/v1/users/me/drafts/{id} should pass middleware."""
        httpx_mock.add_response(json={"id": "draft1", "message": {"id": "msg1", "threadId": "t1"}})
        response = client.get(
            "/gmail/v1/users/me/drafts/draft1",
            headers=auth_headers,
        )
        assert response.status_code != 403

    def test_create_draft_not_blocked(self, client, auth_headers, httpx_mock):
        """POST /gmail/v1/users/me/drafts should pass middleware."""
        httpx_mock.add_response(json={"id": "draft1", "message": {"id": "msg1", "threadId": "t1"}})
        response = client.post(
            "/gmail/v1/users/me/drafts",
            json={"message": {"raw": "dGVzdA=="}},
            headers=auth_headers,
        )
        assert response.status_code != 403

    def test_update_draft_not_blocked(self, client, auth_headers, httpx_mock):
        """PUT /gmail/v1/users/me/drafts/{id} should pass middleware."""
        httpx_mock.add_response(json={"id": "draft1", "message": {"id": "msg1", "threadId": "t1"}})
        response = client.put(
            "/gmail/v1/users/me/drafts/draft1",
            json={"message": {"raw": "dGVzdA=="}},
            headers=auth_headers,
        )
        assert response.status_code != 403

    def test_delete_draft_not_blocked(self, client, auth_headers, httpx_mock):
        """DELETE /gmail/v1/users/me/drafts/{id} should pass middleware."""
        httpx_mock.add_response(status_code=204)
        response = client.delete(
            "/gmail/v1/users/me/drafts/draft1",
            headers=auth_headers,
        )
        assert response.status_code != 403

    def test_send_draft_still_blocked(self, client, auth_headers):
        """POST /gmail/v1/users/me/drafts/send must remain blocked."""
        response = client.post(
            "/gmail/v1/users/me/drafts/send",
            json={"id": "draft1"},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestAllowlistApproach:
    """Verify the allowlist approach - only explicitly allowed operations work."""

    def test_delete_message_not_allowed(self, client, auth_headers):
        """DELETE on messages should not be allowed (not in allowlist)."""
        response = client.delete(
            "/gmail/v1/users/me/messages/msg1",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_batch_modify_not_allowed(self, client, auth_headers):
        """POST to batchModify should not be allowed (not in allowlist)."""
        response = client.post(
            "/gmail/v1/users/me/messages/batchModify",
            json={"ids": ["msg1"], "addLabelIds": ["STARRED"]},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_batch_delete_not_allowed(self, client, auth_headers):
        """POST to batchDelete should not be allowed (not in allowlist)."""
        response = client.post(
            "/gmail/v1/users/me/messages/batchDelete",
            json={"ids": ["msg1"]},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_create_label_not_allowed(self, client, auth_headers):
        """POST to create label should not be allowed (not in allowlist)."""
        response = client.post(
            "/gmail/v1/users/me/labels",
            json={"name": "New Label"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_delete_label_not_allowed(self, client, auth_headers):
        """DELETE on labels should not be allowed (not in allowlist)."""
        response = client.delete(
            "/gmail/v1/users/me/labels/Label_1",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_get_profile_not_allowed(self, client, auth_headers):
        """GET profile should not be allowed (not in allowlist)."""
        response = client.get(
            "/gmail/v1/users/me/profile",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_watch_not_allowed(self, client, auth_headers):
        """POST watch should not be allowed (not in allowlist)."""
        response = client.post(
            "/gmail/v1/users/me/watch",
            json={"topicName": "projects/test/topics/test"},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestBlocklistNeverPrompts:
    """Invariant 1: blocked operations are rejected immediately and never reach
    the approval prompt, whatever the method. The POST-only tests above pass
    even with BLOCKED_PATHS emptied, because the allowlist also rejects POST to
    these paths; but DELETE/PUT/GET on .../drafts/send route into the draft
    handlers (as draft_id="send") and would queue an approval push. These
    requests exercise the blocklist on its own, under --confirm-all, with the
    confirmation handler watched."""

    BLOCKED_REQUESTS = [
        ("DELETE", "/gmail/v1/users/me/drafts/send"),
        ("PUT", "/gmail/v1/users/me/drafts/send"),
        ("GET", "/gmail/v1/users/me/drafts/send"),
        ("GET", "/gmail/v1/users/me/messages/send"),
        ("GET", "/gmail/v1/users/me/messages/import"),
        ("GET", "/gmail/v1/users/me/messages/insert"),
        ("DELETE", "/gmail/v1/users/me/drafts/send/"),
        ("DELETE", "/GMAIL/V1/USERS/ME/DRAFTS/SEND"),
        ("GET", "/gmail/v1/users/someone@example.com/messages/import"),
    ]

    @pytest.mark.parametrize("method,path", BLOCKED_REQUESTS)
    def test_blocked_path_is_403_without_prompting_or_calling_upstream(
        self, client, auth_headers, config_confirm_all, httpx_mock, method, path
    ):
        with patch.object(ConfirmationHandler, "confirm", new_callable=AsyncMock) as confirm:
            response = client.request(method, path, json={}, headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
        assert confirm.await_count == 0
        assert httpx_mock.get_requests() == []

    @pytest.mark.parametrize(
        "path,blocked",
        [
            ("/gmail/v1/users/me/messages/send", True),
            ("/gmail/v1/users/me/messages/send/", True),
            ("/Gmail/V1/Users/Me/Drafts/Send", True),
            ("/gmail/v1/users/anyone/messages/insert", True),
            ("/gmail/v1/users/me/drafts/send/extra", False),
            ("/gmail/v1/users/me/drafts", False),
        ],
    )
    def test_is_blocked_path(self, path, blocked):
        assert is_blocked_path(path) is blocked


class TestUnlistedMethodsAreRejected:
    """The allowlist matches (method, path) pairs. HEAD would run the GET handler
    and OPTIONS/TRACE/unknown methods have no reviewed route, so none may pass
    the middleware for any allowlisted or blocked path."""

    @pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "TRACE", "SEND"])
    @pytest.mark.parametrize(
        "path",
        [
            "/gmail/v1/users/me/messages",
            "/gmail/v1/users/me/drafts/d1",
            "/gmail/v1/users/me/messages/send",
            "/calendar/v3/calendars/primary/events",
        ],
    )
    def test_unlisted_method_is_403_and_never_reaches_upstream(
        self, client, auth_headers, httpx_mock, method, path
    ):
        response = client.request(method, path, headers=auth_headers)
        assert response.status_code == 403
        if method != "HEAD":  # a HEAD response carries no body
            assert response.json()["error"] == "forbidden"
        assert httpx_mock.get_requests() == []
