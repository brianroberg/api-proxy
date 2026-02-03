"""Tests for web-based approval API handlers."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from api_proxy.approval.handlers import router
from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.main import app
from api_proxy.web_confirmation import get_web_queue, reset_web_queue


@pytest.fixture
def config_web_confirm(temp_dir, api_keys_file, token_file):
    """Configure for web confirmation."""
    config = Config(
        api_keys_file=api_keys_file,
        token_file=token_file,
        confirmation_mode=ConfirmationMode.MODIFY,
        confirmation_timeout=5.0,
        web_confirmation=True,
    )
    set_config(config)
    return config


@pytest.fixture
def web_client(config_web_confirm):
    """Create test client with approval router enabled."""
    reset_web_queue()
    # Include approval router for testing
    if router not in app.routes:
        app.include_router(router)
    client = TestClient(app)
    yield client


class TestApprovalQueueEndpoint:
    """Tests for GET /approval/api/queue endpoint."""

    def test_get_queue_empty(self, web_client):
        """Empty queue should return empty list."""
        response = web_client.get("/approval/api/queue")
        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == []

    def test_get_queue_with_pending_request(self, web_client, config_web_confirm):
        """Queue with pending request should return request details."""
        queue = get_web_queue()

        # Add request in background
        async def add_request():
            return await queue.add_request(
                method="POST",
                path="/gmail/v1/users/me/messages/123/modify",
                labels_to_add=["STARRED"],
            )

        loop = asyncio.new_event_loop()
        task = loop.create_task(add_request())

        # Wait a moment for request to be added
        loop.run_until_complete(asyncio.sleep(0.05))

        try:
            response = web_client.get("/approval/api/queue")
            assert response.status_code == 200
            data = response.json()
            assert len(data["pending"]) == 1

            req = data["pending"][0]
            assert req["method"] == "POST"
            assert req["path"] == "/gmail/v1/users/me/messages/123/modify"
            assert req["labels_to_add"] == ["STARRED"]
            assert "id" in req
            assert "created_at" in req
        finally:
            task.cancel()
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            loop.close()


class TestApproveEndpoint:
    """Tests for POST /approval/api/{request_id}/approve endpoint."""

    def test_approve_request(self, web_client, config_web_confirm):
        """Approving request should return success and resolve request."""
        queue = get_web_queue()
        result_holder = {}

        async def add_and_wait():
            result = await queue.add_request(
                method="POST",
                path="/test",
            )
            result_holder["result"] = result

        loop = asyncio.new_event_loop()
        task = loop.create_task(add_and_wait())
        loop.run_until_complete(asyncio.sleep(0.05))

        try:
            # Get request ID
            pending = loop.run_until_complete(queue.get_pending())
            request_id = pending[0]["id"]

            # Approve via API
            response = web_client.post(f"/approval/api/{request_id}/approve")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Request approved"

            # Wait for result
            loop.run_until_complete(task)
            assert result_holder["result"] is True
        finally:
            loop.close()

    def test_approve_nonexistent_returns_404(self, web_client):
        """Approving non-existent request should return 404."""
        response = web_client.post("/approval/api/nonexistent-id/approve")
        assert response.status_code == 404


class TestRejectEndpoint:
    """Tests for POST /approval/api/{request_id}/reject endpoint."""

    def test_reject_request(self, web_client, config_web_confirm):
        """Rejecting request should return success and resolve request as False."""
        queue = get_web_queue()
        result_holder = {}

        async def add_and_wait():
            result = await queue.add_request(
                method="POST",
                path="/test",
            )
            result_holder["result"] = result

        loop = asyncio.new_event_loop()
        task = loop.create_task(add_and_wait())
        loop.run_until_complete(asyncio.sleep(0.05))

        try:
            # Get request ID
            pending = loop.run_until_complete(queue.get_pending())
            request_id = pending[0]["id"]

            # Reject via API
            response = web_client.post(f"/approval/api/{request_id}/reject")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["message"] == "Request rejected"

            # Wait for result
            loop.run_until_complete(task)
            assert result_holder["result"] is False
        finally:
            loop.close()

    def test_reject_nonexistent_returns_404(self, web_client):
        """Rejecting non-existent request should return 404."""
        response = web_client.post("/approval/api/nonexistent-id/reject")
        assert response.status_code == 404


class TestApprovalUI:
    """Tests for GET /approval/ UI endpoint."""

    def test_approval_ui_returns_html(self, web_client):
        """UI endpoint should return HTML page."""
        response = web_client.get("/approval/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "API Proxy Approval Queue" in response.text
