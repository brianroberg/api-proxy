"""Google Calendar API route handlers."""

import json
import logging
import re
import string
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api_proxy.auth import verify_api_key
from api_proxy.calendar.client import get_calendar_client
from api_proxy.calendar.models import EventRequest, RespondRequest
from api_proxy.config import get_config
from api_proxy.confirmation import (
    ConfirmationOutcome,
    ConfirmationRequest,
    get_confirmation_handler,
    requires_confirmation,
)

logger = logging.getLogger(__name__)

# Create router with authentication dependency
router = APIRouter(
    prefix="/calendar/v3",
    tags=["calendar"],
    dependencies=[Depends(verify_api_key)],
)

# Regex for validating calendarId - "primary" or email-like strings
# Note: Pattern includes # for holiday calendars like "en.usa#holiday@group.v.calendar.google.com"
CALENDAR_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._%+#-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$|^primary$")

# Regex for validating eventId - alphanumeric with some special chars
EVENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_calendar_id(calendar_id: str) -> str:
    """
    Basic validation of calendarId parameter.
    Accepts 'primary' or email-like strings. Calendar API will do further validation.
    """
    if not isinstance(calendar_id, str) or not calendar_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid calendarId parameter"},
        )
    # Allow 'primary' or basic email format
    if calendar_id != "primary" and not CALENDAR_ID_PATTERN.match(calendar_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid calendarId format"},
        )
    return calendar_id


def validate_event_id(event_id: str) -> str:
    """
    Basic validation of eventId.
    Accepts alphanumeric strings with underscores and hyphens.
    """
    if not isinstance(event_id, str) or not event_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid eventId"},
        )
    if not EVENT_ID_PATTERN.match(event_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid eventId format"},
        )
    return event_id


async def forward_response(response) -> JSONResponse:
    """Forward a Calendar API response to the caller."""
    try:
        # Handle 204 No Content responses (returned by DELETE operations)
        # These have no body, so we can't call response.json()
        if response.status_code == 204:
            return JSONResponse(status_code=204, content=None)

        content = response.json()
        # Check if this is a Calendar API error
        if response.status_code >= 400:
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "error": "backend_error",
                    "message": content.get("error", {}).get("message", "Backend API error"),
                    "details": content,
                },
            )
        return JSONResponse(status_code=response.status_code, content=content)
    except json.JSONDecodeError:
        # If we can't parse JSON, return error with raw content info
        logger.warning(f"Failed to parse JSON response from Calendar API: {response.status_code}")
        return JSONResponse(
            status_code=response.status_code,
            content={"error": "backend_error", "message": "Invalid JSON response from backend"},
        )


def _json_dict_or_none(response) -> dict | None:
    """Parse a backend body, returning None unless it is a JSON object."""
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _get_event_or_404(client, path: str) -> httpx.Response:
    """
    Fetch an event ahead of a write operation.

    Maps the failures every write path handles identically: a missing event
    raises a tagged 404 and a backend communication failure raises a tagged
    502. Any other response is returned for the caller to handle.
    """
    try:
        response = await client.request("GET", path)
    except (RuntimeError, httpx.HTTPError) as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e
    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={"error": "backend_error", "message": "Event not found"},
        )
    return response


async def _resolve_authenticated_user_email(client) -> str:
    """
    Resolve the authenticated account's email address via its primary
    calendar, whose id is the account's address.

    The attendee ``self`` flag cannot identify the caller: it marks the
    attendee matching the calendar an event copy sits on, so on a shared or
    delegated calendar it points at another person. The primary-calendar id
    is a best-effort identity signal — if it ever differs from the address
    the caller was invited under, the RSVP fails closed as "not an attendee"
    rather than touching anyone else's entry.
    """
    try:
        response = await client.request("GET", "/calendars/primary")
    except (RuntimeError, httpx.HTTPError) as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e

    email = None
    if response.status_code == 200:
        data = _json_dict_or_none(response)
        email = data.get("id") if data else None
    if not isinstance(email, str) or "@" not in email:
        logger.error(f"Could not resolve authenticated user email: {response.status_code}")
        raise HTTPException(
            status_code=502,
            detail={
                "error": "backend_error",
                "message": "Could not resolve the authenticated user's email address",
            },
        )
    return email


