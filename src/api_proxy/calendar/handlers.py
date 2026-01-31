"""Google Calendar API route handlers."""

import json
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api_proxy.auth import verify_api_key
from api_proxy.calendar.client import get_calendar_client
from api_proxy.calendar.models import EventRequest
from api_proxy.confirmation import (
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


async def handle_confirmation(
    request: Request,
    method: str,
    path: str,
    is_modify: bool,
    event_summary: str | None = None,
    event_attendees: list[str] | None = None,
    send_updates: str | None = None,
) -> None:
    """
    Handle confirmation if required. Raises HTTPException if rejected.
    """
    if not requires_confirmation(method, is_modify):
        return

    handler = get_confirmation_handler()
    confirmation_request = ConfirmationRequest(
        method=method,
        path=path,
        query_params=dict(request.query_params) if request.query_params else None,
        event_summary=event_summary,
        event_attendees=event_attendees,
        send_updates=send_updates,
    )

    approved = await handler.confirm(confirmation_request)
    if not approved:
        key_name = getattr(request.state, "api_key_name", "unknown")
        logger.warning(f"Request rejected by operator: {method} {path} (key: {key_name})")
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Request rejected by operator"},
        )


def _should_confirm_invitation(send_updates: str | None) -> bool:
    """Check if the sendUpdates parameter requires confirmation."""
    return send_updates is not None and send_updates in ("all", "externalOnly")


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

    # Determine if confirmation is needed
    # - Always confirm if sendUpdates is "all" or "externalOnly" (sending invitations)
    is_modify = _should_confirm_invitation(sendUpdates)

    # Extract attendee emails for confirmation prompt
    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    await handle_confirmation(
        request,
        "POST",
        path,
        is_modify=is_modify,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _should_confirm_invitation(sendUpdates) else None,
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

    # Confirm if sending invitations
    is_modify = _should_confirm_invitation(sendUpdates)

    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    await handle_confirmation(
        request,
        "PUT",
        path,
        is_modify=is_modify,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _should_confirm_invitation(sendUpdates) else None,
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

    # Confirm if sending invitations
    is_modify = _should_confirm_invitation(sendUpdates)

    attendee_emails = None
    if body.attendees:
        attendee_emails = [a.email for a in body.attendees]

    await handle_confirmation(
        request,
        "PATCH",
        path,
        is_modify=is_modify,
        event_summary=body.summary,
        event_attendees=attendee_emails,
        send_updates=sendUpdates if _should_confirm_invitation(sendUpdates) else None,
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

    # DELETE always requires confirmation (is_modify=True)
    await handle_confirmation(
        request,
        "DELETE",
        path,
        is_modify=True,
        event_summary=f"Event ID: {event_id}",
        send_updates=sendUpdates,
    )

    params = {}
    if sendUpdates is not None:
        params["sendUpdates"] = sendUpdates

    client = get_calendar_client()
    try:
        response = await client.request("DELETE", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e
