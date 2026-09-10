"""``--external-base-url`` falls back to the ``EXTERNAL_BASE_URL`` environment
variable, so a deployment can set it in compose ``environment:`` instead of
overriding the image's whole command line."""

import sys

import pytest

from api_proxy import main as main_mod


def _parse(monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["api-proxy", *argv])
    return main_mod.parse_args()


def test_env_var_supplies_external_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_BASE_URL", "http://docker-3:8000")
    assert _parse(monkeypatch, []).external_base_url == "http://docker-3:8000"


def test_flag_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_BASE_URL", "http://docker-3:8000")
    args = _parse(monkeypatch, ["--external-base-url", "https://proxy.example.com"])
    assert args.external_base_url == "https://proxy.example.com"


def test_unset_env_var_leaves_default_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXTERNAL_BASE_URL", raising=False)
    assert _parse(monkeypatch, []).external_base_url is None


def test_blank_env_var_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_BASE_URL", "   ")
    assert _parse(monkeypatch, []).external_base_url is None
