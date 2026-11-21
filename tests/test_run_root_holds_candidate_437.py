"""#437 V5: an entry of the run root that is, or contains, the candidate tree is never scrubbed."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


class _Account:
    user = "cairnwork-verifier"
    python = "/usr/bin/python3"

    def __init__(self, run_root: Path):
        self.run_root = run_root
        self.removed: list[str] = []

    def remove(self, path):
        from cairnwork.verifier_account import VerifierAccountError

        self.removed.append(os.fspath(path))
        raise VerifierAccountError("no sudo in this test")


def _private_with_run(path: Path) -> Path:
    (path / "run").mkdir(parents=True)
    (path / "run" / "kept.txt").write_text("candidate data")
    return path


@pytest.mark.parametrize("where", ["is-candidate", "contains-candidate", "alias"])
def test_run_root_entry_holding_the_candidate_is_left_alone(tmp_path, where):
    from cairnwork.run_scrub import stale_run_error

    root = tmp_path / "runs"
    root.mkdir(mode=0o700)
    held = _private_with_run(root / "cand.abcdefgh")
    held.chmod(0o750)
    mode_before = stat.S_IMODE(os.lstat(held).st_mode)
    if where == "is-candidate":
        candidate = held
    elif where == "contains-candidate":
        candidate = held / "run" / "tree"
        candidate.mkdir()
    else:
        candidate = tmp_path / "cand-link"
        candidate.symlink_to(held, target_is_directory=True)
    stale = _private_with_run(root / "old.ijklmnop")
    account = _Account(root)

    assert stale_run_error(account, candidate) is None
    assert (held / "run" / "kept.txt").read_text() == "candidate data"
    assert stat.S_IMODE(os.lstat(held).st_mode) == mode_before
    assert str(held / "run") not in account.removed
    assert not (stale / "run").exists()
    assert account.removed == [str(stale / "run")]
