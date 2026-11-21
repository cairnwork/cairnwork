"""#437 R4: an account run root must be outside the candidate tree."""

import os
from contextlib import contextmanager

import pytest

from cairnwork.account_prelude import locked_prelude
from cairnwork.run_scrub import stale_run_error


class _Account:
    user = "cairnwork-verifier"
    python = "/usr/bin/python3"

    def __init__(self, run_root):
        self.run_root = run_root


@pytest.mark.parametrize("use_symlink", [False, True])
def test_run_root_inside_candidate_tree_is_refused(tmp_path, use_symlink):
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    run_root = candidate / "runs"
    run_root.mkdir(mode=0o700)
    os.chmod(run_root, 0o700)
    marker = run_root / "preserve"
    marker.write_text("untouched")
    if use_symlink:
        alias = tmp_path / "candidate-link"
        alias.symlink_to(candidate, target_is_directory=True)
        candidate = alias

    message = stale_run_error(_Account(run_root), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message
    assert "move it outside" in message
    assert marker.read_text() == "untouched"


def test_run_root_equal_to_candidate_tree_is_refused(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    marker = candidate / "preserve"
    marker.write_text("untouched")

    message = stale_run_error(_Account(candidate), candidate)

    assert message is not None
    assert "run root is inside the candidate tree" in message
    assert "move it outside" in message
    assert marker.read_text() == "untouched"


@pytest.mark.parametrize("run_name", ["runs", "candidate-runs"])
def test_run_root_sibling_or_name_prefix_is_not_inside_candidate(tmp_path, run_name):
    candidate = tmp_path / "cand"
    candidate.mkdir(mode=0o700)
    run_root = tmp_path / run_name
    run_root.mkdir(mode=0o700)
    os.chmod(candidate, 0o700)
    os.chmod(run_root, 0o700)

    assert stale_run_error(_Account(run_root), candidate) is None


def test_locked_prelude_passes_candidate_tree_to_account_scrub(tmp_path):
    class RecordingAccount:
        user = "cairnwork-verifier"

        def __init__(self):
            self.candidate_tree = None

        @contextmanager
        def exclusive(self, _timeout):
            yield

        def available(self, _python):
            return True

        def require_idle(self):
            pass

        def scrub_stale_runs(self, candidate_tree):
            self.candidate_tree = candidate_tree

    account = RecordingAccount()
    candidate = tmp_path / "repo"
    candidate.mkdir()

    with locked_prelude(account, "python3", 1, candidate):
        pass

    assert account.candidate_tree == candidate
