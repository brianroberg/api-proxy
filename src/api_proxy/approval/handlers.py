"""FastAPI routes for web-based approval."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from api_proxy.web_confirmation import get_web_queue

logger = logging.getLogger(__name__)


class QueueResponse(BaseModel):
    """Response model for queue listing."""

    pending: list[dict]


class ActionResponse(BaseModel):
    """Response model for approve/reject actions."""

    success: bool
    message: str


# Note: include_in_schema=False to exclude from OpenAPI docs
# These are internal UI endpoints, not part of the proxy's external API
router = APIRouter(prefix="/approval", tags=["approval"], include_in_schema=False)


@router.get("/", include_in_schema=False)
async def approval_ui():
    """Serve the approval UI HTML page."""
    static_dir = Path(__file__).parent / "static"
    html_file = static_dir / "index.html"
    if not html_file.exists():
        logger.error(f"Approval UI HTML file not found: {html_file}")
        raise HTTPException(
            status_code=404,
            detail={"error": "proxy_error", "message": "Approval UI not found"},
        )
    return FileResponse(html_file, media_type="text/html")


@router.get("/api/queue", response_model=QueueResponse)
async def get_queue():
    """Get list of pending confirmation requests."""
    queue = get_web_queue()
    pending = await queue.get_pending()
    return QueueResponse(pending=pending)


@router.post("/api/{request_id}/approve", response_model=ActionResponse)
async def approve_request(request_id: str):
    """Approve a pending request."""
    queue = get_web_queue()
    success = await queue.approve(request_id)
    if not success:
        logger.warning(f"Approve request failed: {request_id} not found or already processed")
        raise HTTPException(
            status_code=404,
            detail={"error": "proxy_error", "message": "Request not found or already processed"},
        )
    return ActionResponse(success=True, message="Request approved")


@router.post("/api/{request_id}/reject", response_model=ActionResponse)
async def reject_request(request_id: str):
    """Reject a pending request."""
    queue = get_web_queue()
    success = await queue.reject(request_id)
    if not success:
        logger.warning(f"Reject request failed: {request_id} not found or already processed")
        raise HTTPException(
            status_code=404,
            detail={"error": "proxy_error", "message": "Request not found or already processed"},
        )
    return ActionResponse(success=True, message="Request rejected")


@router.get("/api/events")
async def event_stream():
    """SSE stream of queue changes."""
    queue = get_web_queue()
    return StreamingResponse(
        queue.stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
