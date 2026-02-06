"""Gmail API route handlers."""

import json
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from api_proxy.auth import verify_api_key
from api_proxy.confirmation import (
    ConfirmationRequest,
    get_confirmation_handler,
    requires_confirmation,
)
from api_proxy.gmail.client import get_gmail_client
from api_proxy.gmail.models import ModifyMessageRequest

logger = logging.getLogger(__name__)

# Create router with authentication dependency
router = APIRouter(
    prefix="/gmail/v1/users",
    tags=["gmail"],
    dependencies=[Depends(verify_api_key)],
)

# Regex for validating userId - basic validation, let Gmail handle the rest
USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$|^me$")

# Regex for validating message/label IDs - alphanumeric with some special chars
# Gmail IDs are typically base64-like strings
RESOURCE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_user_id(user_id: str) -> str:
    """
    Basic validation of userId parameter.
    Accepts 'me' or email-like strings. Gmail will do further validation.
    """
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid userId parameter"},
        )
    # Allow 'me' or basic email format
    if user_id != "me" and not USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": "Invalid userId format"},
        )
    return user_id


def validate_resource_id(resource_id: str, resource_type: str = "resource") -> str:
    """
    Basic validation of message/label IDs.
    Accepts alphanumeric strings with underscores and hyphens.
    """
    if not isinstance(resource_id, str) or not resource_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": f"Invalid {resource_type} ID"},
        )
    if not RESOURCE_ID_PATTERN.match(resource_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "proxy_error", "message": f"Invalid {resource_type} ID format"},
        )
    return resource_id


async def forward_response(response) -> JSONResponse:
    """Forward a Gmail API response to the caller."""
    try:
        content = response.json()
        # Check if this is a Gmail API error
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
        logger.warning(f"Failed to parse JSON response from Gmail API: {response.status_code}")
        return JSONResponse(
            status_code=response.status_code,
            content={"error": "backend_error", "message": "Invalid JSON response from backend"},
        )


async def fetch_message_context(user_id: str, message_id: str) -> dict[str, str | None]:
    """Fetch message subject, sender, and snippet for confirmation display."""
    client = get_gmail_client()
    try:
        path = f"/gmail/v1/users/{user_id}/messages/{message_id}"
        response = await client.request(
            "GET", path, params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
        )
        if response.status_code != 200:
            return {"subject": None, "from": None, "snippet": None}

        data = response.json()
        subject = None
        from_header = None
        headers = data.get("payload", {}).get("headers", [])
        for header in headers:
            name = header.get("name", "").lower()
            if name == "subject":
                subject = header.get("value")
            elif name == "from":
                from_header = header.get("value")
        return {
            "subject": subject,
            "from": from_header,
            "snippet": data.get("snippet"),
        }
    except Exception as e:
        logger.warning(f"Failed to fetch message context for confirmation: {e}")
        return {"subject": None, "from": None, "snippet": None}


async def handle_confirmation(
    request: Request,
    method: str,
    path: str,
    is_modify: bool,
    labels_to_add: list[str] | None = None,
    labels_to_remove: list[str] | None = None,
    message_subject: str | None = None,
    message_from: str | None = None,
    message_snippet: str | None = None,
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
        labels_to_add=labels_to_add,
        labels_to_remove=labels_to_remove,
        message_subject=message_subject,
        message_from=message_from,
        message_snippet=message_snippet,
    )

    approved = await handler.confirm(confirmation_request)
    if not approved:
        key_name = getattr(request.state, "api_key_name", "unknown")
        logger.warning(f"Request rejected by operator: {method} {path} (key: {key_name})")
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Request rejected by operator"},
        )


# =============================================================================
# READ OPERATIONS
# =============================================================================


@router.get("/{user_id}/messages")
async def list_messages(
    request: Request,
    user_id: str,
    maxResults: Annotated[int | None, Query()] = None,
    pageToken: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    labelIds: Annotated[list[str] | None, Query()] = None,
    includeSpamTrash: Annotated[bool | None, Query()] = None,
):
    """List messages in the user's mailbox."""
    user_id = validate_user_id(user_id)
    path = f"/gmail/v1/users/{user_id}/messages"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if maxResults is not None:
        params["maxResults"] = maxResults
    if pageToken is not None:
        params["pageToken"] = pageToken
    if q is not None:
        params["q"] = q
    if labelIds is not None:
        params["labelIds"] = labelIds
    if includeSpamTrash is not None:
        params["includeSpamTrash"] = includeSpamTrash

    client = get_gmail_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/{user_id}/messages/{message_id}")
