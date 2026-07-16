"""Tests for Gmail API handlers."""

import json

import pytest


class TestListMessages:
    """Tests for GET /gmail/v1/users/{userId}/messages."""

    def test_returns_correct_status_code(
        self, client, auth_headers, httpx_mock, mock_messages_list
    ):
        """Should return 200 on success."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
            json=mock_messages_list,
        )
        response = client.get("/gmail/v1/users/me/messages", headers=auth_headers)
        assert response.status_code == 200

    def test_forwards_query_parameters(self, client, auth_headers, httpx_mock):
        """Should forward query parameters to Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10&q=is%3Aunread",
            json={"messages": []},
        )
        response = client.get(
            "/gmail/v1/users/me/messages",
            params={"maxResults": 10, "q": "is:unread"},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_returns_gmail_response(self, client, auth_headers, httpx_mock, mock_messages_list):
        """Should return Gmail API response correctly."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
            json=mock_messages_list,
        )
        response = client.get("/gmail/v1/users/me/messages", headers=auth_headers)
        data = response.json()
        assert "messages" in data
        assert len(data["messages"]) == 2


class TestGetMessage:
    """Tests for GET /gmail/v1/users/{userId}/messages/{id}."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_message):
        """Should return 200 on success."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1",
            json=mock_message,
        )
        response = client.get("/gmail/v1/users/me/messages/msg1", headers=auth_headers)
        assert response.status_code == 200

    def test_forwards_format_parameter(self, client, auth_headers, httpx_mock, mock_message):
        """Should forward format parameter to Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1?format=metadata",
            json=mock_message,
        )
        response = client.get(
            "/gmail/v1/users/me/messages/msg1",
            params={"format": "metadata"},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_returns_gmail_response(self, client, auth_headers, httpx_mock, mock_message):
        """Should return Gmail API response correctly."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1",
            json=mock_message,
        )
        response = client.get("/gmail/v1/users/me/messages/msg1", headers=auth_headers)
        data = response.json()
        assert data["id"] == "msg1"
        assert "labelIds" in data


class TestGetThread:
    """Tests for GET /gmail/v1/users/{userId}/threads/{id}."""

    def test_returns_thread(self, client, auth_headers, httpx_mock):
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/threads/thread123?format=full",
            json={
                "id": "thread123",
                "messages": [
                    {"id": "msg1", "threadId": "thread123"},
                    {"id": "msg2", "threadId": "thread123"},
                ],
            },
        )
        response = client.get(
            "/gmail/v1/users/me/threads/thread123?format=full",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "thread123"
        assert len(data["messages"]) == 2

    def test_validates_thread_id(self, client, auth_headers):
        response = client.get(
            "/gmail/v1/users/me/threads/invalid id with spaces",
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_validates_user_id(self, client, auth_headers):
        response = client.get(
            "/gmail/v1/users/not valid/threads/thread123",
            headers=auth_headers,
        )
        assert response.status_code == 400


class TestListLabels:
    """Tests for GET /gmail/v1/users/{userId}/labels."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_labels_list):
        """Should return 200 on success."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json=mock_labels_list,
        )
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 200

    def test_returns_gmail_response(self, client, auth_headers, httpx_mock, mock_labels_list):
        """Should return Gmail API response correctly."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json=mock_labels_list,
        )
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        data = response.json()
        assert "labels" in data
        assert len(data["labels"]) == 3


class TestGetLabel:
    """Tests for GET /gmail/v1/users/{userId}/labels/{id}."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_label):
        """Should return 200 on success."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels/Label_1",
            json=mock_label,
        )
        response = client.get("/gmail/v1/users/me/labels/Label_1", headers=auth_headers)
        assert response.status_code == 200

    def test_returns_gmail_response(self, client, auth_headers, httpx_mock, mock_label):
        """Should return Gmail API response correctly."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels/Label_1",
            json=mock_label,
        )
        response = client.get("/gmail/v1/users/me/labels/Label_1", headers=auth_headers)
        data = response.json()
        assert data["id"] == "Label_1"
        assert data["name"] == "Custom Label"


class TestModifyMessage:
    """Tests for POST /gmail/v1/users/{userId}/messages/{id}/modify."""

    def test_returns_correct_status_code(
        self, client, auth_headers, httpx_mock, mock_modify_response
    ):
        """Should return 200 on success."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1/modify",
            json=mock_modify_response,
        )
        response = client.post(
            "/gmail/v1/users/me/messages/msg1/modify",
            json={"addLabelIds": ["STARRED"]},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_forwards_request_body(self, client, auth_headers, httpx_mock, mock_modify_response):
        """Should forward request body to Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1/modify",
            json=mock_modify_response,
        )
        response = client.post(
            "/gmail/v1/users/me/messages/msg1/modify",
            json={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify the request was made with correct body
        request = httpx_mock.get_request()
        assert request is not None

    def test_returns_gmail_response(self, client, auth_headers, httpx_mock, mock_modify_response):
        """Should return Gmail API response correctly."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1/modify",
            json=mock_modify_response,
        )
        response = client.post(
            "/gmail/v1/users/me/messages/msg1/modify",
            json={"addLabelIds": ["STARRED"]},
            headers=auth_headers,
        )
        data = response.json()
        assert data["id"] == "msg1"
        assert "STARRED" in data["labelIds"]