async def handle_confirmation(
    request: Request,
    method: str,
    path: str,
    is_modify: bool,
    event_summary: str | None = None,
    event_attendees: list[str] | None = None,
    send_updates: str | None = None,
    event_start: str | None = None,
    event_end: str | None = None,
    rsvp_response: str | None = None,
    calendar_id: str | None = None,
) -> None:
    """
    Handle confirmation if required. Raises HTTPException if rejected or if
    the confirmation window expired with no operator response.

    ``calendar_id`` is passed only by the event write handlers (create /
    update / patch / delete). When it is one of the configured
    approval-exempt calendars the write skips the gate — see
    ``_is_exempt_calendar_write`` — and the bypass is logged at INFO. A
    write whose ``sendUpdates`` would make the backend email the event's
    attendees is outward communication, not bookkeeping on an agent-owned
    calendar, so it never bypasses: it falls through to the gate like any
    other write.
    """
    if not requires_confirmation(method, is_modify):
        return

    if _is_exempt_calendar_write(is_modify, calendar_id) and not _sends_invitations(send_updates):
        key_name = getattr(request.state, "api_key_name", "unknown")
        logger.info(
            f"Approval bypassed for exempt calendar: {method} {path} "
            f"calendar_id={calendar_id} (key: {key_name})"
        )
        return

    handler = get_confirmation_handler()
    confirmation_request = ConfirmationRequest(
        method=method,
        path=path,
        query_params=dict(request.query_params) if request.query_params else None,
        event_summary=event_summary,
        event_attendees=event_attendees,
        send_updates=send_updates,
        event_start=event_start,
        event_end=event_end,
        rsvp_response=rsvp_response,
    )

    outcome = await handler.confirm(confirmation_request)
    if outcome is ConfirmationOutcome.APPROVED:
        return

    key_name = getattr(request.state, "api_key_name", "unknown")
    if outcome is ConfirmationOutcome.EXPIRED:
        # Nobody ever saw the prompt: distinguishable from a rejection so
        # callers know a retry (after notifying the operator) is reasonable.
        logger.warning(f"Confirmation expired unanswered: {method} {path} (key: {key_name})")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "confirmation_expired",
                "message": "Confirmation request expired before an operator responded",
            },
        )

    logger.warning(f"Request rejected by operator: {method} {path} (key: {key_name})")
    raise HTTPException(
        status_code=403,
        detail={"error": "forbidden", "message": "Request rejected by operator"},
    )


def _is_exempt_calendar_write(is_modify: bool, calendar_id: str | None) -> bool:
    """
    Whether this modifying operation targets an approval-exempt calendar.

    Exact string equality against ``Config.approval_exempt_calendars``: no
    case folding, no substring matching, no URL decoding here. The value
    compared is the ``calendarId`` path parameter as the route handler
    received it, which Starlette has already percent-decoded (a still-encoded
    ``%40`` would fail ``validate_calendar_id`` before reaching this point),
    so a request written as ``...%40group.calendar.google.com`` matches the
    decoded id in the configured list. Decoding again would be wrong:
    ``CALENDAR_ID_PATTERN`` admits a literal ``%`` in the local part, so a
    second decode could rewrite a legitimate id.

    Reads never qualify (``is_modify`` False), so in ALL mode a read on an
    exempt calendar is still confirmed. Note that the operative guard is the
    call-site list — only the four event write handlers pass ``calendar_id``
    at all, and every read handler leaves it ``None`` — so the ``is_modify``
    check here is belt-and-braces for a future caller that passes both. It
    is pinned by a direct-call test
    (``test_handle_confirmation_never_exempts_a_non_modify_call``).
    """
    if not is_modify or calendar_id is None:
        return False
    return calendar_id in get_config().approval_exempt_calendars


