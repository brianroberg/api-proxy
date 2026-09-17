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
    # Cross-repo invariant: this window must stay strictly shorter than every
    # client's mutation timeout (calendar-agent calls mutating routes with
    # PROXY_CONFIRM_TIMEOUT, default 330s), otherwise the client gives up
    # before the operator decides and the outcome — including an approved
    # mutation that then executes — is undeliverable. Raising
    # confirmation_timeout therefore requires raising every client's mutation
    # timeout FIRST. Nothing enforces this across repos; see README
    # "Confirmation timeouts and client timeouts".
    confirmation_timeout: float | None = 300.0  # 5 minutes, None for no timeout
    web_confirmation: bool = False  # Use web-based confirmation instead of console

    # Approval notifications (ntfy). Sends happen only in web-confirmation
    # mode and only when the NTFY_TOKEN environment variable is set; see
    # notifications.py.
    ntfy_url: str = "https://ntfy.robergb.net/alerts-agent"
    # Externally reachable base URL of this proxy, used to build the
    # dashboard link in approval notifications (e.g. "https://proxy.example.com").
    # Falls back to http://{host}:{port} when unset.
    external_base_url: str | None = None

    # Calendars whose event writes (create/update/patch/delete) bypass the
    # approval queue. Matched by exact string equality against the decoded
    # calendarId path parameter; empty = no exemptions. Set from the
    # APPROVAL_EXEMPT_CALENDARS environment variable or
    # --approval-exempt-calendars, i.e. deployment config the calling agent
    # cannot edit. Every bypassed write is logged at INFO (see
    # calendar/handlers.py handle_confirmation).
    approval_exempt_calendars: frozenset[str] = frozenset()

    # API base URLs
    gmail_api_base_url: str = "https://gmail.googleapis.com"
    calendar_api_base_url: str = "https://www.googleapis.com/calendar/v3"


def parse_exempt_calendars(raw: str | None) -> frozenset[str]:
    """
    Parse the APPROVAL_EXEMPT_CALENDARS value: comma-separated calendar ids,
    whitespace-trimmed, empty entries dropped. None, empty and whitespace-only
    input all yield no exemptions. Ids are kept verbatim — no case folding or
    URL decoding — because matching is exact-string equality.
    """
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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
