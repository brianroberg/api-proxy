"""api-proxy #18: a transport failure between the proxy and Google is the
upstream's fault, not a proxy bug. It must come back as a 502 naming the
upstream host, with one log line and no traceback, instead of an unhandled
exception (a 500 and a ~130-line traceback in production).

Review item 1 (PR #25): the 502's ``error`` says whether Google may have
received the request. ``backend_error`` means it was not sent;
``backend_outcome_unknown`` means it may have been, so a write may have been
applied. A caller that switches on ``error`` can then re-read before retrying
a write instead of treating it as a definite failure."""

import logging
from unittest.mock import patch

import httpx
import pytest

from api_proxy.calendar.client import CalendarClient
from api_proxy.gmail.client import GmailClient
from api_proxy.upstream import UpstreamUnavailableError

EVENT = {
    "summary": "Planning",
    "start": {"dateTime": "2026-10-01T09:00:00-04:00"},
    "end": {"dateTime": "2026-10-01T10:00:00-04:00"},
}

ROUTES = [
    pytest.param(
        "GET", "/gmail/v1/users/me/messages", None, "gmail.googleapis.com", id="gmail-list"
    ),
    pytest.param(
        "DELETE", "/gmail/v1/users/me/drafts/d1", None, "gmail.googleapis.com", id="gmail-delete"
    ),
    pytest.param(
        "GET",
        "/calendar/v3/calendars/primary/events",
        None,
        "www.googleapis.com",
        id="calendar-list",
    ),
    pytest.param(
        "POST",
        "/calendar/v3/calendars/primary/events",
        EVENT,
        "www.googleapis.com",
        id="calendar-create",
    ),
]

# The request never left the proxy, so Google cannot have acted on it.
NOT_SENT = [
    pytest.param(httpx.ConnectError("All connection attempts failed"), id="ConnectError"),
    pytest.param(httpx.ConnectTimeout("timed out"), id="ConnectTimeout"),
    pytest.param(httpx.PoolTimeout("no free connection"), id="PoolTimeout"),
    # Raised while opening a tunnel through a forward proxy (the CONNECT was
    # refused) or during a SOCKS handshake, before Google is contacted.
    pytest.param(httpx.ProxyError("407 Proxy Authentication Required"), id="ProxyError"),
]
# The request may have reached Google, so its outcome is unknown.
MAYBE_SENT = [
    pytest.param(httpx.ReadTimeout("timed out"), id="ReadTimeout"),
    pytest.param(httpx.ReadError("connection reset by peer"), id="ReadError"),
    pytest.param(
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        id="RemoteProtocolError",
    ),
    # Raised while decoding the body of a response Google already sent.
    pytest.param(
        httpx.DecodingError("Error -3 while decompressing data: incorrect header check"),
        id="DecodingError",
    ),
]


# Each failure with the ``error`` value it must produce.
WITH_ERROR = [pytest.param(*p.values, "backend_error", id=p.id) for p in NOT_SENT] + [
    pytest.param(*p.values, "backend_outcome_unknown", id=p.id) for p in MAYBE_SENT
]


def _send(client, auth_headers, method, path, body):
    return client.request(method, path, json=body, headers=auth_headers)


def _problems(caplog):
    """The proxy's own log records at WARNING or above."""
    return [
        r for r in caplog.records if r.name.startswith("api_proxy") and r.levelno >= logging.WARNING
    ]


@pytest.mark.parametrize("exc,error", WITH_ERROR)
@pytest.mark.parametrize("method,path,body,host", ROUTES)
def test_upstream_transport_failure_is_a_502_with_one_log_line(
    client, auth_headers, httpx_mock, caplog, method, path, body, host, exc, error
):
    httpx_mock.add_exception(exc)

    with caplog.at_level(logging.INFO):
        response = _send(client, auth_headers, method, path, body)

    assert response.status_code == 502, response.text
    payload = response.json()
    assert payload["error"] == error
    assert host in payload["message"]
    assert type(exc).__name__ in payload["message"]

    [line] = _problems(caplog)  # exactly one, and it names the upstream and the error
    assert host in line.getMessage()
    assert type(exc).__name__ in line.getMessage()
    assert not any(r.exc_info for r in caplog.records)  # no traceback anywhere


@pytest.mark.parametrize("exc", NOT_SENT)
def test_a_failure_before_sending_says_the_request_was_not_sent(
    client, auth_headers, httpx_mock, exc
):
    httpx_mock.add_exception(exc)

    response = client.post(
        "/calendar/v3/calendars/primary/events", json=EVENT, headers=auth_headers
    )

    assert "was not sent" in response.json()["message"]