def _sends_invitations(send_updates: str | None) -> bool:
    """
    Whether this sendUpdates value would make the backend send invitation
    emails ("all" or "externalOnly").

    Used only to enrich the confirmation prompt. It is deliberately NOT the
    confirmation gate: every event mutation passes is_modify=True regardless
    of sendUpdates, so bare creates/updates are confirmed on the same footing
    as DELETE and /respond.
    """
    return send_updates is not None and send_updates in ("all", "externalOnly")


def _reject_if_has_attendees(body: EventRequest) -> None:
    """
    Reject requests that include attendees.

    This is a security measure to prevent the agent from sending calendar
    invitations on behalf of the user. Creating events with attendees could
    result in invitation emails being sent, which constitutes communication
    with others.
    """
    if body.attendees and len(body.attendees) > 0:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": "Creating or updating events with attendees is not allowed. "
                "Events with attendees could send invitations on your behalf.",
            },
        )


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _normalize_email(value) -> str:
    """
    Normalize an email for comparison: strip whitespace, lowercase ASCII only.

    Full Unicode folding (str.lower/str.casefold) can conflate distinct
    addresses (e.g. 'ß' folds to 'ss'), which would let a crafted attendee
    entry collide with the caller's address and misdirect the RSVP patch.
    Non-strings normalize to "" (never matches).
    """
    if not isinstance(value, str):
        return ""
    return value.strip().translate(_ASCII_LOWER)


def build_rsvp_attendees(attendees, user_email: str, new_status: str) -> list[dict] | None:
    """
    Build the single-entry attendee list for an RSVP patch.

    Identifies the caller's own entry in ``attendees`` by ASCII-case-
    insensitive email match against ``user_email`` (the authenticated
    account). Returns a one-entry list carrying only that attendee's email
    and the new ``responseStatus``, suitable for a PATCH with
    ``attendeesOmitted=true`` — the Calendar API then updates just that entry
    and leaves every other guest untouched. Returns ``None`` if the caller is
    not an attendee.
    """
    target = _normalize_email(user_email)
    if not target:
        return None
    for attendee in attendees or []:
        if _normalize_email(attendee.get("email")) == target:
            return [{"email": attendee["email"], "responseStatus": new_status}]
    return None


def _format_event_datetime(dt) -> str | None:
    """
    Format EventDateTime for display in confirmation prompt.

    Returns the dateTime (for timed events) or date (for all-day events).
    """
    if dt is None:
        return None
    # Handle both EventDateTime model and dict (from API response)
    if hasattr(dt, "dateTime"):
        return dt.dateTime or dt.date
    elif isinstance(dt, dict):
        return dt.get("dateTime") or dt.get("date")
    return None


# =============================================================================
# CALENDAR LIST (Read-only)
# =============================================================================


@router.get("/users/me/calendarList")
async def list_calendars(
    request: Request,
    maxResults: Annotated[int | None, Query()] = None,
    pageToken: Annotated[str | None, Query()] = None,
    showDeleted: Annotated[bool | None, Query()] = None,
    showHidden: Annotated[bool | None, Query()] = None,
):
    """List all calendars for the authenticated user."""
    path = "/users/me/calendarList"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if maxResults is not None:
        params["maxResults"] = maxResults
    if pageToken is not None:
        params["pageToken"] = pageToken
    if showDeleted is not None:
        params["showDeleted"] = showDeleted
    if showHidden is not None:
        params["showHidden"] = showHidden

    client = get_calendar_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/calendars/{calendar_id}")
