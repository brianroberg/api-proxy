"""Main FastAPI application and CLI entry point."""

import argparse
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.gmail.client import close_gmail_client
from api_proxy.gmail.handlers import router as gmail_router
from api_proxy.models import ErrorResponse, HealthResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# PATH MATCHING
# =============================================================================


def matches_path_pattern(path: str, pattern: str) -> bool:
    """
    Check if a path matches a pattern with {placeholder} wildcards.

    Args:
        path: The actual request path (e.g., "/gmail/v1/users/me/messages")
        pattern: The pattern to match (e.g., "/gmail/v1/users/{user_id}/messages")

    Returns:
        True if the path matches the pattern, False otherwise.
    """
    pattern_parts = pattern.lower().split("/")
    path_parts = path.lower().split("/")

    if len(path_parts) != len(pattern_parts):
        return False

    for pattern_part, path_part in zip(pattern_parts, path_parts, strict=True):
        # Placeholders like {user_id} match anything
        if pattern_part.startswith("{") and pattern_part.endswith("}"):
            continue
        if pattern_part != path_part:
            return False

    return True


# =============================================================================
# BLOCKED OPERATIONS - These paths are ALWAYS forbidden
# =============================================================================

BLOCKED_PATHS = [
    # Send operations
    "/gmail/v1/users/{user_id}/messages/send",
    # Draft operations
    "/gmail/v1/users/{user_id}/drafts",
    "/gmail/v1/users/{user_id}/drafts/send",
    "/gmail/v1/users/{user_id}/drafts/{draft_id}",
    # Import/insert operations
    "/gmail/v1/users/{user_id}/messages/import",
    "/gmail/v1/users/{user_id}/messages/insert",
]

# Allowed operations with their HTTP methods
ALLOWED_OPERATIONS = [
    # Read operations
    ("GET", "/gmail/v1/users/{user_id}/messages"),
    ("GET", "/gmail/v1/users/{user_id}/messages/{message_id}"),
    ("GET", "/gmail/v1/users/{user_id}/labels"),
    ("GET", "/gmail/v1/users/{user_id}/labels/{label_id}"),
    # Modify operations
    ("POST", "/gmail/v1/users/{user_id}/messages/{message_id}/modify"),
    ("POST", "/gmail/v1/users/{user_id}/messages/{message_id}/trash"),
    ("POST", "/gmail/v1/users/{user_id}/messages/{message_id}/untrash"),
]


def is_blocked_path(path: str) -> bool:
    """
    Check if a path matches any blocked pattern.
    """
    path = path.rstrip("/")
    return any(matches_path_pattern(path, pattern) for pattern in BLOCKED_PATHS)


def is_allowed_path(path: str, method: str) -> bool:
    """
    Check if a path/method combination is explicitly allowed.
    Allowlist approach: if not in allowed list, it's blocked.
    """
    path = path.rstrip("/")

    # Health check is always allowed (handled separately, no auth)
    if path.lower() == "/health":
        return True

    for allowed_method, pattern in ALLOWED_OPERATIONS:
        if method.upper() == allowed_method and matches_path_pattern(path, pattern):
            return True

    return False


# =============================================================================
# APPLICATION SETUP
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("API Proxy starting up...")
    yield
    logger.info("API Proxy shutting down...")
    await close_gmail_client()


app = FastAPI(
    title="API Proxy",
    description="A proxy server that enforces capability restrictions between AI agents and backend APIs",
    version="0.1.0",
    lifespan=lifespan,
)


# =============================================================================
# MIDDLEWARE FOR BLOCKED OPERATIONS
# =============================================================================


@app.middleware("http")
async def check_blocked_operations(request: Request, call_next):
    """Middleware to block forbidden operations before authentication."""
    path = request.url.path
    method = request.method

    # Skip check for health endpoint
    if path == "/health":
        return await call_next(request)

    # First check if explicitly blocked (fail fast)
    if is_blocked_path(path):
        logger.warning(f"Blocked operation attempted: {method} {path}")
        return JSONResponse(
            status_code=403,
            content=ErrorResponse.forbidden_error(
                "This operation is not allowed"
            ).model_dump(),
        )

    # Then check if allowed (allowlist approach)
    if not is_allowed_path(path, method):
        logger.warning(f"Unknown endpoint accessed: {method} {path}")
        return JSONResponse(
            status_code=403,
            content=ErrorResponse.forbidden_error(
                "This operation is not allowed"
            ).model_dump(),
        )

    return await call_next(request)


# =============================================================================
# REQUEST LOGGING
# =============================================================================


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests."""
    response = await call_next(request)

    # Get API key name if available
    key_name = getattr(request.state, "api_key_name", None)
    key_info = f" (key: {key_name})" if key_name else ""

    logger.info(
        f"{request.method} {request.url.path} - {response.status_code}{key_info}"
    )

    return response


# =============================================================================
# EXCEPTION HANDLERS
# =============================================================================


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle HTTP exceptions with consistent error format."""
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse.proxy_error(str(detail)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle validation errors."""
    return JSONResponse(
        status_code=422,
        content=ErrorResponse.proxy_error("Invalid request parameters").model_dump(),
    )


# =============================================================================
# ROUTES
# =============================================================================


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint. No authentication required."""
    return HealthResponse()


# Include Gmail routes
app.include_router(gmail_router)


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="api-proxy",
        description="API Proxy server - enforces capability restrictions for AI agents",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    parser.add_argument(
        "--api-keys-file",
        type=Path,
        default=Path("api_keys.json"),
        help="Path to API keys file (default: api_keys.json)",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path("token.json"),
        help="Path to Google OAuth token file (default: token.json)",
    )

    # Confirmation mode (mutually exclusive)
    confirm_group = parser.add_mutually_exclusive_group()
    confirm_group.add_argument(
        "--confirm-all",
        action="store_true",
        help="Require confirmation for all requests",
    )
    confirm_group.add_argument(
        "--confirm-modify",
        action="store_true",
        help="Require confirmation for modify operations only (default)",
    )
    confirm_group.add_argument(
        "--no-confirm",
        action="store_true",
        help="Do not require any confirmation",
    )

    parser.add_argument(
        "--confirmation-timeout",
        type=float,
        default=300.0,
        help="Timeout for confirmation prompts in seconds (default: 300)",
    )

    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Determine confirmation mode
    if args.confirm_all:
        confirmation_mode = ConfirmationMode.ALL
    elif args.no_confirm:
        confirmation_mode = ConfirmationMode.NONE
    else:
        # Default is MODIFY
        confirmation_mode = ConfirmationMode.MODIFY

    # Create and set configuration
    config = Config(
        host=args.host,
        port=args.port,
        api_keys_file=args.api_keys_file,
        token_file=args.token_file,
        confirmation_mode=confirmation_mode,
        confirmation_timeout=args.confirmation_timeout if args.confirmation_timeout > 0 else None,
    )
    set_config(config)

    logger.info(f"Starting API Proxy on {config.host}:{config.port}")
    logger.info(f"Confirmation mode: {confirmation_mode.value}")
    logger.info(f"API keys file: {config.api_keys_file}")
    logger.info(f"Token file: {config.token_file}")

    try:
        uvicorn.run(
            "api_proxy.main:app",
            host=config.host,
            port=config.port,
            reload=args.reload,
        )
        return 0
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        return 0
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
