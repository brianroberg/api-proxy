"""Human-in-the-loop confirmation handling."""

import asyncio
import logging
import sys
from dataclasses import dataclass

from api_proxy.config import ConfirmationMode, get_config

logger = logging.getLogger(__name__)


@dataclass
class ConfirmationRequest:
    """Details of a request awaiting confirmation."""

    method: str
    path: str
    query_params: dict[str, str] | None = None
    # Gmail-specific fields
    labels_to_add: list[str] | None = None
    labels_to_remove: list[str] | None = None
    # Calendar-specific fields
    event_summary: str | None = None
    event_attendees: list[str] | None = None
    send_updates: str | None = None  # "all", "externalOnly", "none"


class ConfirmationHandler:
    """Handles human-in-the-loop confirmation for requests."""

    def __init__(self):
        # Lock to ensure only one confirmation at a time
        self._lock = asyncio.Lock()

    def _format_prompt(self, request: ConfirmationRequest) -> str:
        """Format the confirmation prompt for display."""
        lines = [f"[CONFIRM] {request.method} {request.path}"]

        if request.query_params:
            params_str = "&".join(f"{k}={v}" for k, v in request.query_params.items())
            lines.append(f"  Query: {params_str}")

        # Gmail-specific fields
        if request.labels_to_add:
            lines.append(f"  Add labels: {', '.join(request.labels_to_add)}")

        if request.labels_to_remove:
            lines.append(f"  Remove labels: {', '.join(request.labels_to_remove)}")

        # Calendar-specific fields
        if request.event_summary:
            lines.append(f"  Event: {request.event_summary}")

        if request.event_attendees:
            lines.append(f"  Attendees: {', '.join(request.event_attendees)}")

        if request.send_updates:
            lines.append(f"  Send notifications: {request.send_updates}")

        lines.append("Allow this request? [y/N]: ")

        return "\n".join(lines)

    async def _get_input(self, prompt: str, timeout: float | None) -> str | None:
        """Get input from stdin asynchronously with optional timeout."""
        sys.stdout.write(prompt)
        sys.stdout.flush()

        try:
            if timeout is not None:
                result = await asyncio.wait_for(
                    asyncio.to_thread(sys.stdin.readline),
                    timeout=timeout,
                )
            else:
                result = await asyncio.to_thread(sys.stdin.readline)
            return result.strip().lower()
        except TimeoutError:
            sys.stdout.write("\n[TIMEOUT] Confirmation timed out\n")
            sys.stdout.flush()
            return None

    async def confirm(self, request: ConfirmationRequest) -> bool:
        """
        Request confirmation from the operator.

        Returns True if approved, False if rejected or timed out.
        Only one confirmation can be pending at a time.
        """
        config = get_config()
        prompt = self._format_prompt(request)

        async with self._lock:
            response = await self._get_input(prompt, config.confirmation_timeout)

            if response in ("y", "yes"):
                logger.info(
                    f"Request APPROVED: {request.method} {request.path}"
                )
                sys.stdout.write("[APPROVED]\n")
                sys.stdout.flush()
                return True
            else:
                reason = "timed out" if response is None else "rejected"
                logger.info(
                    f"Request REJECTED ({reason}): {request.method} {request.path}"
                )
                if response is not None:
                    sys.stdout.write("[REJECTED]\n")
                    sys.stdout.flush()
                return False


# Global handler instance
_handler: ConfirmationHandler | None = None


def get_confirmation_handler() -> ConfirmationHandler:
    """Get the global confirmation handler instance."""
    global _handler
    if _handler is None:
        _handler = ConfirmationHandler()
    return _handler


def requires_confirmation(method: str, is_modify_operation: bool) -> bool:
    """
    Determine if a request requires confirmation based on the current mode.

    Args:
        method: HTTP method (GET, POST, etc.)
        is_modify_operation: True if this is a modify operation (label changes, trash, etc.)

    Returns:
        True if confirmation is required, False otherwise.
    """
    config = get_config()
    mode = config.confirmation_mode

    if mode == ConfirmationMode.NONE:
        return False
    elif mode == ConfirmationMode.ALL:
        return True
    elif mode == ConfirmationMode.MODIFY:
        return is_modify_operation
    else:
        return False
