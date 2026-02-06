"""Shared Pydantic models for API responses."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "0.1.1"


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    message: str

    @classmethod
    def proxy_error(cls, message: str) -> "ErrorResponse":
        """Create a proxy-originated error response."""
        return cls(error="proxy_error", message=message)

    @classmethod
    def auth_error(cls, message: str) -> "ErrorResponse":
        """Create an authentication error response."""
        return cls(error="auth_error", message=message)

    @classmethod
    def forbidden_error(cls, message: str) -> "ErrorResponse":
        """Create a forbidden operation error response."""
        return cls(error="forbidden", message=message)

    @classmethod
    def backend_error(cls, message: str) -> "ErrorResponse":
        """Create a backend-originated error response."""
        return cls(error="backend_error", message=message)
