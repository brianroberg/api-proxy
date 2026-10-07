"""Tests for APIKeyManager (the CLI itself is tested in test_keys_cli.py)."""

import json
import logging
import threading
import time

import pytest

from api_proxy import auth
from api_proxy.auth import API_KEY_PREFIX, APIKeyManager, KeysFileError, KeysFileLockTimeout


class TestCreateCommand:
    """Test the create command."""

    def test_creates_key_with_valid_name(self, temp_dir):
        """Create should generate a key with valid name."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")

        assert key.startswith(API_KEY_PREFIX)
        assert len(key) == len(API_KEY_PREFIX) + 32

    def test_generated_key_has_correct_format(self, temp_dir):
        """Generated key should have aproxy_ prefix + 32 alphanumeric chars."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")

        assert key.startswith("aproxy_")
        random_part = key[len("aproxy_") :]
        assert len(random_part) == 32
        assert random_part.isalnum()
        assert random_part.islower() or random_part.replace("0123456789", "").islower()

    def test_stores_key_with_correct_metadata(self, temp_dir):
        """Created key should be stored with correct metadata."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")

        with open(keys_file) as f:
            data = json.load(f)

        assert key in data["keys"]
        key_data = data["keys"][key]
        assert key_data["name"] == "test-agent"
        assert key_data["created_at"] is not None
        assert key_data["last_used_at"] is None
        assert key_data["enabled"] is True

    def test_rejects_duplicate_names(self, temp_dir):
        """Create should reject duplicate names."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        manager.create_key("test-agent")

        with pytest.raises(ValueError, match="already exists"):
            manager.create_key("test-agent")

    def test_rejects_empty_name(self, temp_dir):
        """Create should reject empty names."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        with pytest.raises(ValueError, match="between 1 and 64"):
            manager.create_key("")

    def test_rejects_too_long_name(self, temp_dir):
        """Create should reject names over 64 characters."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        with pytest.raises(ValueError, match="between 1 and 64"):
            manager.create_key("a" * 65)

    def test_rejects_special_characters(self, temp_dir):
        """Create should reject names with special characters."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        with pytest.raises(ValueError, match="alphanumeric"):
            manager.create_key("test@agent")


class TestListCommand:
    """Test the list command."""

    def test_lists_all_keys_with_correct_columns(self, temp_dir):
        """List should return all keys with correct metadata."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        manager.create_key("agent-1")
        manager.create_key("agent-2")

        keys = manager.list_keys()

        assert len(keys) == 2
        names = {k["name"] for k in keys}
        assert names == {"agent-1", "agent-2"}

        for key in keys:
            assert "name" in key
            assert "created_at" in key
            assert "last_used_at" in key
            assert "enabled" in key
            assert "key_suffix" in key

    def test_shows_never_for_unused_keys(self, temp_dir):
        """List should show None for last_used_at on unused keys."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        manager.create_key("agent-1")

        keys = manager.list_keys()

        assert keys[0]["last_used_at"] is None

    def test_handles_empty_key_file(self, temp_dir):
        """List should handle empty key file gracefully."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        keys = manager.list_keys()

        assert keys == []


class TestDisableEnableCommands:
    """Test disable and enable commands."""

    def test_disable_sets_enabled_false(self, temp_dir):
        """Disable should set enabled to false."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")
        result = manager.set_enabled("test-agent", False)

        assert result is True
        with open(keys_file) as f:
            data = json.load(f)
        assert data["keys"][key]["enabled"] is False

    def test_enable_sets_enabled_true(self, temp_dir):
        """Enable should set enabled to true."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")
        manager.set_enabled("test-agent", False)
        result = manager.set_enabled("test-agent", True)

        assert result is True
        with open(keys_file) as f:
            data = json.load(f)
        assert data["keys"][key]["enabled"] is True

    def test_disable_nonexistent_key_returns_false(self, temp_dir):
        """Disable on non-existent key should return False."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        result = manager.set_enabled("nonexistent", False)

        assert result is False

    def test_enable_nonexistent_key_returns_false(self, temp_dir):
        """Enable on non-existent key should return False."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        result = manager.set_enabled("nonexistent", True)

        assert result is False