async def get_calendar(request: Request, calendar_id: str):
    """Get metadata for a specific calendar."""
    calendar_id = validate_calendar_id(calendar_id)
    path = f"/calendars/{calendar_id}"

    await handle_confirmation(request, "GET", path, is_modify=False)

    client = get_calendar_client()
    try:
        response = await client.request("GET", path)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


# =============================================================================
# EVENTS - READ OPERATIONS
# =============================================================================


@router.get("/calendars/{calendar_id}/events")
async def list_events(
    request: Request,
    calendar_id: str,
    maxResults: Annotated[int | None, Query()] = None,
    pageToken: Annotated[str | None, Query()] = None,
    timeMin: Annotated[str | None, Query()] = None,
    timeMax: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    singleEvents: Annotated[bool | None, Query()] = None,
    orderBy: Annotated[str | None, Query()] = None,
    showDeleted: Annotated[bool | None, Query()] = None,
    updatedMin: Annotated[str | None, Query()] = None,
    syncToken: Annotated[str | None, Query()] = None,
):
    """List events in a calendar."""
    calendar_id = validate_calendar_id(calendar_id)
    path = f"/calendars/{calendar_id}/events"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if maxResults is not None:
        params["maxResults"] = maxResults
    if pageToken is not None:
        params["pageToken"] = pageToken
    if timeMin is not None:
        params["timeMin"] = timeMin
    if timeMax is not None:
        params["timeMax"] = timeMax
    if q is not None:
        params["q"] = q
    if singleEvents is not None:
        params["singleEvents"] = singleEvents
    if orderBy is not None:
        params["orderBy"] = orderBy
    if showDeleted is not None:
        params["showDeleted"] = showDeleted
    if updatedMin is not None:
        params["updatedMin"] = updatedMin
    if syncToken is not None:
        params["syncToken"] = syncToken

    client = get_calendar_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/calendars/{calendar_id}/events/{event_id}")
async def get_event(
    request: Request,
    calendar_id: str,
    event_id: str,
    timeZone: Annotated[str | None, Query()] = None,
):
    """Get a specific event by ID."""
    calendar_id = validate_calendar_id(calendar_id)
    event_id = validate_event_id(event_id)
    path = f"/calendars/{calendar_id}/events/{event_id}"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if timeZone is not None:
        params["timeZone"] = timeZone

    client = get_calendar_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


# =============================================================================
# EVENTS - CREATE OPERATION
# =============================================================================


@router.post("/calendars/{calendar_id}/events")
async def create_event(
    request: Request,
    calendar_id: str,
    body: EventRequest,
    sendUpdates: Annotated[str | None, Query()] = None,
    conferenceDataVersion: Annotated[int | None, Query()] = None,
):
    """Create a new event in a calendar."""
    calendar_id = validate_calendar_id(calendar_id)
    path = f"/calendars/{calendar_id}/events"

    # Block events with attendees (security: prevents sending invitations)
    _reject_if_has_attendees(body)

    # Extract attendee emails for confirmation prompt
    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    # Every event mutation is confirmed on the same footing as DELETE and
    # /respond (is_modify=True). sendUpdates only enriches the prompt when it
    # would send invitations; it no longer decides whether to confirm.
    await handle_confirmation(
        request,
        "POST",
        path,
        is_modify=True,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _sends_invitations(sendUpdates) else None,
        event_start=_format_event_datetime(body.start),
        event_end=_format_event_datetime(body.end),
        calendar_id=calendar_id,
    )

    params = {}
    if sendUpdates is not None:
        params["sendUpdates"] = sendUpdates
    if conferenceDataVersion is not None:
        params["conferenceDataVersion"] = conferenceDataVersion

    client = get_calendar_client()
    try:
        response = await client.request(
            "POST",
            path,
            params=params or None,
            json_body=body.model_dump(exclude_none=True),
        )
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


# =============================================================================
# EVENTS - UPDATE OPERATIONS
# =============================================================================


