"""API key authentication middleware and utilities."""

import json
import logging
import os
import secrets
import string
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Header, HTTPException, Request

from api_proxy.config import get_config

logger = logging.getLogger(__name__)

# API key format: aproxy_ + 32 alphanumeric characters
API_KEY_PREFIX = "aproxy_"
API_KEY_LENGTH = 32
API_KEY_CHARS = string.ascii_lowercase + string.digits


class APIKeyManager:
    """Manages API key storage and validation."""

    def __init__(self, keys_file: Path):
        self.keys_file = keys_file

    def _load_keys(self) -> dict:
        """Load keys from file. Creates empty structure if file doesn't exist."""
        if not self.keys_file.exists():
            return {"keys": {}}

        try:
            with open(self.keys_file) as f:
                data = json.load(f)
                if "keys" not in data:
                    data["keys"] = {}
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load API keys file: {e}")
            return {"keys": {}}

    def _save_keys(self, data: dict) -> None:
        """Save keys to file atomically."""
        # Write to temp file first, then rename for atomicity
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=self.keys_file.parent, prefix=".api_keys_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(temp_path, self.keys_file)
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def generate_key(self) -> str:
        """Generate a new API key."""
        random_part = "".join(secrets.choice(API_KEY_CHARS) for _ in range(API_KEY_LENGTH))
        return f"{API_KEY_PREFIX}{random_part}"

    def create_key(self, name: str) -> str:
        """Create a new API key with the given name."""
        data = self._load_keys()

        # Check for duplicate names
        for key_data in data["keys"].values():
            if key_data.get("name") == name:
                raise ValueError(f"API key with name '{name}' already exists")

        # Validate name
        if not name or len(name) > 64:
            raise ValueError("Name must be between 1 and 64 characters")
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Name must contain only alphanumeric characters, hyphens, and underscores")

        key = self.generate_key()
        data["keys"][key] = {
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "last_used_at": None,
            "enabled": True,
        }
        self._save_keys(data)
        return key

    def get_key_by_name(self, name: str) -> tuple[str, dict] | None:
        """Get a key and its metadata by name."""
        data = self._load_keys()
        for key, key_data in data["keys"].items():
            if key_data.get("name") == name:
                return key, key_data
        return None

    def validate_key(self, key: str) -> dict | None:
        """
        Validate an API key and return its metadata if valid.
        Returns None if key is invalid or doesn't exist.
        """
        if not key or not key.startswith(API_KEY_PREFIX):
            return None

        data = self._load_keys()
        return data["keys"].get(key)

    def update_last_used(self, key: str) -> None:
        """Update the last_used_at timestamp for a key."""
        data = self._load_keys()
        if key in data["keys"]:
            data["keys"][key]["last_used_at"] = datetime.now(UTC).isoformat()
            self._save_keys(data)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a key by name. Returns True if successful."""
        data = self._load_keys()
        for key, key_data in data["keys"].items():
            if key_data.get("name") == name:
                data["keys"][key]["enabled"] = enabled
                self._save_keys(data)
                return True
        return False

    def revoke_key(self, name: str) -> bool:
        """Permanently delete a key by name. Returns True if successful."""
        data = self._load_keys()
        key_to_delete = None
        for key, key_data in data["keys"].items():
            if key_data.get("name") == name:
                key_to_delete = key
                break

        if key_to_delete:
            del data["keys"][key_to_delete]
            self._save_keys(data)
            return True
        return False

    def list_keys(self) -> list[dict]:
        """List all keys with their metadata (excluding the actual key values)."""
        data = self._load_keys()
        result = []
        for key, key_data in data["keys"].items():
            result.append({
                "name": key_data.get("name", "unknown"),
                "created_at": key_data.get("created_at"),
                "last_used_at": key_data.get("last_used_at"),
                "enabled": key_data.get("enabled", True),
                "key_suffix": key[-4:],  # Last 4 chars for identification
            })
        return result


def get_api_key_manager() -> APIKeyManager:
    """Get the API key manager instance."""
    config = get_config()
    return APIKeyManager(config.api_keys_file)


async def verify_api_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """
    FastAPI dependency that verifies the API key from the Authorization header.
    Returns the key metadata if valid.
    Raises HTTPException with appropriate status codes on failure.
    """
    if authorization is None:
        logger.warning("Request missing Authorization header")
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_error", "message": "Missing Authorization header"},
        )

    # Parse Bearer token
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("Invalid Authorization header format")
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_error", "message": "Invalid Authorization header format"},
        )

    key = parts[1].strip()
    if not key:
        logger.warning("Empty API key in Authorization header")
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_error", "message": "Invalid API key"},
        )

    manager = get_api_key_manager()
    key_data = manager.validate_key(key)

    if key_data is None:
        # Log only the prefix to avoid leaking invalid keys
        key_preview = key[:10] + "..." if len(key) > 10 else key
        logger.warning(f"Invalid API key attempted: {key_preview}")
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_error", "message": "Invalid API key"},
        )

    if not key_data.get("enabled", True):
        logger.warning(f"Disabled API key used: {key_data.get('name', 'unknown')}")
        raise HTTPException(
            status_code=403,
            detail={"error": "auth_error", "message": "API key is disabled"},
        )

    # Update last used timestamp
    manager.update_last_used(key)

    # Store key info in request state for logging
    request.state.api_key_name = key_data.get("name", "unknown")

    return key_data