@pytest.mark.parametrize("exc", MAYBE_SENT)
def test_a_failure_after_sending_says_the_outcome_is_unknown(client, auth_headers, httpx_mock, exc):
    """A write that timed out waiting for Google's answer may still have been
    applied; the message must not claim it was not sent."""
    httpx_mock.add_exception(exc)

    response = client.post(
        "/calendar/v3/calendars/primary/events", json=EVENT, headers=auth_headers
    )

    message = response.json()["message"]
    assert "outcome is unknown" in message
    assert "not sent" not in message


@pytest.mark.parametrize(
    "path",
    ["/gmail/v1/users/me/messages", "/calendar/v3/calendars/primary/events"],
    ids=["gmail", "calendar"],
)
def test_a_proxy_side_error_is_not_reported_as_an_upstream_failure(
    client, auth_headers, httpx_mock, path
):
    """The mapping is kept to transport failures on Google's side. A local
    protocol error means the proxy built a bad request; reporting that as
    "upstream unreachable" would hide a proxy bug."""
    httpx_mock.add_exception(httpx.LocalProtocolError("Illegal header value"))

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 500


ATTENDING = dict(EVENT, attendees=[{"email": "me@example.com", "responseStatus": "needsAction"}])


@pytest.mark.parametrize(
    "path,body,answered",
    [
        pytest.param("/calendar/v3/calendars/primary/events/e1", None, [], id="delete-event-fetch"),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [],
            id="respond-event-fetch",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [ATTENDING],
            id="respond-own-address-lookup",
        ),
    ],
)
def test_a_failed_lookup_before_a_write_says_the_write_was_not_sent(
    client, auth_headers, httpx_mock, path, body, answered
):
    """Delete and RSVP read from Google before they write. When that read
    times out, the read's outcome is unknown but the write was never sent,
    so the answer is backend_error, not backend_outcome_unknown."""
    for event in answered:
        httpx_mock.add_response(json=event)
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))

    response = client.request(
        "DELETE" if body is None else "POST", path, json=body, headers=auth_headers
    )

    assert response.status_code == 502, response.text
    payload = response.json()
    assert payload["error"] == "backend_error"
    assert "the write was not sent" in payload["message"]
    assert all(sent.method == "GET" for sent in httpx_mock.get_requests())


@pytest.mark.parametrize("exc,error", WITH_ERROR)
def test_a_delete_that_fails_after_its_lookup_reports_the_delete_itself(
    client, auth_headers, httpx_mock, exc, error
):
    """The lookup succeeded and the DELETE itself failed: the error value
    describes the DELETE."""
    httpx_mock.add_response(json=EVENT)
    httpx_mock.add_exception(exc)

    response = client.delete("/calendar/v3/calendars/primary/events/e1", headers=auth_headers)

    assert response.status_code == 502, response.text
    assert response.json()["error"] == error
    assert [sent.method for sent in httpx_mock.get_requests()] == ["GET", "DELETE"]


ILLEGAL_HEADER = httpx.LocalProtocolError("Illegal header value b'Bearer mock_access_token\\n'")


@pytest.mark.parametrize(
    "path,body,answered",
    [
        pytest.param("/calendar/v3/calendars/primary/events/e1", None, [], id="delete-lookup"),
        pytest.param("/calendar/v3/calendars/primary/events/e1", None, [EVENT], id="delete"),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [ATTENDING],
            id="respond-own-address-lookup",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [ATTENDING, {"id": "me@example.com"}],
            id="respond-patch",
        ),
    ],
)
def test_a_proxy_side_error_on_a_calendar_write_is_a_500_without_its_text(
    client, auth_headers, httpx_mock, path, body, answered
):
    """Review item 7 (PR #25): delete and RSVP caught every httpx.HTTPError
    and answered a 502 carrying httpx's own message, which for an illegal
    header holds the Authorization value. Like every other route, they must
    leave a proxy-side error a 500 and keep that text out of the body."""
    for answer in answered:
        httpx_mock.add_response(json=answer)
    httpx_mock.add_exception(ILLEGAL_HEADER)

    response = client.request(
        "DELETE" if body is None else "POST", path, json=body, headers=auth_headers
    )

    assert response.status_code == 500
    assert "Illegal header value" not in response.text
    assert "mock_access_token" not in response.text


