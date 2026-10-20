"""Transport failures between the proxy and the Google APIs (api-proxy #18)."""

import httpx

# Failures between the proxy and Google: Google unreachable, not answering in
# time, or dropping the connection; a forward proxy refusing to open the
# tunnel (ProxyError); or a response body that cannot be decoded
# (DecodingError, raised while reading a response Google already sent). None
# of these is a fault in the proxy's own code. Deliberately narrower than
# httpx.TransportError, so that LocalProtocolError and UnsupportedProtocol,
# which would point at a request the proxy itself built badly, stay unhandled
# 500s. TooManyRedirects is not listed: these clients leave follow_redirects
# off, so httpx returns a redirect response instead of raising it.
UPSTREAM_FAILURES = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
    httpx.DecodingError,
)

# Failures raised before the request left the proxy, so Google never saw it.
# ProxyError is raised while the tunnel through the forward proxy is being
# set up (a refused CONNECT, or a failed SOCKS handshake), before the request
# to Google is written.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError)


# The 502 ``error`` value for a request that may have reached Google: no
# complete answer came back, so a write may or may not have been applied.
BACKEND_OUTCOME_UNKNOWN = "backend_outcome_unknown"


class UpstreamUnavailableError(RuntimeError):
    """
    A request to a Google API failed in transport, or its response body could
    not be decoded (see UPSTREAM_FAILURES).

    Subclasses RuntimeError, which the route handlers already catch and
    answer with a 502 and one log line. The message carries the upstream host
    and the exception type but not the exception's own text, which httpx may
    build from the request. ``outcome_unknown`` is True when the request may
    have reached Google.
    """

    def __init__(self, host: str, cause: Exception):
        self.host = host
        self.outcome_unknown = not isinstance(cause, _NOT_SENT)
        name = type(cause).__name__
        if not self.outcome_unknown:
            message = f"Could not reach upstream {host} ({name}); the request was not sent"
        else:
            message = (
                f"No complete response from upstream {host} ({name}); the request "
                "may have reached it, so its outcome is unknown"
            )
        super().__init__(message)


def backend_failure_detail(e: Exception) -> dict:
    """
    The 502 body for a backend call that raised instead of answering.

    ``error`` is ``backend_outcome_unknown`` when the call may have reached
    Google (see UpstreamUnavailableError), and ``backend_error`` otherwise.
    The status is 502 either way, so a caller that checks only the status
    sees what it saw before the distinction existed.
    """
    unknown = isinstance(e, UpstreamUnavailableError) and e.outcome_unknown
    return {"error": BACKEND_OUTCOME_UNKNOWN if unknown else "backend_error", "message": str(e)}


def lookup_failure_detail(e: Exception) -> dict:
    """
    The 502 body for a failed read the proxy makes before a write.

    The write itself was not sent, so ``error`` is ``backend_error`` even
    when the read's own outcome is unknown.
    """
    return {
        "error": "backend_error",
        "message": f"The lookup before the write failed, so the write was not sent: {e}",
    }
