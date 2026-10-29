"""Issue #240: removing a verifier's checkout and scratch. The verdict is decided before cleanup,
so a failing removal (the account's included) never escapes run_verifiers; what the owner cannot
remove is reported as an ALERT on stderr, never silent. No sudo: stand-in accounts."""

import os
import subprocess

import pytest

import cairnwork.verifiers as verifiers
from cairnwork.account_checkout import STAGE
from cairnwork.run_scrub import close_private
from cairnwork.verifier_account import VerifierAccountError


@pytest.mark.parametrize(
    "error", [VerifierAccountError("outside"), subprocess.TimeoutExpired("rm", 60), OSError("sudo")]
)
def test_a_failing_account_removal_keeps_the_verdict_and_the_owner_removes_its_part(
    tmp_path, capsys, error
):
    """The verdict is decided before cleanup: a failing account removal is not raised out of
    run_verifiers; the owner removes what it can, and what is left would be an ALERT."""
    work = tmp_path / "x.work"
    (work / STAGE / "x").mkdir(parents=True)
    (work / STAGE / "x" / "f").write_text("owner")

    class Failing:
        def remove(self, path):
            raise error

    verifiers._remove_checkout(work, "x", Failing())
    assert not work.exists() and "ALERT" not in capsys.readouterr().err


def test_a_failing_account_removal_of_scratch_keeps_the_verdict(tmp_path):
    private = tmp_path / "id-v0.abcd1234"
    run = private / "run"
    (run / "home").mkdir(parents=True)
    (run / "tmp").mkdir()

    class Failing:
        user = "cairnwork-verifier"
        run_root = tmp_path

        def remove(self, path):
            raise subprocess.TimeoutExpired("rm", 60)

    close_private(private, Failing())
    assert not run.exists()


def test_a_scratch_leftover_is_reported_not_silent(tmp_path, capsys):
    private = tmp_path / "id-v0.abcd1234"
    closed = private / "run" / "closed"  # stands in for what only the account could remove
    closed.mkdir(parents=True)
    (closed / "f").write_text("account")
    os.chmod(closed, 0o500)
    os.chmod(private, 0o750)

    class Failing:
        user = "cairnwork-verifier"
        run_root = tmp_path

        def remove(self, path):
            raise VerifierAccountError("sudo failed")

    try:
        close_private(private, Failing())
        assert (private / "run").exists()
        assert os.stat(private).st_mode & 0o777 == 0o750  # not locked before the delete succeeds
        err = capsys.readouterr().err
        assert f"ALERT verifier cleanup: {private / 'run'} was not removed" in err
    finally:
        os.chmod(closed, 0o700)


def test_a_leftover_after_cleanup_is_reported_not_silent(tmp_path, capsys):
    """An account whose removal did nothing (``rm`` runs with check=False) leaves its copy, which
    the owner cannot remove: the run's verdict stands, but the leftover is an ALERT on stderr."""
    work = tmp_path / "x.work"
    copy = work / "x" / "closed"  # stands in for the account's copy: the owner's rmtree fails
    copy.mkdir(parents=True)
    (copy / "f").write_text("account")
    os.chmod(copy, 0o500)

    class Inert:
        def remove(self, path):
            pass

    try:
        verifiers._remove_checkout(work, "x", Inert())
        assert work.exists()
        assert f"ALERT verifier cleanup: {work} was not removed" in capsys.readouterr().err
    finally:
        os.chmod(copy, 0o700)
    verifiers._remove_checkout(work, "x", Inert())
    assert not work.exists() and "ALERT" not in capsys.readouterr().err