@pytest.mark.parametrize(
    "client_class,url,path",
    [
        pytest.param(
            GmailClient,
            "https://gmail.googleapis.com/gmail/v1/users/me/labels",
            "/gmail/v1/users/me/labels",
            id="gmail",
        ),
        pytest.param(
            CalendarClient,
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            "/calendars/primary/events",
            id="calendar",
        ),
    ],
)
async def test_a_transport_failure_on_the_retry_after_a_401_is_an_upstream_failure(
    test_config, httpx_mock, client_class, url, path
):
    """Review item 12 (PR #25): both clients retry once after a 401 with a
    refreshed token. The transport mapping must cover that retry too (the
    first request after a token expiry on a fresh container), or it escapes
    as a 500 with a traceback. Only Credentials.refresh is patched."""
    httpx_mock.add_response(method="GET", url=url, status_code=401, json={})
    httpx_mock.add_exception(httpx.ConnectError("All connection attempts failed"), url=url)

    def refresh(creds, request):
        creds.token = "fresh-token"

    client = client_class()
    try:
        with patch("google.oauth2.credentials.Credentials.refresh", refresh):
            with pytest.raises(UpstreamUnavailableError, match="was not sent"):
                await client.request("GET", path)
    finally:
        await client.close()

    assert [r.headers["Authorization"] for r in httpx_mock.get_requests()] == [
        "Bearer mock_access_token",
        "Bearer fresh-token",
    ]


# A body that claims gzip and is not: httpx raises DecodingError while reading
# it, after Google has answered.
CORRUPT_GZIP = {"headers": {"Content-Encoding": "gzip"}, "content": b"not gzip"}


@pytest.mark.parametrize("method,path,body,host", ROUTES)
def test_a_corrupt_response_body_is_a_502_whose_outcome_is_unknown(
    client, auth_headers, httpx_mock, caplog, method, path, body, host
):
    """Review round 2 item 2 (PR #25): a response body that fails to decode
    is not a proxy bug, and Google answered, so a write may have been
    applied. It must be a 502 saying the outcome is unknown, with one log line
    and no traceback, not an unhandled 500."""
    httpx_mock.add_response(**CORRUPT_GZIP)

    with caplog.at_level(logging.INFO):
        response = _send(client, auth_headers, method, path, body)

    assert response.status_code == 502, response.text
    payload = response.json()
    assert payload["error"] == "backend_outcome_unknown"
    assert host in payload["message"] and "DecodingError" in payload["message"]
    assert "incorrect header check" not in payload["message"]  # httpx's own text stays out
    [line] = _problems(caplog)
    assert "DecodingError" in line.getMessage()
    assert not any(r.exc_info for r in caplog.records)


@pytest.mark.parametrize(
    "path,body,answered,error",
    [
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1",
            None,
            [],
            "backend_error",
            id="delete-lookup",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1",
            None,
            [EVENT],
            "backend_outcome_unknown",
            id="delete",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [],
            "backend_error",
            id="respond-event-fetch",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [ATTENDING],
            "backend_error",
            id="respond-own-address-lookup",
        ),
        pytest.param(
            "/calendar/v3/calendars/primary/events/e1/respond",
            {"responseStatus": "accepted"},
            [ATTENDING, {"id": "me@example.com"}],
            "backend_outcome_unknown",
            id="respond-patch",
        ),
    ],
)
def test_a_corrupt_response_body_on_a_calendar_write_says_whether_the_write_was_sent(
    client, auth_headers, httpx_mock, path, body, answered, error
):
    """Review round 2 item 2 (PR #25): review item 7 narrowed delete's and
    RSVP's four except clauses to RuntimeError, so a DecodingError there
    became a 500. On the RSVP PATCH that is a write Google may have applied,
    reported with no backend_outcome_unknown signal. A corrupt body on a
    lookup means the write was not sent; on the write itself, the outcome is
    unknown."""
    for answer in answered:
        httpx_mock.add_response(json=answer)
    httpx_mock.add_response(**CORRUPT_GZIP)

    response = client.request(
        "DELETE" if body is None else "POST", path, json=body, headers=auth_headers
    )

    assert response.status_code == 502, response.text
    payload = response.json()
    assert payload["error"] == error
    assert "DecodingError" in payload["message"]
    if error == "backend_error":
        assert "the write was not sent" in payload["message"]
    assert len(httpx_mock.get_requests()) == len(answered) + 1
