"""Per-calendar approval exemption: configuration parsing and CLI wiring (issue #16).

The behavioural tests (which writes bypass the queue) live in
test_calendar_security.py; this module pins how the setting is read.
"""

import sys

import pytest

from api_proxy import main as main_mod
from api_proxy.config import Config, parse_exempt_calendars

EARMARKS = (
    "c_dee653a1a8d29be6243468773c8c7909bd3936ac2ff477fad3dd3852b3eeb6bf@group.calendar.google.com"
)


class TestParseExemptCalendars:
    """Unset / empty / whitespace-only → no exemptions; otherwise exact ids."""

    @pytest.mark.parametrize("raw", [None, "", "   ", ",", " , ,\t"], ids=repr)
    def test_blank_forms_yield_no_exemptions(self, raw):
        assert parse_exempt_calendars(raw) == frozenset()

    def test_single_id(self):
        assert parse_exempt_calendars(EARMARKS) == frozenset({EARMARKS})

    def test_comma_separated_ids_are_trimmed(self):
        raw = f"  {EARMARKS} , other@group.calendar.google.com ,"
        assert parse_exempt_calendars(raw) == frozenset(
            {EARMARKS, "other@group.calendar.google.com"}
        )

    def test_ids_are_kept_verbatim_not_normalised(self):
        """No case folding or decoding: matching is exact-string equality."""
        raw = "Mixed.Case@group.calendar.google.com"
        assert parse_exempt_calendars(raw) == frozenset({raw})


class TestConfigDefault:
    def test_default_is_no_exemptions(self):
        assert Config().approval_exempt_calendars == frozenset()


def _parse(monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["api-proxy", *argv])
    return main_mod.parse_args()


class TestCliWiring:
    """--approval-exempt-calendars falls back to APPROVAL_EXEMPT_CALENDARS; flag wins."""

    def test_unset_env_var_leaves_default_empty(self, monkeypatch):
        monkeypatch.delenv("APPROVAL_EXEMPT_CALENDARS", raising=False)
        assert _parse(monkeypatch, []).approval_exempt_calendars == ""

    def test_env_var_supplies_value(self, monkeypatch):
        monkeypatch.setenv("APPROVAL_EXEMPT_CALENDARS", EARMARKS)
        assert _parse(monkeypatch, []).approval_exempt_calendars == EARMARKS

    def test_flag_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("APPROVAL_EXEMPT_CALENDARS", EARMARKS)
        args = _parse(monkeypatch, ["--approval-exempt-calendars", "a@b.com"])
        assert args.approval_exempt_calendars == "a@b.com"