class TestRevokeCommand:
    """Test the revoke command."""

    def test_revoke_removes_key_entirely(self, temp_dir):
        """Revoke should remove key from file entirely."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")
        result = manager.revoke_key("test-agent")

        assert result is True
        with open(keys_file) as f:
            data = json.load(f)
        assert key not in data["keys"]

    def test_revoke_nonexistent_key_returns_false(self, temp_dir):
        """Revoke on non-existent key should return False."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        result = manager.revoke_key("nonexistent")

        assert result is False


class TestShowCommand:
    """Test the show command."""

    def test_shows_all_metadata(self, temp_dir):
        """Show should display all metadata for a key."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key = manager.create_key("test-agent")
        result = manager.get_key_by_name("test-agent")

        assert result is not None
        found_key, key_data = result
        assert found_key == key
        assert key_data["name"] == "test-agent"
        assert key_data["created_at"] is not None
        assert key_data["enabled"] is True

    def test_show_nonexistent_returns_none(self, temp_dir):
        """Show on non-existent key should return None."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        result = manager.get_key_by_name("nonexistent")

        assert result is None


class TestFileHandling:
    """Test file handling edge cases."""

    def test_creates_key_file_if_not_exists(self, temp_dir):
        """Manager should create key file if it doesn't exist."""
        keys_file = temp_dir / "new_keys.json"
        manager = APIKeyManager(keys_file)

        assert not keys_file.exists()

        manager.create_key("test-agent")

        assert keys_file.exists()

    def test_handles_corrupted_json_gracefully(self, temp_dir):
        """Manager should handle corrupted JSON gracefully."""
        keys_file = temp_dir / "keys.json"
        keys_file.write_text("not valid json{{{")

        manager = APIKeyManager(keys_file)
        keys = manager.list_keys()

        # Should return empty list, not crash
        assert keys == []

    def test_a_request_does_not_rewrite_a_corrupt_keys_file(self, temp_dir, caplog):
        """Review item 4 (PR #25): update_last_used skips, with a warning,
        rather than write over a keys file it cannot parse."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        key = manager.create_key("agent")
        keys_file.write_text("not valid json{{{")

        with caplog.at_level(logging.WARNING, logger="api_proxy.auth"):
            manager.update_last_used(key)

        assert keys_file.read_text() == "not valid json{{{"
        assert any("Skipped the last_used_at update" in r.getMessage() for r in caplog.records)

    def test_create_refuses_to_replace_a_corrupt_keys_file(self, temp_dir):
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        manager.create_key("agent-1")
        keys_file.write_text("not valid json{{{")

        with pytest.raises(KeysFileError, match="keys file"):
            manager.create_key("agent-2")

        assert keys_file.read_text() == "not valid json{{{"

    def test_preserves_existing_keys_when_adding_new(self, temp_dir):
        """Adding a new key should preserve existing keys."""
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)

        key1 = manager.create_key("agent-1")
        key2 = manager.create_key("agent-2")

        with open(keys_file) as f:
            data = json.load(f)

        assert key1 in data["keys"]
        assert key2 in data["keys"]


class TestRevocationIsDurable:
    """Every authenticated request rewrites the keys file (update_last_used:
    load, set last_used_at, save). A revoke or disable that lands between one
    request's load and save is written back over by that request, so the key
    comes back. This forces that interleaving deterministically."""

    def _interleave(self, keys_file, monkeypatch, operator_change):
        manager = APIKeyManager(keys_file)
        key = manager.create_key("agent")
        original_load = APIKeyManager._load_keys
        state = {"fired": False}

        def load_then_operator_acts(self, *args, **kwargs):
            data = original_load(self, *args, **kwargs)
            if not state["fired"]:
                state["fired"] = True
                monkeypatch.setattr(APIKeyManager, "_load_keys", original_load)
                # The operator acts from another thread. Without a lock it
                # finishes inside this load-to-save window (the lost update);
                # a fix that locks the file makes it wait, and the timeout
                # lets the request finish instead of deadlocking the test.
                operator = threading.Thread(
                    target=operator_change, args=(APIKeyManager(keys_file),)
                )
                operator.start()
                operator.join(timeout=1.0)
                state["operator"] = operator
            return data

        monkeypatch.setattr(APIKeyManager, "_load_keys", load_then_operator_acts)
        manager.update_last_used(key)  # the in-flight request's bookkeeping
        state["operator"].join(timeout=10)
        assert not state["operator"].is_alive()
        return key

    def test_revoke_during_a_request_stays_revoked(self, temp_dir, monkeypatch):
        keys_file = temp_dir / "keys.json"
        key = self._interleave(keys_file, monkeypatch, lambda m: m.revoke_key("agent"))
        assert APIKeyManager(keys_file).validate_key(key) is None

    def test_disable_during_a_request_stays_disabled(self, temp_dir, monkeypatch):
        keys_file = temp_dir / "keys.json"
        key = self._interleave(keys_file, monkeypatch, lambda m: m.set_enabled("agent", False))
        assert APIKeyManager(keys_file).validate_key(key)["enabled"] is False


class TestKeysFileLockAcrossProcesses:
    """api-proxy #19: the CLI and the server are separate processes, so the
    lock that keeps one writer from undoing another must hold across them,
    must not outlive a holder that dies, and must not stall the server."""

    def test_a_write_waits_for_a_lock_held_by_another_process(self, temp_dir, lock_holder):
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        manager.create_key("agent")
        holder = lock_holder(keys_file, 1.0)

        started = time.monotonic()
        assert manager.revoke_key("agent") is True
        waited = time.monotonic() - started

        assert waited >= 0.5  # it waited for the other process to let go
        holder.wait(timeout=5)
        assert manager.get_key_by_name("agent") is None

    def test_a_killed_lock_holder_leaves_no_stale_lock(self, temp_dir, lock_holder, monkeypatch):
        monkeypatch.setattr(auth, "KEYS_LOCK_TIMEOUT_SECONDS", 3.0)
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        manager.create_key("agent")
        holder = lock_holder(keys_file, 60)

        holder.kill()
        holder.wait(timeout=5)
        started = time.monotonic()
        assert manager.revoke_key("agent") is True

        assert time.monotonic() - started < 1.0

    def test_a_cli_write_gives_up_with_an_error_when_the_lock_stays_held(
        self, temp_dir, lock_holder, monkeypatch
    ):
        monkeypatch.setattr(auth, "KEYS_LOCK_TIMEOUT_SECONDS", 0.2)
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        manager.create_key("agent")
        lock_holder(keys_file, 30)

        with pytest.raises(KeysFileLockTimeout, match="lock"):
            manager.revoke_key("agent")

        assert manager.get_key_by_name("agent") is not None  # nothing was written

    def test_a_request_skips_last_used_rather_than_wait_on_a_held_lock(
        self, temp_dir, lock_holder, monkeypatch, caplog
    ):
        """update_last_used runs on the server's event loop: a long wait there
        stalls every request. It is bookkeeping for an already-authenticated
        request, so it gives up after a short wait and logs a warning."""
        monkeypatch.setattr(auth, "LAST_USED_LOCK_TIMEOUT_SECONDS", 0.2)
        keys_file = temp_dir / "keys.json"
        manager = APIKeyManager(keys_file)
        key = manager.create_key("agent")
        lock_holder(keys_file, 30)

        started = time.monotonic()
        with caplog.at_level(logging.WARNING, logger="api_proxy.auth"):
            manager.update_last_used(key)

        assert time.monotonic() - started < 2.0
        assert manager.validate_key(key)["last_used_at"] is None
        assert any("last_used_at" in r.getMessage() for r in caplog.records)
