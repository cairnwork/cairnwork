"""Synthetic checks for private/run cleanup (Issue #392). No verifier account and no sudo:
a stand-in ``remove`` refuses, and the owner's ``rmtree`` is what deletes a real directory.
"""

import os
import shlex
import shutil
import stat
import sys
from typing import Any

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
        self.python = "/srv/cw/venv/bin/python"  # the verifier python, named in the recovery steps
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
    account: Any = _Account(root)
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
    account: Any = _Account(root)
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


def test_a_symlinked_private_is_not_followed_to_a_same_named_directory(tmp_path):
    """A same-named directory under another ``runs`` parent is not deleted.

    ``_real_dir`` and ``_consider`` both refuse a symlink, so dropping only one of those checks
    can still leave ``secret`` in place. This asserts the outcome: the target is untouched.
    """
    root = tmp_path / "runs"
    root.mkdir()
    name = "rid-v0.abcd1234"
    real = tmp_path / "decoy" / "runs" / name
    (real / "run").mkdir(parents=True)
    (real / "run" / "secret").write_text("keep")
    link = root / name
    link.symlink_to(real)
    account: Any = _Account(root)
    with pytest.raises(VerifierAccountError):
        VerifierAccount.scrub_stale_runs(account)
    assert (real / "run" / "secret").read_text() == "keep"
    assert link.is_symlink()
    assert account.removed == []


def test_both_deletes_fail_and_recovery_steps_quote_a_hostile_path(tmp_path, monkeypatch):
    """Both deletes fail. quote() must change the path, or dropping it would stay green."""
    import cairnwork.run_scrub as run_scrub

    monkeypatch.setattr(run_scrub.sys, "platform", "darwin")
    root = tmp_path / "runs dir" / 'a;b $(x) "q"'
    private = _private(root, "rid-v0.abcd1234")
    os.chmod(private, 0o750)
    (private / "run" / "cw-stale").write_text("stay")

    class _InertRmtree:
        avoids_symlink_attacks = shutil.rmtree.avoids_symlink_attacks

        def __call__(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(run_scrub.shutil, "rmtree", _InertRmtree())
    account: Any = _Account(root)
    with pytest.raises(VerifierAccountError) as caught:
        VerifierAccount.scrub_stale_runs(account)
    message = str(caught.value)
    run = private / "run"
    q_run, q_priv = shlex.quote(str(run)), shlex.quote(str(private))
    assert q_run != str(run)  # the path is hostile enough that quote changes it
    policy = shlex.quote("(version 1) (allow default)")
    limits = " ".join(map(str, run_scrub._UNLOCK_LIMITS))
    unlock = (
        f"as the verifier account: sudo -n -u cairnwork-verifier -- /usr/bin/sandbox-exec -p "
        f"{policy} /srv/cw/venv/bin/python -I -c {shlex.quote(run_scrub._UNLOCK)} {q_run} {limits}"
    )
    root = f"/usr/bin/chflags -R -- nouchg,nouappnd {q_run}; /bin/rm -rfx -- {q_run}"
    steps = (
        f"{unlock}; "
        f"as the verifier account: sudo -n -u cairnwork-verifier -- /bin/rm -rf -- {q_run}; "
        f"as root, only if {q_run} is still there (a limit was reached): {root}; "
        f"as the owner: rm -rf -- {q_run}; "
        f"as the owner: chmod 700 {q_priv}"
    )
    assert steps in message
    assert "&&" not in message
    assert not message.endswith(".")
    assert stat.S_IMODE(private.stat().st_mode) == 0o750
    assert (run / "cw-stale").read_text() == "stay"
    assert account.removed == [str(run)]


def test_rmtree_that_follows_symlinks_is_not_called(tmp_path, monkeypatch):
    import cairnwork.run_scrub as run_scrub

    root = tmp_path / "runs"
    private = _private(root, "rid-v0.abcd1234")
    (private / "run" / "marker").write_text("stay")
    called: list[str] = []

    class _Following:
        avoids_symlink_attacks = False

        def __call__(self, *_args, **_kwargs):
            called.append("rmtree")

    monkeypatch.setattr(run_scrub.shutil, "rmtree", _Following())
    account: Any = _Account(root)
    with pytest.raises(VerifierAccountError):
        VerifierAccount.scrub_stale_runs(account)
    assert account.removed == []
    assert called == []
    assert (private / "run" / "marker").read_text() == "stay"


def test_the_unlock_is_requested_as_the_account(tmp_path, monkeypatch):
    """The stand-in records the argv scrub asks ``remove`` to run. The unlock (flags, ACLs and
    modes, #437) is that account's, by the verifier python, before its rm."""
    import cairnwork.run_scrub as run_scrub

    monkeypatch.setattr(run_scrub.sys, "platform", "darwin")
    root = tmp_path / "runs"
    private = _private(root, "rid-v0.abcd1234")

    class _Recording(_Account):
        def __init__(self, run_root):
            super().__init__(run_root)
            self.commands: list[list[str]] = []

        def remove(self, path):
            text = os.fspath(path)
            self.removed.append(text)

            def wrap(argv: list[str]) -> list[str]:
                return ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default)", *argv]

            self.commands.extend(run_scrub.account_delete_argv(self.user, text, wrap, self.python))
            raise VerifierAccountError("no sudo in this test")

    account: Any = _Recording(root)
    assert stale_run_error(account) is None  # owner rmtree still removes the real directory
    unlock = next(argv for argv in account.commands if run_scrub._UNLOCK in argv)
    rm = next(argv for argv in account.commands if "/bin/rm" in argv)
    assert unlock[:4] == ["/usr/bin/sudo", "-n", "-u", "cairnwork-verifier"]
    assert unlock[8:11] == ["/srv/cw/venv/bin/python", "-I", "-c"]
    assert account.commands.index(unlock) < account.commands.index(rm)
    assert not (private / "run").exists()