@router.put("/calendars/{calendar_id}/events/{event_id}")
async def update_event(
    request: Request,
    calendar_id: str,
    event_id: str,
    body: EventRequest,
    sendUpdates: Annotated[str | None, Query()] = None,
    conferenceDataVersion: Annotated[int | None, Query()] = None,
):
    """Update an event (full replacement)."""
    calendar_id = validate_calendar_id(calendar_id)
    event_id = validate_event_id(event_id)
    path = f"/calendars/{calendar_id}/events/{event_id}"

    # Block events with attendees (security: prevents sending invitations)
    _reject_if_has_attendees(body)

    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    # Every event mutation is confirmed on the same footing as DELETE and
    # /respond (is_modify=True). sendUpdates only enriches the prompt when it
    # would send invitations; it no longer decides whether to confirm.
    await handle_confirmation(
        request,
        "PUT",
        path,
        is_modify=True,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _sends_invitations(sendUpdates) else None,
        event_start=_format_event_datetime(body.start),
        event_end=_format_event_datetime(body.end),
        calendar_id=calendar_id,
    )

    params = {}
    if sendUpdates is not None:
        params["sendUpdates"] = sendUpdates
    if conferenceDataVersion is not None:
        params["conferenceDataVersion"] = conferenceDataVersion

    client = get_calendar_client()
    try:
        response = await client.request(
            "PUT",
            path,
            params=params or None,
            json_body=body.model_dump(exclude_none=True),
        )
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.patch("/calendars/{calendar_id}/events/{event_id}")
async def patch_event(
    request: Request,
    calendar_id: str,
    event_id: str,
    body: EventRequest,
    sendUpdates: Annotated[str | None, Query()] = None,
    conferenceDataVersion: Annotated[int | None, Query()] = None,
):
    """Partially update an event."""
    calendar_id = validate_calendar_id(calendar_id)
    event_id = validate_event_id(event_id)
    path = f"/calendars/{calendar_id}/events/{event_id}"

    # Block events with attendees (security: prevents sending invitations)
    _reject_if_has_attendees(body)

    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    # Every event mutation is confirmed on the same footing as DELETE and
    # /respond (is_modify=True). sendUpdates only enriches the prompt when it
    # would send invitations; it no longer decides whether to confirm.
    await handle_confirmation(
        request,
        "PATCH",
        path,
        is_modify=True,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _sends_invitations(sendUpdates) else None,
        event_start=_format_event_datetime(body.start),
        event_end=_format_event_datetime(body.end),
        calendar_id=calendar_id,
    )

    params = {}
    if sendUpdates is not None:
        params["sendUpdates"] = sendUpdates
    if conferenceDataVersion is not None:
        params["conferenceDataVersion"] = conferenceDataVersion

    client = get_calendar_client()
    try:
        response = await client.request(
            "PATCH",
            path,
            params=params or None,
            json_body=body.model_dump(exclude_none=True),
        )
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


# =============================================================================
# EVENTS - DELETE OPERATION (Always requires confirmation)
# =============================================================================


@router.delete("/calendars/{calendar_id}/events/{event_id}")
async def delete_event(
    request: Request,
    calendar_id: str,
    event_id: str,
    sendUpdates: Annotated[str | None, Query()] = None,
):
    """Delete an event. This operation always requires confirmation."""
    calendar_id = validate_calendar_id(calendar_id)
    event_id = validate_event_id(event_id)
    path = f"/calendars/{calendar_id}/events/{event_id}"

    # Fetch event to get summary and dates for confirmation display
    client = get_calendar_client()
    event_summary = f"Event ID: {event_id}"
    event_start = None
    event_end = None

    response = await _get_event_or_404(client, path)
    event_data = _json_dict_or_none(response) if response.status_code == 200 else None
    if event_data is not None:
        event_summary = event_data.get("summary", event_summary)
        event_start = _format_event_datetime(event_data.get("start"))
        event_end = _format_event_datetime(event_data.get("end"))
    else:
        logger.warning(f"Failed to fetch event metadata: {response.status_code}")

    # DELETE is confirmed like every other event write (is_modify=True),
    # subject to the per-calendar exemption.
    await handle_confirmation(
        request,
        "DELETE",
        path,
        is_modify=True,
        event_summary=event_summary,
        send_updates=sendUpdates,
        event_start=event_start,
        event_end=event_end,
        calendar_id=calendar_id,
    )

    params = {}
    if sendUpdates is not None:
        params["sendUpdates"] = sendUpdates

    try:
        response = await client.request("DELETE", path, params=params or None)
        return await forward_response(response)
    except (RuntimeError, httpx.HTTPError) as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


