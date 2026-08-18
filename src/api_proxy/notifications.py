"""Push notifications for approval-queue events via ntfy.

Without a notification, a queued approval request is silent: unless the
operator happens to have the dashboard open, the request expires unseen
(see ``confirmation_timeout`` in config.py) and the caller is told it
expired. This module pushes an ntfy notification when a request enters the
web approval queue, with a deep link to the dashboard, and a low-priority
follow-up when the request is resolved (approved/rejected/expired) so a
stale phone notification is not acted on.

Design constraints (issue #5):

- Notifications are best-effort and MUST never block, delay, or fail the
  approval flow: sends are fire-and-forget with a short timeout; every
  failure is logged and swallowed.
- Auth comes from the ``NTFY_TOKEN`` environment variable. The token is
  never logged or echoed. When it is unset, notifications are disabled
  cleanly (logged once, not per request).
- No third-party personal data in titles or bodies: ntfy messages are
  cached server-side and mirrored to every subscribed device. Attendee
  names/emails and message senders are deliberately never included --
  "Delete event: <summary>" style only.
- Only the web-confirmation queue notifies. Console mode does not: the
  approval dashboard is not mounted there (the notification's action button
  would dead-end) and console mode presumes an operator already at the
  terminal where the prompt is displayed.

Deferred (noted in issue #5): coalescing multiple simultaneous requests
into one summary notification. Each pending request carries its own
deadline and its own deep link, which a queue-depth summary would lose,
and batching would add timer state to a fire-and-forget path.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from api_proxy.config import get_config

if TYPE_CHECKING:
    from api_proxy.web_confirmation import PendingRequest

logger = logging.getLogger(__name__)

NTFY_TOKEN_ENV = "NTFY_TOKEN"

# Short timeout so a slow ntfy server cannot pin resources; the approval
# flow itself never awaits the send.
NOTIFY_TIMEOUT_SECONDS = 10.0

# Log the "notifications disabled" message once, not per request.
_disabled_logged = False

# Strong references to in-flight sends: asyncio holds only weak references
# to tasks, so without this a send could be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


def reset_notification_state() -> None:
    """Reset module state (for testing)."""
    global _disabled_logged
    _disabled_logged = False


def _get_token() -> str | None:
    """Return the ntfy token, or None (logging the disablement once)."""
    global _disabled_logged
    token = os.environ.get(NTFY_TOKEN_ENV)
    if not token:
        if not _disabled_logged:
            logger.info(f"{NTFY_TOKEN_ENV} not set; approval notifications are disabled")
            _disabled_logged = True
        return None
    return token


def _header_safe(value: str) -> str:
    """
    Make a string safe to place in an HTTP header.

    Collapses all whitespace (a newline in an event summary must not become
    a header injection) and RFC 2047-encodes values that are not Latin-1
    (ntfy decodes =?UTF-8?B?...?= titles natively).
    """
    value = " ".join(value.split())
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"=?UTF-8?B?{encoded}?="


def _dashboard_url(request_id: str) -> str:
    """Deep link to this request on the approval dashboard."""
    config = get_config()
    base = config.external_base_url or f"http://{config.host}:{config.port}"
    return f"{base.rstrip('/')}/approval/#{request_id}"


def describe_request(pending: PendingRequest) -> str:
    """
    One-line description of what is being approved.

    Deliberately excludes third-party personal data: attendee lists and
    message senders never appear. Event summaries and message subjects are
    the "Delete event: <summary>" style the privacy constraint calls for.
    """
    method = pending.method.upper()
    path = pending.path
    summary = pending.event_summary

    if path.endswith("/respond"):
        desc = (
            f"RSVP ({pending.rsvp_response}) to event" if pending.rsvp_response else "RSVP to event"
        )
        return f"{desc}: {summary}" if summary else desc
    if "/events" in path or summary:
        verb = {"DELETE": "Delete", "POST": "Create", "PUT": "Update", "PATCH": "Update"}.get(
            method, method
        )
        desc = f"{verb} event"
        return f"{desc}: {summary}" if summary else desc
    if "/drafts" in path:
        verb = {"POST": "Create", "PUT": "Update", "DELETE": "Delete"}.get(method, method)
        return f"{verb} draft"

    if path.endswith("/untrash"):
        base = "Untrash message"
    elif path.endswith("/trash"):
        base = "Trash message"
    elif path.endswith("/modify"):
        base = "Modify message labels"
    else:
        return f"{method} {path}"
    subject = pending.message_subject
    return f"{base}: {subject}" if subject else base


def build_pending_notification(pending: PendingRequest) -> tuple[dict[str, str], str]:
    """
    Build (headers, body) for a "request awaiting approval" push.

    The Authorization header is added at send time, never here, so this
    stays safe to log and to unit-test. Priority is high deliberately for
    this notification type only: iOS defers normal-priority pushes, and a
    deferred notification for a request that expires in minutes is useless.
    """
    config = get_config()
    timeout = config.confirmation_timeout

    lines = [f"{pending.method} {pending.path}"]
    if timeout is not None:
        expires_at = datetime.fromtimestamp(pending.created_at + timeout).astimezone()
        lines.append(f"Expires at {expires_at:%Y-%m-%d %H:%M:%S %Z} ({timeout:.0f}s window).")
        lines.append(
            "If nobody responds, the request expires and the caller is told it "
            "expired (not that it was rejected)."
        )
    else:
        lines.append("No expiry configured; the request waits until an operator responds.")

    headers = {
        "Title": _header_safe(f"Approval needed: {describe_request(pending)}"),
        "Priority": "high",
        "Actions": f"view, Review, {_dashboard_url(pending.id)}",
    }
    return headers, "\n".join(lines)


def build_resolution_notification(
    pending: PendingRequest, outcome: str
) -> tuple[dict[str, str], str]:
    """
    Build (headers, body) for a follow-up push after a request is resolved.

    ntfy cannot recall a delivered notification; a short low-priority
    follow-up keeps the operator from acting on a stale one.
    """
    consequence = {
        "approved": "Approved; the request was forwarded to the backend.",
        "rejected": "Rejected; the caller was told the operator rejected it.",
        "expired": "Expired with no operator response; the caller was told it expired.",
    }.get(outcome, outcome)

    headers = {
        "Title": _header_safe(f"{outcome.capitalize()}: {describe_request(pending)}"),
        "Priority": "low",
    }
    return headers, f"{pending.method} {pending.path}\n{consequence}"


async def _send(url: str, headers: dict[str, str], body: str) -> None:
    """POST one notification to ntfy. Logs and swallows every failure."""
    try:
        async with httpx.AsyncClient(timeout=NOTIFY_TIMEOUT_SECONDS) as client:
            response = await client.post(url, content=body.encode("utf-8"), headers=headers)
        if response.status_code >= 400:
            logger.warning(f"ntfy notification rejected: HTTP {response.status_code}")
    except Exception as e:
        # Never let a notification failure surface anywhere near the
        # approval flow. The exception text never contains the token.
        logger.warning(f"ntfy notification failed: {type(e).__name__}: {e}")


def _fire_and_forget(coro) -> None:
    """Schedule a send in the background without ever awaiting it inline."""

    async def _safe() -> None:
        try:
            await coro
        except Exception as e:
            logger.warning(f"ntfy notification failed: {type(e).__name__}: {e}")

    try:
        task = asyncio.get_running_loop().create_task(_safe())
    except RuntimeError:
        # No running event loop (e.g. sync test context): drop the send.
        coro.close()
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _dispatch(build, *args) -> None:
    """Build a notification and send it fire-and-forget. Never raises."""
    token = _get_token()
    if token is None:
        return
    try:
        headers, body = build(*args)
        headers = {"Authorization": f"Bearer {token}", **headers}
        url = get_config().ntfy_url
    except Exception as e:
        logger.warning(f"Failed to build ntfy notification: {type(e).__name__}: {e}")
        return
    _fire_and_forget(_send(url, headers, body))


def notify_request_pending(pending: PendingRequest) -> None:
    """Push a notification that a request is waiting for approval."""
    _dispatch(build_pending_notification, pending)


def notify_request_resolved(pending: PendingRequest, outcome: str) -> None:
    """Push a follow-up notification that a request was resolved.

    ``outcome`` is a ConfirmationOutcome value string
    ("approved" / "rejected" / "expired").
    """
    _dispatch(build_resolution_notification, pending, outcome)
