"""Synthetic checks for private/run cleanup (Issue #392). No verifier account and no sudo:
a stand-in ``remove`` refuses, and the owner's ``rmtree`` is what deletes a real directory.
"""

import os
import shutil
import stat
import sys

import pytest

import cairnwork.blackbox_exec as blackbox_exec
import cairnwork.verifiers as verifiers
from cairnwork.pytest_config import PytestSettings
from cairnwork.run_scrub import close_private, stale_run_error
from cairnwork.verifier_account import VerifierAccount, VerifierAccountError


class _Checkout:
    def __init__(self, root, name="id-v0"):
        self.name = name
        self.parent = type("P", (), {"parent": root})()


class _Account:
    """``remove`` records the path and refuses, so no sudo runs."""

    def __init__(self, root):
        self.user = "cairnwork-verifier"
        self.run_root = root
        self.removed = []

    def remove(self, path):
        self.removed.append(os.fspath(path))
        raise VerifierAccountError("no sudo in this test")


def _private(root, name):
    private = root / name
    (private / "run" / "home").mkdir(parents=True)
    (private / "verifier.log").write_text("kept")
    return private


def test_scrub_removes_a_real_run_dir_and_keeps_the_log(tmp_path):
    root = tmp_path / "runs"
    private = _private(root, "rid-v0.abcd1234")
    (private / "run" / "cw-stale").write_text("stale")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("keep")
    (private / "run" / "escape").symlink_to(outside, target_is_directory=True)
    account = _Account(root)
    assert stale_run_error(account) is None
    assert not (private / "run").exists()
    assert (private / "verifier.log").read_text() == "kept"
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
    assert (outside / "secret").read_text() == "keep"  # the link inside run/ was not followed
    assert account.removed == [str(private / "run")]
    assert shutil.rmtree.avoids_symlink_attacks


def test_a_run_symlink_outside_the_root_is_not_followed(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / "secret").mkdir(parents=True)
    (outside / "secret" / "file").write_text("keep")
    private = root / "rid-v0.abcd1234"
    private.mkdir()
    (private / "verifier.log").write_text("kept")
    (private / "run").symlink_to(outside)
    account = _Account(root)
    with pytest.raises(VerifierAccountError) as caught:
        VerifierAccount.scrub_stale_runs(account)
    message = str(caught.value)
    run = private / "run"
    assert str(run) in message
    assert "sudo -n -u cairnwork-verifier -- /bin/rm -rf --" in message
    assert "chmod 700" in message
    assert "unlink that symlink only" in message
    assert "do not remove or chmod its target" in message
    assert (outside / "secret" / "file").read_text() == "keep"
    assert run.is_symlink()  # left in place: not unlinked and not followed
    assert account.removed == []
    assert (private / "verifier.log").read_text() == "kept"


def test_close_private_does_not_follow_a_run_symlink(tmp_path, capsys):
    outside = tmp_path / "safe"
    outside.mkdir()
    (outside / "secret").write_text("keep")
    private = tmp_path / "runs" / "id-v0.abcd1234"
    private.mkdir(parents=True)
    (private / "verifier.log").write_text("kept")
    (private / "run").symlink_to(outside)
    close_private(private, None)  # owner mode: the verdict stands, so this does not raise
    assert (outside / "secret").read_text() == "keep"
    assert (private / "run").is_symlink()
    assert "not removed" in capsys.readouterr().err
    assert (private / "verifier.log").read_text() == "kept"


def test_a_work_dir_is_not_scrubbed_and_a_bad_name_refuses(tmp_path):
    root = tmp_path / "runs"
    work = root / "rid-v0.work"
    (work / "run").mkdir(parents=True)
    (work / "run" / "marker").write_text("stay")
    odd = root / "not-a-private-dir"
    (odd / "run").mkdir(parents=True)
    (odd / "run" / "marker").write_text("stay")
    account = _Account(root)
    with pytest.raises(VerifierAccountError) as caught:
        VerifierAccount.scrub_stale_runs(account)
    assert str(odd / "run") in str(caught.value)
    assert (work / "run" / "marker").read_text() == "stay"
    assert (odd / "run" / "marker").read_text() == "stay"
    assert account.removed == []


def test_a_raised_verifier_still_drops_its_run_dir(tmp_path, monkeypatch):
    made = {}

    def mkdtemp(prefix, dir):
        path = tmp_path / f"{prefix}abcd1234"
        path.mkdir()
        made["private"] = path
        return str(path)

    def boom(*_args, **_kwargs):
        private = made["private"]
        (private / "run" / "home").mkdir(parents=True)
        (private / "verifier.log").write_text("kept")
        raise RuntimeError("before verdict")

    monkeypatch.setattr(verifiers.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(verifiers.pytest_config, "installed_plugins", lambda _python: ())
    monkeypatch.setattr(verifiers, "_run_one_in", boom)
    with pytest.raises(RuntimeError, match="before verdict"):
        verifiers._run_one(
            None, None, None, _Checkout(tmp_path), sys.executable, None, None, None,
            PytestSettings(), None,
        )  # fmt: skip
    private = made["private"]
    assert not (private / "run").exists()
    assert (private / "verifier.log").read_text() == "kept"
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


def test_a_raised_blackbox_still_drops_its_run_dir(tmp_path, monkeypatch):
    def prepare(private, _ini, _text, channel=None):
        (private / "run").mkdir()
        (private / "blackbox.log").write_text("kept")
        raise RuntimeError("before read")

    monkeypatch.setattr(blackbox_exec, "_prepare_run", prepare)
    with pytest.raises(RuntimeError, match="before read"):
        blackbox_exec._run_in(None, None, None, _Checkout(tmp_path), None, None, None, None, ())
    private = next(tmp_path.glob("id-v0.*"))
    assert not (private / "run").exists()
    assert (private / "blackbox.log").read_text() == "kept"
    assert stat.S_IMODE(private.stat().st_mode) == 0o700
