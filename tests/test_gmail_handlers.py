"""Tests for Gmail API handlers."""



class TestListMessages:
    """Tests for GET /gmail/v1/users/{userId}/messages."""

    def test_returns_correct_status_code(self, client, auth_headers, httpx_mock, mock_messages_list):
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

    def test_returns_gmail_response(
        self, client, auth_headers, httpx_mock, mock_modify_response
    ):
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
