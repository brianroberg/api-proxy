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

import httpx
import pytest

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
]
# The request may have reached Google, so its outcome is unknown.
MAYBE_SENT = [
    pytest.param(httpx.ReadTimeout("timed out"), id="ReadTimeout"),
    pytest.param(httpx.ReadError("connection reset by peer"), id="ReadError"),
    pytest.param(
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        id="RemoteProtocolError",
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
