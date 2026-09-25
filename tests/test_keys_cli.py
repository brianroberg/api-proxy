"""The api-proxy-keys CLI itself (api_proxy.keys.main).

test_keys.py exercises APIKeyManager directly; nothing ran the CLI, which had
0% coverage. `disable` and `revoke` are how the operator cuts an agent off, so a
command that printed success without changing the keys file would leave the
agent authorised while the operator believed it was cut off.
"""

import json
import sys

import pytest

from api_proxy import keys as keys_cli
from api_proxy.auth import APIKeyManager


@pytest.fixture
def keys_file(tmp_path):
    return tmp_path / "api_keys.json"


@pytest.fixture
def run(monkeypatch, keys_file, capsys):
    def _run(*argv: str) -> tuple[int, str, str]:
        monkeypatch.setattr(
            sys, "argv", ["api-proxy-keys", "--api-keys-file", str(keys_file), *argv]
        )
        code = keys_cli.main()
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _stored(keys_file) -> dict:
    return {
        v["name"]: {"key": k, **v} for k, v in json.loads(keys_file.read_text())["keys"].items()
    }


def test_create_writes_an_enabled_key_and_prints_it(run, keys_file):
    code, out, _ = run("create", "--name", "agent")
    assert code == 0
    stored = _stored(keys_file)["agent"]
    assert stored["enabled"] is True
    assert stored["key"] in out


def test_disable_then_enable_flip_the_stored_flag(run, keys_file):
    run("create", "--name", "agent")
    assert run("disable", "--name", "agent")[0] == 0
    assert _stored(keys_file)["agent"]["enabled"] is False
    assert run("enable", "--name", "agent")[0] == 0
    assert _stored(keys_file)["agent"]["enabled"] is True


def test_revoke_removes_the_key_so_it_no_longer_validates(run, keys_file):
    run("create", "--name", "agent")
    key = _stored(keys_file)["agent"]["key"]
    code, out, _ = run("revoke", "--name", "agent")
    assert code == 0
    assert "agent" not in _stored(keys_file)
    assert APIKeyManager(keys_file).validate_key(key) is None


@pytest.mark.parametrize("command", ["disable", "enable", "revoke", "show"])
def test_unknown_name_fails_with_not_found(run, command):
    code, out, err = run(command, "--name", "nobody")
    assert code == 1
    assert "not found" in err
    assert out == ""


def test_show_masks_all_but_the_last_four_characters(run, keys_file):
    run("create", "--name", "agent")
    key = _stored(keys_file)["agent"]["key"]
    code, out, _ = run("show", "--name", "agent")
    assert code == 0
    assert key not in out
    assert key[-4:] in out
    assert key[-5:] not in out  # only the last four characters are shown
    assert "Last Used:  never" in out


def test_list_empty_and_populated(run):
    code, out, _ = run("list")
    assert (code, out.strip()) == (0, "No API keys found.")
    run("create", "--name", "agent")
    code, out, _ = run("list")
    assert code == 0
    assert "agent" in out
    assert "never" in out


def test_list_shows_a_disabled_key_as_disabled(run):
    run("create", "--name", "agent")
    run("disable", "--name", "agent")
    code, out, _ = run("list")
    assert code == 0
    [row] = [line for line in out.splitlines() if line.startswith("agent")]
    assert row.split()[-1] == "no"


def test_create_with_a_duplicate_name_fails_and_adds_nothing(run, keys_file):
    run("create", "--name", "agent")
    code, _, err = run("create", "--name", "agent")
    assert code == 1
    assert "already exists" in err
    assert list(_stored(keys_file)) == ["agent"]
