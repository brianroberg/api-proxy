"""Configuration management for the API proxy."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ConfirmationMode(Enum):
    """Confirmation mode for requests."""

    NONE = "none"  # No confirmation required
    MODIFY = "modify"  # Confirmation for modify operations only (default)
    ALL = "all"  # Confirmation for all operations


@dataclass
class Config:
    """Application configuration."""

    # Server settings
    host: str = "127.0.0.1"
    port: int = 8000

    # File paths
    api_keys_file: Path = Path("api_keys.json")
    token_file: Path = Path("token.json")

    # Confirmation settings
    confirmation_mode: ConfirmationMode = ConfirmationMode.MODIFY
    confirmation_timeout: float | None = 300.0  # 5 minutes, None for no timeout

    # API base URLs
    gmail_api_base_url: str = "https://gmail.googleapis.com"
    calendar_api_base_url: str = "https://www.googleapis.com/calendar/v3"


# Global config instance, set during startup
_config: Config | None = None


def get_config() -> Config:
    """Get the current configuration."""
    if _config is None:
        raise RuntimeError("Configuration not initialized")
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration."""
    global _config
    _config = config
