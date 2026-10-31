"""PAUSE (#476) AC8: the checkout digest that checks the read-only guard sees every kind of
change and fails closed; the one event controller's log."""

import os
import shutil
import subprocess
import sys

import pytest
from test_pause_guard import _fill, _log

from experimental.pause.controller import JsonlEventLog, read_events
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import GuardUnavailable
from experimental.pause.schema import Reason
from tests.conftest import git


@pytest.fixture
def checkout(tmp_path):
    co = tmp_path / "repo"
    co.mkdir()
    _fill(co)
    return co


def _xattr(path, value):
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-w", "user.cw", value, str(path)], check=True)
        return
    try:
        os.setxattr(path, "user.cw", value.encode())
    except OSError as e:
        pytest.skip(f"no user attributes on this file system: {e}")


def _other_group(c):
    other = [g for g in os.getgroups() if g != (c / "src" / "a.py").stat().st_gid]
    if not other:
        pytest.skip("this account is in no second group")
    os.chown(c / "src" / "a.py", -1, other[0])


def _acl(c):
    if sys.platform != "darwin":
        pytest.skip("a macOS extended ACL (chmod +a)")
    subprocess.run(["chmod", "+a", "everyone deny write", str(c / "src" / "a.py")], check=True)


CHANGES = {
    "content": lambda c: (c / "src" / "a.py").write_text("x = 2\n"),
    "ignored_file": lambda c: (c / "build.log").write_text("log\n"),
    "mode": lambda c: (c / "src" / "a.py").chmod(0o755),
    "symlink_target": lambda c: (os.unlink(c / "link"), os.symlink("src/b.py", c / "link")),
    "dir_to_symlink": lambda c: (shutil.rmtree(c / "src"), os.symlink("/tmp", c / "src")),
    "git_config": lambda c: git(c, "config", "--local", "core.hooksPath", "/x"),
    "assume_unchanged": lambda c: (
        git(c, "update-index", "--assume-unchanged", "--", "src/a.py"),
        (c / "src" / "a.py").write_text("x = 5\n"),
    ),
    "xattr": lambda c: _xattr(c / "src" / "a.py", "planted"),
    "acl": _acl,
    "owner": _other_group,
}


@pytest.mark.parametrize("name", sorted(CHANGES))
def test_the_digest_sees_every_kind_of_change(name, checkout):
    guard = TreeGuard(checkout)
    before = guard.digest()
    assert guard.digest() == before
    CHANGES[name](checkout)
    assert guard.digest() != before


def test_one_controller_per_event_log_in_a_private_directory(checkout, tmp_path):
    log = _log(checkout, tmp_path)
    log.record("a")
    with pytest.raises(GuardUnavailable):
        JsonlEventLog(tmp_path / "ctl" / "events.jsonl", checkout=checkout)  # a second one
    (tmp_path / "shared").mkdir(mode=0o755)
    with pytest.raises(GuardUnavailable, match="0700"):
        JsonlEventLog(tmp_path / "shared" / "events.jsonl", checkout=checkout)
    (checkout / "ctl").mkdir(mode=0o700)
    with pytest.raises(GuardUnavailable, match="outside"):
        JsonlEventLog(checkout / "ctl" / "events.jsonl", checkout=checkout)
    with (tmp_path / "ctl" / "events.jsonl").open("a") as f:
        f.write('{"kind": "b", "seq": 3}\n')
    with pytest.raises(ValueError, match="sequence"):
        read_events(tmp_path / "ctl" / "events.jsonl")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode 000 directory")
def test_the_digest_fails_closed_when_it_cannot_see_the_checkout(checkout, tmp_path):
    for limits in ({"max_files": 3}, {"max_bytes": 10}):
        with pytest.raises(GuardUnavailable):
            TreeGuard(checkout, **limits).digest()
    with pytest.raises(GuardUnavailable):
        TreeGuard(tmp_path / "missing").digest()
    os.symlink(checkout, tmp_path / "via-link")
    with pytest.raises(GuardUnavailable):
        TreeGuard(tmp_path / "via-link").digest()
    # The same account cannot read a mode 000 directory; a worker account's unreadable files
    # are covered when step 3 wires the guard in the worker-account mode (not tested here).
    (checkout / "src").chmod(0)
    try:
        with pytest.raises(GuardUnavailable) as err:
            TreeGuard(checkout).digest()
    finally:
        (checkout / "src").chmod(0o755)
    assert err.value.reason == Reason.PAUSE_GUARD_UNAVAILABLE
