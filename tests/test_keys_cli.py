"""The api-proxy-keys CLI itself (api_proxy.keys.main).

test_keys.py exercises APIKeyManager directly; nothing ran the CLI, which had
0% coverage. `disable` and `revoke` are how the operator cuts an agent off, so a
command that printed success without changing the keys file would leave the
agent authorised while the operator believed it was cut off.
"""

import json
import os
import sys

import pytest

from api_proxy import auth
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


WRITES = [
    pytest.param(("create", "--name", "other"), id="create"),
    pytest.param(("revoke", "--name", "agent"), id="revoke"),
    pytest.param(("disable", "--name", "agent"), id="disable"),
    pytest.param(("enable", "--name", "agent"), id="enable"),
]


@pytest.mark.parametrize("argv", WRITES)
def test_a_write_that_cannot_get_the_lock_says_so_and_exits_3(
    run, keys_file, lock_holder, monkeypatch, argv
):
    """Review item 2 (PR #25): when another process keeps the keys-file lock,
    a write gives up. It must say so on one 'Error:' line, not a traceback,
    and exit 3, so a script can tell 'try again' from 'no such key' (1)."""
    monkeypatch.setattr(auth, "KEYS_LOCK_TIMEOUT_SECONDS", 0.2)
    run("create", "--name", "agent")
    before = keys_file.read_bytes()
    lock_holder(keys_file, 30)

    code, out, err = run(*argv)

    assert code == 3
    assert err.startswith("Error: ") and "lock" in err
    assert len(err.strip().splitlines()) == 1
    assert out == ""
    assert keys_file.read_bytes() == before  # nothing was written


@pytest.mark.parametrize("argv", WRITES)
def test_a_write_that_cannot_open_or_take_the_lock_says_so_and_exits_3(
    run, keys_file, break_keys_lock, argv
):
    """Review item 3 (PR #25): a lock file the CLI cannot open or lock is
    reported like a busy lock: one 'Error:' line, exit 3, nothing written."""
    run("create", "--name", "agent")
    before = keys_file.read_bytes()
    break_keys_lock(keys_file)

    code, out, err = run(*argv)

    assert code == 3
    assert err.startswith("Error: ") and "lock" in err
    assert len(err.strip().splitlines()) == 1
    assert out == ""
    assert keys_file.read_bytes() == before


def _make_unreadable(keys_file):
    keys_file.chmod(0o000)


DAMAGE = [
    pytest.param(lambda f: f.write_text("not json{{{"), id="not-json"),
    pytest.param(lambda f: f.write_bytes(b"\xff\xfe{}"), id="not-utf8"),
    pytest.param(lambda f: f.write_text("[]"), id="not-an-object"),
    pytest.param(lambda f: f.write_text('{"keys": []}'), id="keys-not-an-object"),
    pytest.param(
        _make_unreadable,
        id="unreadable",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes"),
    ),
]


@pytest.mark.parametrize("damage", DAMAGE)
@pytest.mark.parametrize("argv", WRITES)
def test_a_write_refuses_to_replace_a_keys_file_it_cannot_read(run, keys_file, argv, damage):
    """Review item 4 (PR #25): a write used to treat an unreadable or corrupt
    keys file as empty, so one `create` replaced every key with the new one
    (and revoke/disable said "not found"). It must refuse instead: one
    'Error:' line, exit 3, and the file left exactly as it was."""
    run("create", "--name", "agent")
    damage(keys_file)
    before = keys_file.stat()  # works on a mode-000 file too

    try:
        code, out, err = run(*argv)
    finally:
        keys_file.chmod(0o600)

    assert code == 3
    assert err.startswith("Error: ") and "keys file" in err
    assert len(err.strip().splitlines()) == 1
    assert out == ""
    after = keys_file.stat()  # a save renames a new file into place: new inode
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )


def test_a_missing_keys_file_is_still_created_from_empty(run, keys_file):
    """Only an unreadable file is refused; a missing one is an empty key set."""
    assert not keys_file.exists()
    assert run("create", "--name", "agent")[0] == 0
    assert list(_stored(keys_file)) == ["agent"]