# =============================================================================
# EVENTS - RSVP (respond to an invitation)
# =============================================================================


def _not_an_attendee_error() -> HTTPException:
    """Error for RSVP attempts by a non-attendee."""
    return HTTPException(
        status_code=400,
        detail={
            "error": "proxy_error",
            "message": "You are not an attendee of this event; cannot RSVP.",
        },
    )


@router.post("/calendars/{calendar_id}/events/{event_id}/respond")
async def respond_to_event(
    request: Request,
    calendar_id: str,
    event_id: str,
    body: RespondRequest,
):
    """
    RSVP to an event by setting ONLY the caller's own responseStatus.

    Unlike PUT/PATCH, the caller supplies no attendee list — only a
    responseStatus. The proxy reads the event, identifies the authenticated
    account's own attendee entry by email (the ``self`` flag is not trusted:
    on a shared calendar it marks the calendar's owner, not the caller), and
    patches just that entry with ``attendeesOmitted=true`` so the backend
    merges it instead of replacing the attendee list. ``sendUpdates=none`` is
    forced so no invitation emails are sent, and like other modifying
    operations the write requires operator confirmation. This makes RSVP
    possible without weakening the attendee-write block that guards
    create/update/patch: the attendee entry is constructed server-side and
    the caller can never add, remove, or alter other guests.
    """
    calendar_id = validate_calendar_id(calendar_id)
    event_id = validate_event_id(event_id)
    path = f"/calendars/{calendar_id}/events/{event_id}"

    client = get_calendar_client()

    # Read the event to find the caller's attendee entry and to enrich the
    # confirmation prompt.
    get_response = await _get_event_or_404(client, path)
    if get_response.status_code != 200:
        return await forward_response(get_response)

    event_data = _json_dict_or_none(get_response)
    if event_data is None:
        logger.warning("Failed to parse event body from Calendar API for RSVP")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": "Invalid JSON response from backend"},
        )

    attendees = event_data.get("attendees") or []
    if not attendees:
        raise _not_an_attendee_error()

    user_email = await _resolve_authenticated_user_email(client)
    patched_attendees = build_rsvp_attendees(attendees, user_email, body.responseStatus)
    if patched_attendees is None:
        raise _not_an_attendee_error()

    # An RSVP is visible to the organizer, so treat it like any other
    # modifying operation and require confirmation before writing.
    await handle_confirmation(
        request,
        "POST",
        f"{path}/respond",
        is_modify=True,
        event_summary=event_data.get("summary", f"Event ID: {event_id}"),
        event_start=_format_event_datetime(event_data.get("start")),
        event_end=_format_event_datetime(event_data.get("end")),
        rsvp_response=body.responseStatus,
    )

    # Patch only the caller's own entry: attendeesOmitted makes the Calendar
    # API merge this entry instead of replacing the attendee list, and
    # sendUpdates=none is forced so no invitations go out.
    try:
        patch_response = await client.request(
            "PATCH",
            path,
            params={"sendUpdates": "none"},
            json_body={"attendees": patched_attendees, "attendeesOmitted": True},
        )
        return await forward_response(patch_response)
    except (RuntimeError, httpx.HTTPError) as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e
