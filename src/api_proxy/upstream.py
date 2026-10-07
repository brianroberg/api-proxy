"""Transport failures between the proxy and the Google APIs (api-proxy #18)."""

import httpx

# Transport failures on Google's side of the proxy: Google unreachable, not
# answering in time, or dropping the connection. None of these is a fault in
# the proxy. Deliberately narrower than httpx.TransportError, so that
# LocalProtocolError, UnsupportedProtocol and ProxyError, which would point
# at a request the proxy itself built badly, stay unhandled 500s.
UPSTREAM_FAILURES = (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)

# Failures raised before the request left the proxy, so Google never saw it.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class UpstreamUnavailableError(RuntimeError):
    """
    A request to a Google API failed in transport.

    Subclasses RuntimeError, which the route handlers already catch and
    answer with a 502 ``backend_error`` and one log line. The message carries the upstream host and the exception type but
    not the exception's own text, which httpx may build from the request.
    """

    def __init__(self, host: str, cause: Exception):
        self.host = host
        name = type(cause).__name__
        if isinstance(cause, _NOT_SENT):
            message = f"Could not reach upstream {host} ({name}); the request was not sent"
        else:
            message = (
                f"No complete response from upstream {host} ({name}); the request "
                "may have reached it, so its outcome is unknown"
            )
        super().__init__(message)
