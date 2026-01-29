"""Tests for API key management CLI."""

import json

import pytest

from api_proxy.auth import API_KEY_PREFIX, APIKeyManager


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
