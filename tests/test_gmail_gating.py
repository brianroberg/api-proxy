"""Gmail routes under the approval modes production uses, with upstream calls pinned.

Every test in test_gmail_handlers.py runs with confirmation off, and no Gmail
test fixed the upstream HTTP method (pytest-httpx matches any method unless told).
So trash/untrash could run without an approval prompt in the default
--confirm-modify mode, a modify could drop its body, and trash or a draft update
or delete could use the wrong method, all with the suite green. Each response
here is registered with its method, and the confirmation handler is recorded.
"""

import json
import re
from unittest.mock import patch

import pytest

from api_proxy.confirmation import ConfirmationHandler, ConfirmationOutcome

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
METADATA_URL = re.compile(r"https://gmail\.googleapis\.com/gmail/v1/users/me/messages/m1\?.*")


@pytest.fixture
def recorded_confirm():
    """Replace confirm() with a recorder that returns a chosen outcome."""
    calls = []
    outcome = {"value": ConfirmationOutcome.APPROVED}

    async def fake_confirm(self, request):
        calls.append(request)
        return outcome["value"]

    with patch.object(ConfirmationHandler, "confirm", fake_confirm):
        yield calls, outcome


def _metadata(httpx_mock, status_code=200):
    httpx_mock.add_response(
        method="GET",
        url=METADATA_URL,
        status_code=status_code,
        json={
            "id": "m1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "a@x.com"},
                    {"name": "Subject", "value": "Hello"},
                ]
            },
        },
    )


@pytest.mark.parametrize("op", ["trash", "untrash"])
def test_trash_and_untrash_ask_for_approval_in_the_default_mode(
    client, auth_headers, config_confirm_modify, httpx_mock, recorded_confirm, op
):
    calls, _ = recorded_confirm
    _metadata(httpx_mock)
    httpx_mock.add_response(method="POST", url=f"{GMAIL}/messages/m1/{op}", json={"id": "m1"})

    response = client.post(f"/gmail/v1/users/me/messages/m1/{op}", headers=auth_headers)

    assert response.status_code == 200
    [request] = calls
    assert request.operation_type == op
    assert (request.message_sender, request.message_subject) == ("a@x.com", "Hello")


@pytest.mark.parametrize("op", ["trash", "untrash"])
@pytest.mark.parametrize(
    "outcome,error",
    [
        (ConfirmationOutcome.REJECTED, "forbidden"),
        (ConfirmationOutcome.EXPIRED, "confirmation_expired"),
    ],
)
def test_a_refused_trash_never_reaches_gmail(
    client, auth_headers, config_confirm_modify, httpx_mock, recorded_confirm, op, outcome, error
):
    _, chosen = recorded_confirm
    chosen["value"] = outcome
    _metadata(httpx_mock)

    response = client.post(f"/gmail/v1/users/me/messages/m1/{op}", headers=auth_headers)

    assert response.status_code == 403
    assert response.json()["error"] == error
    assert [r.method for r in httpx_mock.get_requests()] == ["GET"]  # metadata only


def test_trash_of_a_missing_message_is_404_before_any_prompt(
    client, auth_headers, config_confirm_modify, httpx_mock, recorded_confirm
):
    calls, _ = recorded_confirm
    _metadata(httpx_mock, status_code=404)

    response = client.post("/gmail/v1/users/me/messages/m1/trash", headers=auth_headers)

    assert response.status_code == 404
    assert calls == []


def test_label_changes_are_not_prompted_in_the_default_mode_and_forward_the_body(
    client, auth_headers, config_confirm_modify, httpx_mock, recorded_confirm
):
    calls, _ = recorded_confirm
    httpx_mock.add_response(method="POST", url=f"{GMAIL}/messages/m1/modify", json={"id": "m1"})

    response = client.post(
        "/gmail/v1/users/me/messages/m1/modify",
        json={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert calls == []
    [sent] = httpx_mock.get_requests()
    assert json.loads(sent.content) == {"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]}


def test_modify_leaves_an_absent_label_list_out_rather_than_sending_null(
    client, auth_headers, httpx_mock
):
    httpx_mock.add_response(method="POST", url=f"{GMAIL}/messages/m1/modify", json={"id": "m1"})

    client.post(
        "/gmail/v1/users/me/messages/m1/modify",
        json={"addLabelIds": ["STARRED"]},
        headers=auth_headers,
    )

    [sent] = httpx_mock.get_requests()
    assert json.loads(sent.content) == {"addLabelIds": ["STARRED"]}


def test_label_changes_under_confirm_all_show_names_and_forward_ids(
    client, auth_headers, config_confirm_all, httpx_mock, recorded_confirm
):
    calls, _ = recorded_confirm
    httpx_mock.add_response(
        method="GET",
        url=f"{GMAIL}/labels",
        json={
            "labels": [{"id": "Label_1", "name": "Custom"}, {"id": "STARRED", "name": "STARRED"}]
        },
    )
    _metadata(httpx_mock)
    httpx_mock.add_response(method="POST", url=f"{GMAIL}/messages/m1/modify", json={"id": "m1"})

    response = client.post(
        "/gmail/v1/users/me/messages/m1/modify",
        json={"addLabelIds": ["Label_1", "STARRED"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    [request] = calls
    assert request.labels_to_add == ["Custom", "STARRED"]
    assert (request.message_sender, request.message_subject) == ("a@x.com", "Hello")
    post = next(r for r in httpx_mock.get_requests() if r.method == "POST")
    assert json.loads(post.content) == {"addLabelIds": ["Label_1", "STARRED"]}


@pytest.mark.parametrize(
    "method,path,body,status",
    [
        ("PUT", "/gmail/v1/users/me/drafts/d1", {"message": {"raw": "cmF3"}}, 200),
        ("DELETE", "/gmail/v1/users/me/drafts/d1", None, 204),
    ],
)
def test_draft_update_and_delete_use_their_own_method_upstream(
    client, auth_headers, httpx_mock, method, path, body, status
):
    httpx_mock.add_response(
        method=method,
        url=f"https://gmail.googleapis.com{path}",
        status_code=status,
        json=None if status == 204 else {"id": "d1"},
    )

    response = client.request(method, path, json=body, headers=auth_headers)

    assert response.status_code == status
    [sent] = httpx_mock.get_requests()
    assert sent.method == method
