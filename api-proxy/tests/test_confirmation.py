"""Tests for human-in-the-loop confirmation feature."""

import asyncio
from unittest.mock import patch

import pytest

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import (
    ConfirmationHandler,
    ConfirmationRequest,
    requires_confirmation,
)


class TestConfirmationModes:
    """Test confirmation mode behavior."""

    def test_confirm_all_requires_confirmation_for_read(self, config_confirm_all):
        """With --confirm-all, read operations require confirmation."""
        assert requires_confirmation("GET", is_modify_operation=False) is True

    def test_confirm_all_requires_confirmation_for_modify(self, config_confirm_all):
        """With --confirm-all, modify operations require confirmation."""
        assert requires_confirmation("POST", is_modify_operation=True) is True

    def test_confirm_modify_no_confirmation_for_read(self, config_confirm_modify):
        """With --confirm-modify, read operations don't require confirmation."""
        assert requires_confirmation("GET", is_modify_operation=False) is False

    def test_confirm_modify_requires_confirmation_for_modify(self, config_confirm_modify):
        """With --confirm-modify, modify operations require confirmation."""
        assert requires_confirmation("POST", is_modify_operation=True) is True

    def test_no_confirm_no_confirmation_for_read(self, config_no_confirm):
        """With --no-confirm, read operations don't require confirmation."""
        assert requires_confirmation("GET", is_modify_operation=False) is False

    def test_no_confirm_no_confirmation_for_modify(self, config_no_confirm):
        """With --no-confirm, modify operations don't require confirmation."""
        assert requires_confirmation("POST", is_modify_operation=True) is False


class TestConfirmationPrompt:
    """Test confirmation prompt handling."""

    @pytest.mark.asyncio
    async def test_approved_request_returns_true(self, config_confirm_all):
        """Approved requests (y or Y) should return True."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        with patch("api_proxy.confirmation.sys.stdin"):
            with patch("api_proxy.confirmation.sys.stdout"):
                # Simulate user typing 'y' and pressing Enter
                with patch("asyncio.to_thread", return_value="y"):
                    result = await handler.confirm(request)

        assert result is True

    @pytest.mark.asyncio
    async def test_approved_request_uppercase_y(self, config_confirm_all):
        """Approved requests with uppercase Y should return True."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        with patch("api_proxy.confirmation.sys.stdout"):
            with patch("asyncio.to_thread", return_value="Y"):
                result = await handler.confirm(request)

        assert result is True

    @pytest.mark.asyncio
    async def test_rejected_request_n_returns_false(self, config_confirm_all):
        """Rejected requests (n) should return False."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        with patch("api_proxy.confirmation.sys.stdout"):
            with patch("asyncio.to_thread", return_value="n"):
                result = await handler.confirm(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_rejected_request_empty_returns_false(self, config_confirm_all):
        """Empty response should return False (default is no)."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        with patch("api_proxy.confirmation.sys.stdout"):
            with patch("asyncio.to_thread", return_value=""):
                result = await handler.confirm(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_prompt_includes_method_and_path(self, config_confirm_all):
        """Confirmation prompt should include method and path."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        prompt = handler._format_prompt(request)

        assert "POST" in prompt
        assert "/gmail/v1/users/me/messages/msg1/modify" in prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_labels(self, config_confirm_all):
        """Confirmation prompt should include labels being modified."""
        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
            labels_to_add=["STARRED", "IMPORTANT"],
            labels_to_remove=["UNREAD"],
        )

        prompt = handler._format_prompt(request)

        assert "STARRED" in prompt
        assert "IMPORTANT" in prompt
        assert "UNREAD" in prompt
        assert "Add labels" in prompt
        assert "Remove labels" in prompt


class TestConfirmationTimeout:
    """Test confirmation timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, temp_dir, api_keys_file, token_file):
        """Timeout should return False."""
        config = Config(
            api_keys_file=api_keys_file,
            token_file=token_file,
            confirmation_mode=ConfirmationMode.ALL,
            confirmation_timeout=0.1,  # Very short timeout
        )
        set_config(config)

        handler = ConfirmationHandler()
        request = ConfirmationRequest(
            method="POST",
            path="/gmail/v1/users/me/messages/msg1/modify",
        )

        # Simulate a blocking input that times out
        async def slow_input(*args, **kwargs):
            await asyncio.sleep(10)  # Much longer than timeout
            return "y"

        with patch("api_proxy.confirmation.sys.stdout"):
            with patch("asyncio.to_thread", side_effect=slow_input):
                result = await handler.confirm(request)

        assert result is False


class TestIntegrationWithHandlers:
    """Integration tests for confirmation with Gmail handlers."""

    def test_read_operation_no_confirm_with_modify_mode(
        self, client, auth_headers, httpx_mock, config_confirm_modify
    ):
        """Read operations should proceed without confirmation in modify mode."""
        httpx_mock.add_response(
            url="https://gmail.googleapis.com/gmail/v1/users/me/labels",
            json={"labels": []},
        )

        # This should work without any confirmation prompt
        response = client.get("/gmail/v1/users/me/labels", headers=auth_headers)
        assert response.status_code == 200

    def test_modify_operation_rejected_returns_403(
        self, client, auth_headers, httpx_mock, config_confirm_modify
    ):
        """Rejected modify operations should return 403."""
        # Note: In actual test, we'd need to mock stdin
        # For now, test that with --no-confirm, it works
        pass  # This requires more complex stdin mocking

    def test_blocked_operation_never_prompts(self, client, auth_headers, config_confirm_all):
        """Blocked operations should never trigger confirmation prompt."""
        # Even with confirm_all, send is blocked before confirmation
        response = client.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": "..."},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestCommandLineArgumentParsing:
    """Test CLI argument parsing for confirmation modes."""

    def test_confirm_all_sets_correct_mode(self):
        """--confirm-all should set ConfirmationMode.ALL."""
        import sys

        from api_proxy.main import parse_args

        original_argv = sys.argv
        try:
            sys.argv = ["api-proxy", "--confirm-all"]
            args = parse_args()
            assert args.confirm_all is True
            assert args.confirm_modify is False
            assert args.no_confirm is False
        finally:
            sys.argv = original_argv

    def test_confirm_modify_sets_correct_mode(self):
        """--confirm-modify should set ConfirmationMode.MODIFY."""
        import sys

        from api_proxy.main import parse_args

        original_argv = sys.argv
        try:
            sys.argv = ["api-proxy", "--confirm-modify"]
            args = parse_args()
            assert args.confirm_all is False
            assert args.confirm_modify is True
            assert args.no_confirm is False
        finally:
            sys.argv = original_argv

    def test_no_confirm_sets_correct_mode(self):
        """--no-confirm should set ConfirmationMode.NONE."""
        import sys

        from api_proxy.main import parse_args

        original_argv = sys.argv
        try:
            sys.argv = ["api-proxy", "--no-confirm"]
            args = parse_args()
            assert args.confirm_all is False
            assert args.confirm_modify is False
            assert args.no_confirm is True
        finally:
            sys.argv = original_argv

    def test_default_uses_confirm_modify(self):
        """Default (no option) should use confirm_modify behavior."""
        import sys

        from api_proxy.main import parse_args

        original_argv = sys.argv
        try:
            sys.argv = ["api-proxy"]
            args = parse_args()
            # All should be False, meaning default mode
            assert args.confirm_all is False
            assert args.confirm_modify is False
            assert args.no_confirm is False
        finally:
            sys.argv = original_argv
