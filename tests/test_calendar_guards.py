"""Calendar guards pinned so each one can fail on its own.

- The attendee block (#6) is the main protection against the agent sending
  invitations as the user, and on an APPROVAL_EXEMPT_CALENDARS calendar it is the
  only one: the #16 carve-out looks at sendUpdates alone. Removing it passed the
  whole suite, because the one test that sends attendees also gets its 403 from
  the operator rejecting the request.
- Id validation is what keeps "event1?sendUpdates=all" (sent as %3F) from
  reaching Google on an exempt calendar with no prompt; no test sent a bad id.
- PATCH/PUT must forward only the fields the caller set, and only modelled
  fields: explicit nulls clear fields in Google, and unmodelled keys widen
  what the agent can write.
- The backend client is spec'd here, so a call the real CalendarClient would
  reject (a misnamed keyword) fails instead of passing against a bare AsyncMock.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch
from urllib.parse import unquote

import httpx
import pytest

from api_proxy.calendar.client import CalendarClient
from api_proxy.calendar.handlers import build_rsvp_attendees
from api_proxy.calendar.models import EventRequest
from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import ConfirmationOutcome

EXEMPT = (
    "c_exempt0000000000000000000000000000000000000000000000000000000000@group.calendar.google.com"
)
EVENT = {
    "summary": "Planning",
    "start": {"dateTime": "2026-10-01T09:00:00-04:00"},
    "end": {"dateTime": "2026-10-01T10:00:00-04:00"},
}


def _config(api_keys_file, token_file, mode, exempt=()):
    set_config(
        Config(
            api_keys_file=api_keys_file,
            token_file=token_file,
            confirmation_mode=mode,
            confirmation_timeout=1.0,
            approval_exempt_calendars=frozenset(exempt),
        )
    )


@pytest.fixture
def backend():
    """A CalendarClient-shaped mock (wrong kwargs raise) and a watched confirm()."""
    client = create_autospec(CalendarClient, instance=True)
    response = MagicMock(status_code=200)
    response.json.return_value = {"id": "e1", "summary": "Planning"}
    client.request.return_value = response
    handler = MagicMock()
    handler.confirm = AsyncMock(return_value=ConfirmationOutcome.APPROVED)
    with (
        patch("api_proxy.calendar.handlers.get_calendar_client", return_value=client),
        patch("api_proxy.calendar.handlers.get_confirmation_handler", return_value=handler),
    ):
        yield client, handler


WRITES = [
    pytest.param("post", "/events", id="create"),
    pytest.param("put", "/events/event1", id="update"),
    pytest.param("patch", "/events/event1", id="patch"),
]


@pytest.mark.parametrize("calendar_id", ["primary", EXEMPT], ids=["primary", "exempt"])
@pytest.mark.parametrize("method,suffix", WRITES)
def test_a_write_carrying_attendees_is_refused_before_anything_else(
    client, auth_headers, api_keys_file, token_file, backend, calendar_id, method, suffix
):
    # Confirmation off, so a 403 can only come from the attendee block itself.
    _config(api_keys_file, token_file, ConfirmationMode.NONE, exempt={EXEMPT})
    mock_client, handler = backend
    body = {**EVENT, "attendees": [{"email": "guest@example.com"}]}

    response = getattr(client, method)(
        f"/calendar/v3/calendars/{calendar_id}{suffix}", json=body, headers=auth_headers
    )

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert "attendees" in response.json()["message"]
    mock_client.request.assert_not_awaited()
    handler.confirm.assert_not_awaited()


def test_an_empty_attendee_list_is_allowed(
    client, auth_headers, api_keys_file, token_file, backend
):
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend

    response = client.post(
        "/calendar/v3/calendars/primary/events",
        json={**EVENT, "attendees": []},
        headers=auth_headers,
    )

    assert response.status_code == 200
    mock_client.request.assert_awaited_once()


BAD_IDS = [
    pytest.param(
        "get",
        "/calendar/v3/calendars/primary/events/event1%3FsendUpdates=all",
        id="event-query-smuggle",
    ),
    pytest.param("delete", "/calendar/v3/calendars/primary/events/a.b", id="event-dot"),
    pytest.param("patch", "/calendar/v3/calendars/primary/events/e%20x", id="event-space"),
    pytest.param("get", "/calendar/v3/calendars/bad/events", id="calendar-not-email"),
    pytest.param("post", "/calendar/v3/calendars/a%20b@x.com/events", id="calendar-space"),
    pytest.param("get", "/calendar/v3/calendars/x@y/events", id="calendar-no-tld"),
    pytest.param("get", "/calendar/v3/calendars/x@y.com%3Ffoo/events", id="calendar-trailing-junk"),
]


@pytest.mark.parametrize("method,url", BAD_IDS)
def test_invalid_ids_are_400_before_any_upstream_call(
    client, auth_headers, api_keys_file, token_file, backend, method, url
):
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend
    kwargs = {"json": EVENT} if method in ("post", "put", "patch") else {}

    response = getattr(client, method)(url, headers=auth_headers, **kwargs)

    assert response.status_code == 400
    assert response.json()["error"] == "proxy_error"
    mock_client.request.assert_not_awaited()


@pytest.mark.parametrize("method", ["delete", "put", "patch"])
def test_query_smuggled_in_an_event_id_cannot_notify_via_an_exempt_calendar(
    client, auth_headers, api_keys_file, token_file, backend, method
):
    """On an exempt calendar only sendUpdates keeps a write in the queue; an
    eventId of 'event1?sendUpdates=all' would otherwise reach Google with no
    prompt and email every attendee."""
    _config(api_keys_file, token_file, ConfirmationMode.MODIFY, exempt={EXEMPT})
    mock_client, handler = backend
    kwargs = {} if method == "delete" else {"json": EVENT}

    response = getattr(client, method)(
        f"/calendar/v3/calendars/{EXEMPT}/events/event1%3FsendUpdates=all",
        headers=auth_headers,
        **kwargs,
    )

    assert response.status_code == 400
    mock_client.request.assert_not_awaited()
    handler.confirm.assert_not_awaited()


def _sent(mock_client) -> dict:
    """The last CalendarClient.request call as {parameter: value}, however it was passed."""
    call = mock_client.request.await_args
    bound = inspect.signature(CalendarClient.request).bind(None, *call.args, **call.kwargs)
    bound.apply_defaults()
    return {k: v for k, v in bound.arguments.items() if k != "self"}


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        pytest.param("post", "/events", EVENT, id="create"),
        pytest.param("put", "/events/event1", {"summary": "Renamed"}, id="update"),
        pytest.param("patch", "/events/event1", {"summary": "Renamed"}, id="patch"),
    ],
)
def test_writes_forward_only_the_fields_the_caller_set(
    client, auth_headers, api_keys_file, token_file, backend, method, suffix, body
):
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend

    response = getattr(client, method)(
        f"/calendar/v3/calendars/primary{suffix}", json=body, headers=auth_headers
    )

    assert response.status_code == 200
    sent = _sent(mock_client)
    assert (sent["method"], sent["path"]) == (method.upper(), f"/calendars/primary{suffix}")
    assert sent["json_body"] == body


def test_delete_forwards_send_updates_and_passes_the_204_through(
    client, auth_headers, api_keys_file, token_file, backend
):
    """Delete is the calendar write used every day. A misnamed keyword to the
    client, or a 204 fed to response.json(), passed against the bare AsyncMock
    and its fixture response, which returns {} for .json() even on a 204."""
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend
    mock_client.request.return_value = httpx.Response(204)

    response = client.delete(
        "/calendar/v3/calendars/primary/events/event1?sendUpdates=none", headers=auth_headers
    )

    assert response.status_code == 204
    assert b"backend_error" not in response.content  # a success is not reported as an error
    assert _sent(mock_client) == {
        "method": "DELETE",
        "path": "/calendars/primary/events/event1",
        "params": {"sendUpdates": "none"},
        "json_body": None,
    }


@pytest.mark.xfail(
    strict=True, reason="api-proxy #21: calendar delete also answers 204 with a 'null' body"
)
def test_delete_answers_204_with_no_body(client, auth_headers, api_keys_file, token_file, backend):
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend
    mock_client.request.return_value = httpx.Response(204)

    response = client.delete("/calendar/v3/calendars/primary/events/event1", headers=auth_headers)

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("method,suffix", WRITES)
def test_unmodelled_event_fields_are_never_forwarded(
    client, auth_headers, api_keys_file, token_file, backend, method, suffix
):
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    mock_client, _ = backend
    body = {
        **EVENT,
        "conferenceData": {"createRequest": {"requestId": "x"}},
        "attendeesOmitted": True,
        "organizer": {"email": "someone@example.com"},
        "extendedProperties": {"private": {"k": "v"}},
    }

    getattr(client, method)(
        f"/calendar/v3/calendars/primary{suffix}", json=body, headers=auth_headers
    )

    forwarded = mock_client.request.await_args.kwargs["json_body"]
    assert set(forwarded) <= set(EventRequest.model_fields)


def test_send_updates_all_create_is_put_to_the_operator_and_refusal_stops_it(
    client, auth_headers, api_keys_file, token_file, backend
):
    """Split from test_create_with_send_updates_all_requires_confirmation, which
    also sent attendees and so passed on either of two unrelated 403s."""
    _config(api_keys_file, token_file, ConfirmationMode.MODIFY)
    mock_client, handler = backend
    handler.confirm.return_value = ConfirmationOutcome.REJECTED

    response = client.post(
        "/calendar/v3/calendars/primary/events?sendUpdates=all", json=EVENT, headers=auth_headers
    )

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden", "message": "Request rejected by operator"}
    handler.confirm.assert_awaited_once()
    assert handler.confirm.await_args.args[0].send_updates == "all"
    mock_client.request.assert_not_awaited()


class TestRsvpMatchesOnlyTheCallersOwnAddress:
    """build_rsvp_attendees folds ASCII case only. The existing lookalike test
    uses 'ß', which only casefold() conflates, so str.lower() passed it; but
    lower() maps the KELVIN SIGN (U+212A) to 'k', letting 'Kevin@...' (with that
    sign) be patched as if it were the caller 'kevin@...'."""

    KELVIN = "K"

    def test_kelvin_sign_lookalike_is_not_the_caller(self):
        attendees = [{"email": f"{self.KELVIN}evin@example.com"}]
        assert build_rsvp_attendees(attendees, "kevin@example.com", "accepted") is None

    def test_caller_with_the_lookalike_does_not_match_a_plain_address(self):
        attendees = [{"email": "kevin@example.com"}]
        assert build_rsvp_attendees(attendees, f"{self.KELVIN}evin@example.com", "accepted") is None

    def test_surrounding_whitespace_and_ascii_case_still_match(self):
        attendees = [{"email": " Me@Example.com "}]
        assert build_rsvp_attendees(attendees, "me@example.com", "declined") == [
            {"email": " Me@Example.com ", "responseStatus": "declined"}
        ]

    def test_an_empty_caller_address_matches_nothing(self):
        attendees = [{"displayName": "No email"}, {"email": ""}]
        assert build_rsvp_attendees(attendees, "", "accepted") is None


@pytest.mark.xfail(strict=True, reason="api-proxy #22: '#' in a calendar id truncates the URL")
def test_calendar_id_with_hash_reaches_google_intact(
    client, auth_headers, api_keys_file, token_file, httpx_mock
):
    """The id regex admits '#' on purpose (holiday calendars), so the id must
    reach Google as one percent-encoded path segment, not end at a fragment."""
    _config(api_keys_file, token_file, ConfirmationMode.NONE)
    httpx_mock.add_response(json={"items": []})

    client.get(
        "/calendar/v3/calendars/en.usa%23holiday@group.v.calendar.google.com/events",
        headers=auth_headers,
    )

    [sent] = httpx_mock.get_requests()
    # Any correct encoding passes (e.g. '@' as %40); only the id's intact content is pinned.
    assert (
        unquote(sent.url.raw_path.decode())
        == "/calendar/v3/calendars/en.usa#holiday@group.v.calendar.google.com/events"
    )
