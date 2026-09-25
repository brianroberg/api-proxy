"""ConfirmationHandler.confirm with a web queue: the approval path production runs.

Production starts the proxy with --web-confirm, so every gated Gmail and Calendar
mutation goes through ConfirmationHandler.confirm's web branch into
WebConfirmationQueue. No test built a handler with a queue: the handler tests use
the console branch or patch the handler out, and the queue tests call add_request
directly. Returning APPROVED from that branch no matter what the operator did
passed the whole suite. These drive the real handler and the real queue together.
"""

import asyncio

import pytest

from api_proxy.config import Config, ConfirmationMode, set_config
from api_proxy.confirmation import (
    ConfirmationHandler,
    ConfirmationOutcome,
    ConfirmationRequest,
)
from api_proxy.web_confirmation import WebConfirmationQueue

DELETE_EVENT = ConfirmationRequest(
    method="DELETE", path="/calendar/v3/calendars/primary/events/e1", event_summary="Sync"
)


@pytest.fixture
def web_setup(api_keys_file, token_file):
    def _setup(timeout: float | None) -> tuple[ConfirmationHandler, WebConfirmationQueue]:
        set_config(
            Config(
                api_keys_file=api_keys_file,
                token_file=token_file,
                confirmation_mode=ConfirmationMode.MODIFY,
                confirmation_timeout=timeout,
            )
        )
        queue = WebConfirmationQueue()
        return ConfirmationHandler(web_queue=queue), queue

    return _setup


async def _first_pending(queue: WebConfirmationQueue) -> dict:
    for _ in range(400):
        pending = queue.get_pending_sync()
        if pending:
            return pending[0]
        await asyncio.sleep(0.005)
    raise AssertionError("the request never reached the queue")


@pytest.mark.parametrize(
    "decision,expected",
    [("approve", ConfirmationOutcome.APPROVED), ("reject", ConfirmationOutcome.REJECTED)],
)
@pytest.mark.parametrize("timeout", [5.0, None], ids=["with-expiry", "no-expiry"])
async def test_handler_returns_the_operators_decision(web_setup, decision, expected, timeout):
    """Also covers --confirmation-timeout 0 (no expiry), where the queue waits on
    the operator with no deadline."""
    handler, queue = web_setup(timeout)
    task = asyncio.create_task(handler.confirm(DELETE_EVENT))
    pending = await _first_pending(queue)
    assert await getattr(queue, decision)(pending["id"]) is True
    assert await asyncio.wait_for(task, 5) is expected


async def test_no_expiry_mode_keeps_waiting_for_the_operator(web_setup):
    handler, queue = web_setup(None)
    task = asyncio.create_task(handler.confirm(DELETE_EVENT))
    pending = await _first_pending(queue)
    await asyncio.sleep(0.2)
    assert not task.done()  # no decision yet: nothing may be forwarded
    await queue.reject(pending["id"])
    assert await asyncio.wait_for(task, 5) is ConfirmationOutcome.REJECTED


async def test_handler_reports_expiry_when_nobody_answers(web_setup):
    handler, _ = web_setup(0.05)
    assert await asyncio.wait_for(handler.confirm(DELETE_EVENT), 5) is ConfirmationOutcome.EXPIRED


async def test_every_request_field_reaches_the_dashboard(web_setup):
    """The operator decides from what the dashboard shows. In particular the
    attendee list and 'send notifications' must be visible before approving a
    write that would email people (#16)."""
    handler, queue = web_setup(5.0)
    request = ConfirmationRequest(
        method="PATCH",
        path="/calendar/v3/calendars/primary/events/e1",
        query_params={"sendUpdates": "all"},
        labels_to_add=["L1"],
        labels_to_remove=["L2"],
        message_sender="sender@example.com",
        message_subject="Subject line",
        draft_thread_id="thread-9",
        event_summary="Planning",
        event_attendees=["a@example.com", "b@example.com"],
        send_updates="all",
        event_start="2026-10-01T09:00:00-04:00",
        event_end="2026-10-01T10:00:00-04:00",
        rsvp_response="accepted",
    )
    task = asyncio.create_task(handler.confirm(request))
    pending = await _first_pending(queue)
    expected = {
        "method": "PATCH",
        "path": "/calendar/v3/calendars/primary/events/e1",
        "query_params": {"sendUpdates": "all"},
        "labels_to_add": ["L1"],
        "labels_to_remove": ["L2"],
        "message_sender": "sender@example.com",
        "message_subject": "Subject line",
        "draft_thread_id": "thread-9",
        "event_summary": "Planning",
        "event_attendees": ["a@example.com", "b@example.com"],
        "send_updates": "all",
        "event_start": "2026-10-01T09:00:00-04:00",
        "event_end": "2026-10-01T10:00:00-04:00",
        "rsvp_response": "accepted",
    }
    assert {k: pending[k] for k in expected} == expected
    await queue.reject(pending["id"])
    await asyncio.wait_for(task, 5)