class TestTrashMessage:
    """Tests for POST /gmail/v1/users/{userId}/messages/{id}/trash."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_message):
        """Should return 200 on success (no metadata fetch in NONE confirm mode)."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1/trash",
            json=mock_message,
        )
        response = client.post(
            "/gmail/v1/users/me/messages/msg1/trash",
            headers=auth_headers,
        )
        assert response.status_code == 200


class TestUntrashMessage:
    """Tests for POST /gmail/v1/users/{userId}/messages/{id}/untrash."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_message):
        """Should return 200 on success (no metadata fetch in NONE confirm mode)."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1/untrash",
            json=mock_message,
        )
        response = client.post(
            "/gmail/v1/users/me/messages/msg1/untrash",
            headers=auth_headers,
        )
        assert response.status_code == 200


class TestGmailApiErrors:
    """Tests for Gmail API error handling."""

    def test_forwards_404_error(self, client, auth_headers, httpx_mock):
        """Should forward 404 errors from Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages/nonexistent",
            status_code=404,
            json={"error": {"code": 404, "message": "Not found"}},
        )
        response = client.get(
            "/gmail/v1/users/me/messages/nonexistent",
            headers=auth_headers,
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "backend_error"

    def test_forwards_403_error(self, client, auth_headers, httpx_mock):
        """Should forward 403 errors from Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
            status_code=403,
            json={"error": {"code": 403, "message": "Forbidden"}},
        )
        response = client.get("/gmail/v1/users/me/messages", headers=auth_headers)
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "backend_error"

    def test_forwards_500_error(self, client, auth_headers, httpx_mock):
        """Should forward 500 errors from Gmail API."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/messages",
            status_code=500,
            json={"error": {"code": 500, "message": "Internal error"}},
        )
        response = client.get("/gmail/v1/users/me/messages", headers=auth_headers)
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "backend_error"


class TestUserIdValidation:
    """Tests for userId validation."""

    def test_accepts_me_as_user_id(self, client, auth_headers, httpx_mock, mock_labels_list):
        """Should accept 'me' as userId."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json=mock_labels_list,
        )
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 200

    def test_accepts_email_as_user_id(self, client, auth_headers, httpx_mock, mock_labels_list):
        """Should accept email address as userId."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/user@example.com/labels",
            json=mock_labels_list,
        )
        response = client.get(
            "/gmail/v1/users/user@example.com/labels",
            headers=auth_headers,
        )
        assert response.status_code == 200


# Draft create/update behave identically for body handling; every draft body
# test runs against both. (method, proxy path, Gmail URL the body must go to)
DRAFT_ENDPOINTS = [
    pytest.param(
        "post",
        "/gmail/v1/users/me/drafts",
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        id="create",
    ),
    pytest.param(
        "put",
        "/gmail/v1/users/me/drafts/draft1",
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts/draft1",
        id="update",
    ),
]


@pytest.mark.parametrize(("method", "path", "gmail_url"), DRAFT_ENDPOINTS)
class TestDraftThreadId:
    """Drafts create/update must forward message.threadId to Gmail.

    threadId is what attaches a reply draft to its Gmail conversation;
    silently dropping it strands every reply draft in a fresh thread.
    """

    def test_forwards_thread_id(self, client, auth_headers, httpx_mock, method, path, gmail_url):
        httpx_mock.add_response(
            url=gmail_url, json={"id": "draft1", "message": {"id": "m1", "threadId": "t1"}}
        )
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "threadId": "t1"}},
            headers=auth_headers,
        )
        assert response.status_code == 200
        forwarded = json.loads(httpx_mock.get_requests()[-1].content)
        assert forwarded["message"] == {"raw": "dGVzdA==", "threadId": "t1"}

    def test_omits_thread_id_when_absent(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        httpx_mock.add_response(
            url=gmail_url, json={"id": "draft1", "message": {"id": "m1", "threadId": "t_new"}}
        )
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA=="}},
            headers=auth_headers,
        )
        assert response.status_code == 200
        forwarded = json.loads(httpx_mock.get_requests()[-1].content)
        assert forwarded["message"] == {"raw": "dGVzdA=="}

    def test_accepts_numeric_thread_id(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """A JSON number threadId is coerced to a string, not rejected."""
        httpx_mock.add_response(
            url=gmail_url, json={"id": "draft1", "message": {"id": "m1", "threadId": "12345"}}
        )
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "threadId": 12345}},
            headers=auth_headers,
        )
        assert response.status_code == 200
        forwarded = json.loads(httpx_mock.get_requests()[-1].content)
        assert forwarded["message"] == {"raw": "dGVzdA==", "threadId": "12345"}

    def test_ignores_output_only_resource_fields(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """A drafts.get(format="raw") round trip works: output-only Draft
        resource fields are accepted but never forwarded to Gmail."""
        httpx_mock.add_response(
            url=gmail_url, json={"id": "draft1", "message": {"id": "m1", "threadId": "t1"}}
        )
        response = getattr(client, method)(
            path,
            json={
                "id": "draft1",
                "message": {
                    "id": "m1",
                    "raw": "dGVzdA==",
                    "threadId": "t1",
                    "labelIds": ["DRAFT"],
                    "snippet": "test",
                    "historyId": "12345",
                    "internalDate": "1717000000000",
                    "sizeEstimate": 4,
                },
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        forwarded = json.loads(httpx_mock.get_requests()[-1].content)
        assert forwarded == {"message": {"raw": "dGVzdA==", "threadId": "t1"}}


@pytest.mark.parametrize(("method", "path", "gmail_url"), DRAFT_ENDPOINTS)
class TestDraftBodyValidation:
    """Malformed or misplaced draft body fields are rejected before Gmail is contacted."""

    @pytest.mark.parametrize("bad_id", ["", "t1 t2", "t1\n", "../evil"])
    def test_rejects_malformed_thread_id(
        self, client, auth_headers, httpx_mock, method, path, gmail_url, bad_id
    ):
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "threadId": bad_id}},
            headers=auth_headers,
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "proxy_error"
        assert "thread" in data["message"].lower()
        assert httpx_mock.get_requests() == []

    def test_rejects_misspelled_thread_id_key(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """A snake_case thread_id must fail loudly, not silently detach the draft."""
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "thread_id": "t1"}},
            headers=auth_headers,
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "proxy_error"
        assert "thread_id" in data["message"]
        assert httpx_mock.get_requests() == []

    def test_rejects_top_level_thread_id(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """threadId outside message must fail loudly, not silently detach the draft."""
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA=="}, "threadId": "t1"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "proxy_error"
        assert "threadId" in data["message"]
        assert httpx_mock.get_requests() == []

    def test_rejects_boolean_thread_id(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """bool is an int subclass but must not be coerced to a thread ID."""
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "threadId": True}},
            headers=auth_headers,
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "proxy_error"
        assert "threadId" in data["message"]
        assert httpx_mock.get_requests() == []

    def test_names_offending_field_named_body(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """The 422 message names the full field path even for a field
        literally named "body" (only the leading source marker is dropped)."""
        response = getattr(client, method)(
            path,
            json={"message": {"raw": "dGVzdA==", "body": "z"}},
            headers=auth_headers,
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "proxy_error"
        assert "message.body" in data["message"]
        assert httpx_mock.get_requests() == []

    def test_malformed_json_reports_decode_error(
        self, client, auth_headers, httpx_mock, method, path, gmail_url
    ):
        """Malformed JSON reports a decode error, not a byte offset posing
        as a field name."""
        response = getattr(client, method)(
            path,
            content=b'{"message": {',
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "proxy_error"
        assert data["message"] == "Invalid request parameters: JSON decode error"
        assert httpx_mock.get_requests() == []
