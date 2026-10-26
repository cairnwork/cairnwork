"""#303: the verifier account must not be able to read the owner's declaration. verify_cli asks the
account itself, under the account lock, before anything runs (blackbox_exec.account_can_read)."""

import json
from contextlib import contextmanager

import pytest
from test_blackbox import doc, private_file

from cairnwork import blackbox
from cairnwork.verifier_account import AccountBusy


class _Self:
    """Stands in for the account: runs the probe as this user, or a command that fails."""

    def __init__(self, fail=False, busy=False, exit_code=0):
        self.fail, self.busy, self.locked, self.exit_code = fail, busy, False, exit_code

    def wrap(self, argv, profile):
        if self.fail:
            return ["/usr/bin/false"]
        return [*argv[:2], f"{argv[2]}; exit {self.exit_code}", *argv[3:]]

    @contextmanager
    def exclusive(self, timeout=None):
        if self.busy:
            raise AccountBusy("RESOURCE_LEASE_TIMEOUT: busy")
        self.locked = True
        yield


def test_the_account_probe_says_readable_unless_it_heard_private(tmp_path):
    from typing import Any

    from cairnwork.blackbox_exec import account_can_read

    readable = private_file(tmp_path, b"{}")
    me: Any = _Self()
    assert account_can_read(me, readable)
    assert not account_can_read(me, tmp_path / "missing.json")
    assert me.locked  # asked under the account lock (#247)
    broken: Any = _Self(fail=True)
    assert account_can_read(broken, tmp_path / "missing.json")  # an error is never "private"
    busy: Any = _Self(busy=True)
    assert account_can_read(busy, tmp_path / "missing.json")  # nor is a lock it could not take
    failing: Any = _Self(exit_code=1)
    assert account_can_read(failing, tmp_path / "missing.json")  # "private" from a failed run


@pytest.mark.parametrize("readable", [True, False])
def test_the_cli_refuses_a_declaration_the_account_can_read(
    tmp_path, monkeypatch, capsys, readable
):
    """verify_cli is where the probe runs (a library caller of verify_candidate must probe too)."""
    from cairnwork import verify_cli
    from cairnwork.intake import Intake

    declared = private_file(tmp_path, json.dumps(doc()).encode())
    ran = []
    monkeypatch.setattr(Intake, "verifier_account", lambda self: "account")
    monkeypatch.setattr(verify_cli, "account_can_read", lambda account, path: readable)
    monkeypatch.setattr(
        verify_cli, "verify_candidate", lambda *a, **k: ran.append(1) or {"verified": True}
    )
    argv = ["--state", str(tmp_path), "run", "--repo-path", str(tmp_path), "--base", "b",
            "--candidate", "c", "--allowed-file", "f", "--verifier", "true",
            "--blackbox", str(declared)]  # fmt: skip
    code = verify_cli.main(argv)
    out = json.loads(capsys.readouterr().out)
    if readable:
        assert (code, out["error"], ran) == (2, blackbox.BAD, []), out
        assert "can read" in out["detail"]
    else:
        assert (code, ran) == (0, [1]), out