def test_remove_runs_the_unlock_as_the_account(tmp_path, monkeypatch):
    import cairnwork.run_scrub as run_scrub
    import cairnwork.verifier_account as va

    monkeypatch.setattr(va.sys, "platform", "darwin")
    monkeypatch.setattr(
        va,
        "_backend",
        lambda: type("B", (), {"unconfined": staticmethod(lambda argv: ["sandbox-exec", *argv])})(),
    )
    ran: list[list[str]] = []
    monkeypatch.setattr(va.subprocess, "run", lambda argv, **_k: ran.append(list(argv)))
    root = tmp_path / "cairn"
    (root / "runs" / "rid-v0.abcd1234").mkdir(parents=True)
    va.VerifierAccount(user="acct", root=root).remove(root / "runs" / "rid-v0.abcd1234")
    unlock = next(argv for argv in ran if run_scrub._UNLOCK in argv)
    assert unlock[:4] == ["/usr/bin/sudo", "-n", "-u", "acct"]
    assert unlock[6] == str(root / "venv" / "bin" / "python")


def test_an_unlistable_run_root_has_no_private_chmod(tmp_path, monkeypatch):
    import cairnwork.run_scrub as run_scrub

    root = tmp_path / "runs"
    root.mkdir()

    def _boom(_path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(run_scrub.os, "listdir", _boom)
    message = stale_run_error(_Account(root))
    assert message is not None
    assert "chmod" not in message
    assert "700" not in message
    assert str(root) in message


def test_a_symlinked_run_leaves_private_at_0750(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    private = root / "rid-v0.abcd1234"
    private.mkdir()
    os.chmod(private, 0o750)
    (private / "run").symlink_to(outside)
    account: Any = _Account(root)
    with pytest.raises(VerifierAccountError):
        VerifierAccount.scrub_stale_runs(account)
    assert stat.S_IMODE(private.stat().st_mode) == 0o750
