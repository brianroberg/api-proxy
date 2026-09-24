"""main() must carry the CLI flags into the live Config and confirmation handler.

Production runs ``api-proxy --web-confirm`` with no confirmation-mode flag, so the
approval gate for every Gmail and Calendar mutation rests on main()'s *default*
branch. The flag parsing is tested elsewhere; what these tests pin is the step
after it: which ConfirmationMode, timeout and queue the running proxy actually
gets. Without them, the default could become NONE (no approval at all) with every
other test green.
"""

import sys
from unittest.mock import patch

import pytest

from api_proxy import config as config_mod
from api_proxy import confirmation as confirmation_mod
from api_proxy import main as main_mod
from api_proxy import web_confirmation as web_confirmation_mod
from api_proxy.config import ConfirmationMode, get_config


@pytest.fixture
def run_main(monkeypatch, tmp_path):
    """Call main() with extra CLI args, uvicorn stubbed; restore globals after."""
    monkeypatch.delenv("APPROVAL_EXEMPT_CALENDARS", raising=False)
    monkeypatch.delenv("EXTERNAL_BASE_URL", raising=False)
    # main() replaces these module globals; monkeypatch puts the originals back.
    monkeypatch.setattr(config_mod, "_config", config_mod._config)
    monkeypatch.setattr(confirmation_mod, "_handler", None)
    monkeypatch.setattr(confirmation_mod, "_web_queue", None)
    monkeypatch.setattr(web_confirmation_mod, "_queue", None, raising=False)
    routes_before = list(main_mod.app.router.routes)

    def _run(*extra: str) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "api-proxy",
                "--api-keys-file",
                str(tmp_path / "keys.json"),
                "--token-file",
                str(tmp_path / "token.json"),
                *extra,
            ],
        )
        with patch("api_proxy.main.uvicorn.run") as mock_run:
            assert main_mod.main() == 0
        mock_run.assert_called_once()

    yield _run
    # --web-confirm mounts the approval router on the shared app; undo that.
    main_mod.app.router.routes[:] = routes_before


@pytest.mark.parametrize(
    "flags,expected_mode",
    [
        ((), ConfirmationMode.MODIFY),
        (("--web-confirm",), ConfirmationMode.MODIFY),
        (("--confirm-all",), ConfirmationMode.ALL),
        (("--no-confirm",), ConfirmationMode.NONE),
    ],
    ids=["default", "web-confirm-default", "confirm-all", "no-confirm"],
)
def test_confirmation_mode_reaches_config(run_main, flags, expected_mode):
    run_main(*flags)
    assert get_config().confirmation_mode is expected_mode


@pytest.mark.parametrize(
    "value,expected",
    [("300", 300.0), ("12", 12.0), ("0", None)],
)
def test_confirmation_timeout_reaches_config(run_main, value, expected):
    """A positive timeout is kept; 0 means 'wait forever' (None). Losing the
    timeout would let an approval land after the client gave up and still run."""
    run_main("--confirmation-timeout", value)
    assert get_config().confirmation_timeout == expected


def _approve_routes() -> int:
    # Other test modules mount the approval router on the shared app too, so
    # compare counts before and after main() rather than testing presence.
    return sum(
        1
        for route in main_mod.app.router.routes
        if getattr(route, "path", "") == "/approval/api/{request_id}/approve"
    )


def test_web_confirm_wires_the_queue_and_mounts_the_approval_routes(run_main):
    """Without the queue wired in, confirmations fall back to stdin, which is
    empty in a container: every mutation is rejected and no dashboard entry or
    push ever appears."""
    before = _approve_routes()
    run_main("--web-confirm")
    handler = confirmation_mod.get_confirmation_handler()
    assert handler._web_queue is web_confirmation_mod.get_web_queue()
    assert _approve_routes() == before + 1


def test_without_web_confirm_no_approval_routes_are_mounted(run_main):
    before = _approve_routes()
    run_main()
    assert _approve_routes() == before
    assert confirmation_mod.get_confirmation_handler()._web_queue is None
