"""Pytest fixtures for API proxy tests."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.main import app


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def api_keys_file(temp_dir):
    """Create a temporary API keys file with a test key."""
    keys_file = temp_dir / "api_keys.json"
    keys_data = {
        "keys": {
            "aproxy_testkey1234567890abcdefghij": {
                "name": "test-key",
                "created_at": "2025-01-15T10:30:00Z",
                "last_used_at": None,
                "enabled": True,
            },
            "aproxy_disabledkey890abcdefghijklm": {
                "name": "disabled-key",
                "created_at": "2025-01-15T10:30:00Z",
                "last_used_at": None,
                "enabled": False,
            },
        }
    }
    keys_file.write_text(json.dumps(keys_data))
    return keys_file


@pytest.fixture
def token_file(temp_dir):
    """Create a temporary token file with mock credentials."""
    token_path = temp_dir / "token.json"
    token_data = {
        "token": "mock_access_token",
        "refresh_token": "mock_refresh_token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "mock_client_id",
        "client_secret": "mock_client_secret",
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    }
    token_path.write_text(json.dumps(token_data))
    return token_path


@pytest.fixture
def test_config(api_keys_file, token_file):
    """Create and set a test configuration."""
    config = Config(
        host="127.0.0.1",
        port=8000,
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.NONE,  # Disable confirmation for most tests
        confirmation_timeout=1.0,
    )
    set_config(config)
    return config


@pytest.fixture
def client(test_config):
    """Create a test client for the FastAPI app."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def valid_api_key():
    """Return a valid API key for testing."""
    return "aproxy_testkey1234567890abcdefghij"


@pytest.fixture
def disabled_api_key():
    """Return a disabled API key for testing."""
    return "aproxy_disabledkey890abcdefghijklm"


@pytest.fixture
def auth_headers(valid_api_key):
    """Return headers with valid authentication."""
    return {"Authorization": f"Bearer {valid_api_key}"}


# =============================================================================
# GMAIL API MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_gmail_response():
    """Factory fixture for creating mock Gmail API responses."""

    def _create_response(status_code: int = 200, json_data: dict | None = None):
        response = MagicMock(spec=Response)
        response.status_code = status_code
        response.json.return_value = json_data or {}
        return response

    return _create_response


@pytest.fixture
def mock_messages_list():
    """Mock response for messages.list."""
    return {
        "messages": [
            {"id": "msg1", "threadId": "thread1"},
            {"id": "msg2", "threadId": "thread2"},
        ],
        "resultSizeEstimate": 2,
    }


@pytest.fixture
def mock_message():
    """Mock response for messages.get."""
    return {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "This is a test message...",
        "payload": {
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "recipient@example.com"},
                {"name": "Subject", "value": "Test Subject"},
            ]
        },
    }


@pytest.fixture
def mock_labels_list():
    """Mock response for labels.list."""
    return {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "SENT", "name": "SENT", "type": "system"},
            {"id": "Label_1", "name": "Custom Label", "type": "user"},
        ]
    }


@pytest.fixture
def mock_label():
    """Mock response for labels.get."""
    return {
        "id": "Label_1",
        "name": "Custom Label",
        "type": "user",
        "messagesTotal": 10,
        "messagesUnread": 3,
    }


@pytest.fixture
def mock_modify_response():
    """Mock response for messages.modify."""
    return {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX", "STARRED"],
    }


# =============================================================================
# CONFIRMATION MODE FIXTURES
# =============================================================================


@pytest.fixture
def config_confirm_all(api_keys_file, token_file):
    """Configuration with confirmation required for all operations."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.ALL,
        confirmation_timeout=1.0,
    )
    set_config(config)
    return config


@pytest.fixture
def config_confirm_modify(api_keys_file, token_file):
    """Configuration with confirmation required for modify operations only."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.MODIFY,
        confirmation_timeout=1.0,
    )
    set_config(config)
    return config


@pytest.fixture
def config_no_confirm(api_keys_file, token_file):
    """Configuration with no confirmation required."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.NONE,
        confirmation_timeout=1.0,
    )
    set_config(config)
    return config
