"""#437 E and the run-root hint: a run root others could write is refused, and a ``run`` directly
in the root does not get the private-dir advice. Synthetic: no verifier account, no sudo."""

import os
import shlex

import pytest
from test_run_scrub import _Account

from cairnwork.run_scrub import stale_run_error


def _run_root_with_mode(tmp_path, mode):
    root = tmp_path / "runs"
    root.mkdir()
    os.chmod(root, mode)
    return root


@pytest.mark.parametrize("mode", [0o2750, 0o750, 0o700])
def test_run_root_owned_and_not_group_or_other_writable_passes(tmp_path, mode):
    """#437 E: the mode setup_verifier_user*.sh sets (or tighter) is accepted."""
    root = _run_root_with_mode(tmp_path, mode)
    assert stale_run_error(_Account(root), tmp_path / "candidate") is None


@pytest.mark.parametrize("mode", [0o770, 0o775, 0o757, 0o777, 0o2770])
def test_run_root_group_or_other_writable_is_refused(tmp_path, mode):
    """#437 E: a swap of private/ would follow a link when someone else can write the root."""
    root = _run_root_with_mode(tmp_path, mode)
    message = stale_run_error(_Account(root), tmp_path / "candidate")
    assert message is not None
    assert "writable" in message
    assert shlex.quote(str(root)) in message


def test_run_root_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    root = _run_root_with_mode(tmp_path, 0o750)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(root).st_uid + 1)
    message = stale_run_error(_Account(root), tmp_path / "candidate")
    assert message is not None
    assert "owner" in message


def test_a_symlink_named_run_in_the_run_root_does_not_advise_chmod_of_the_root(tmp_path):
    """#437 residual (lab audit 05-F2): `run` directly under the root is not a private/run."""
    root = tmp_path / "runs"
    root.mkdir()
    os.chmod(root, 0o750)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "run").symlink_to(outside, target_is_directory=True)
    message = stale_run_error(_Account(root), tmp_path / "candidate")
    assert message is not None
    assert str(root / "run") in message
    assert "chmod 700" not in message
    assert "unlink that symlink only" in message
    assert (root / "run").is_symlink() and outside.exists()


def test_a_private_dir_that_cannot_be_sealed_keeps_the_chmod_700_advice(tmp_path, monkeypatch):
    """The run-root hint is for a name ``run`` only: a real private dir keeps its recovery steps."""
    root = tmp_path / "runs"
    root.mkdir()
    os.chmod(root, 0o750)
    private = root / "att.abcd1234"
    private.mkdir(mode=0o750)

    def refuse(*_args):
        raise PermissionError("no")

    monkeypatch.setattr("cairnwork.run_scrub.seal_private", refuse)
    message = stale_run_error(_Account(root), tmp_path / "candidate")
    assert message is not None
    assert "chmod 700" in message and shlex.quote(str(private)) in message