async def get_message(
    request: Request,
    user_id: str,
    message_id: str,
    format: Annotated[str | None, Query()] = None,
    metadataHeaders: Annotated[list[str] | None, Query()] = None,
):
    """Get a specific message by ID."""
    user_id = validate_user_id(user_id)
    message_id = validate_resource_id(message_id, "message")
    path = f"/gmail/v1/users/{user_id}/messages/{message_id}"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if format is not None:
        params["format"] = format
    if metadataHeaders is not None:
        params["metadataHeaders"] = metadataHeaders

    client = get_gmail_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/{user_id}/threads/{thread_id}")
async def get_thread(
    request: Request,
    user_id: str,
    thread_id: str,
    format: Annotated[str | None, Query()] = None,
    metadataHeaders: Annotated[list[str] | None, Query()] = None,
):
    """Get a specific thread by ID."""
    user_id = validate_user_id(user_id)
    thread_id = validate_resource_id(thread_id, "thread")
    path = f"/gmail/v1/users/{user_id}/threads/{thread_id}"

    await handle_confirmation(request, "GET", path, is_modify=False)

    params = {}
    if format is not None:
        params["format"] = format
    if metadataHeaders is not None:
        params["metadataHeaders"] = metadataHeaders

    client = get_gmail_client()
    try:
        response = await client.request("GET", path, params=params or None)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/{user_id}/labels")
async def list_labels(request: Request, user_id: str):
    """List all labels in the user's mailbox."""
    user_id = validate_user_id(user_id)
    path = f"/gmail/v1/users/{user_id}/labels"

    await handle_confirmation(request, "GET", path, is_modify=False)

    client = get_gmail_client()
    try:
        response = await client.request("GET", path)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.get("/{user_id}/labels/{label_id}")
async def get_label(request: Request, user_id: str, label_id: str):
    """Get a specific label by ID."""
    user_id = validate_user_id(user_id)
    label_id = validate_resource_id(label_id, "label")
    path = f"/gmail/v1/users/{user_id}/labels/{label_id}"

    await handle_confirmation(request, "GET", path, is_modify=False)

    client = get_gmail_client()
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
# MODIFY OPERATIONS
# =============================================================================


@router.post("/{user_id}/messages/{message_id}/modify")
async def modify_message(
    request: Request,
    user_id: str,
    message_id: str,
    body: ModifyMessageRequest,
):
    """Modify labels on a message."""
    user_id = validate_user_id(user_id)
    message_id = validate_resource_id(message_id, "message")
    path = f"/gmail/v1/users/{user_id}/messages/{message_id}/modify"

    # Label modifications are not gated — this is the email-labeler's core operation.
    # Destructive operations (trash/untrash) still require confirmation.

    client = get_gmail_client()
    try:
        response = await client.request(
            "POST",
            path,
            json_body=body.model_dump(exclude_none=True),
        )
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.post("/{user_id}/messages/{message_id}/trash")
async def trash_message(request: Request, user_id: str, message_id: str):
    """Move a message to trash."""
    user_id = validate_user_id(user_id)
    message_id = validate_resource_id(message_id, "message")
    path = f"/gmail/v1/users/{user_id}/messages/{message_id}/trash"

    if requires_confirmation("POST", is_modify_operation=True):
        ctx = await fetch_message_context(user_id, message_id)
        await handle_confirmation(
            request, "POST", path, is_modify=True,
            message_subject=ctx["subject"], message_from=ctx["from"], message_snippet=ctx["snippet"],
        )

    client = get_gmail_client()
    try:
        response = await client.request("POST", path)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e


@router.post("/{user_id}/messages/{message_id}/untrash")
async def untrash_message(request: Request, user_id: str, message_id: str):
    """Remove a message from trash."""
    user_id = validate_user_id(user_id)
    message_id = validate_resource_id(message_id, "message")
    path = f"/gmail/v1/users/{user_id}/messages/{message_id}/untrash"

    if requires_confirmation("POST", is_modify_operation=True):
        ctx = await fetch_message_context(user_id, message_id)
        await handle_confirmation(
            request, "POST", path, is_modify=True,
            message_subject=ctx["subject"], message_from=ctx["from"], message_snippet=ctx["snippet"],
        )

    client = get_gmail_client()
    try:
        response = await client.request("POST", path)
        return await forward_response(response)
    except RuntimeError as e:
        logger.error(f"Backend communication error: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": "backend_error", "message": str(e)},
        ) from e
