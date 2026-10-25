"""Issue #279: ``grant_read`` opens symlinks themselves, not only what they point at.

Account-free: only mode bits are checked, so this runs on any host and never touches the real
verifier account.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cairnwork.verifier_account import VerifierAccount

# Link modes are checked by readlink on macOS only; Linux ignores them and cannot chmod a link.
pytestmark = pytest.mark.skipif(
    os.chmod not in os.supports_follow_symlinks,
    reason="platform cannot chmod a symlink itself",
)


def _build_checkout(root: Path) -> tuple[Path, Path, Path]:
    root.mkdir()
    target = root / "AGENTS.real.md"
    target.write_text("x\n")
    link = root / "AGENTS.md"
    link.symlink_to(target.name)
    sub = root / "sub"
    sub.mkdir()
    dir_link = root / "sublink"
    dir_link.symlink_to(sub.name)
    return target, link, dir_link


def test_grant_read_opens_symlink_itself_under_strict_umask(tmp_path: Path) -> None:
    old = os.umask(0o077)
    try:
        root = tmp_path / "checkout"
        target, link, dir_link = _build_checkout(root)
        # Mimic git's strict-umask checkout: the link's own mode is closed to other users.
        os.chmod(link, 0o700, follow_symlinks=False)
        os.chmod(dir_link, 0o700, follow_symlinks=False)
        target_before = stat.S_IMODE(os.stat(target).st_mode)
        assert not os.lstat(link).st_mode & stat.S_IROTH
        VerifierAccount.grant_read(root)
    finally:
        os.umask(old)
    for each in (link, dir_link):
        mode = os.lstat(each).st_mode
        assert stat.S_ISLNK(mode)
        assert mode & stat.S_IROTH, oct(mode)
    # The target was opened by the file pass as before, never changed through the link twice.
    assert stat.S_IMODE(os.stat(target).st_mode) == target_before | 0o044
    assert os.readlink(link) == "AGENTS.real.md"


def test_grant_read_does_not_follow_a_link_out_of_the_checkout(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    os.chmod(outside, 0o600)
    root = tmp_path / "checkout"
    root.mkdir()
    link = root / "escape"
    link.symlink_to(outside)
    os.chmod(link, 0o700, follow_symlinks=False)
    VerifierAccount.grant_read(root)
    assert stat.S_IMODE(os.stat(outside).st_mode) == 0o600
    assert os.lstat(link).st_mode & stat.S_IROTH
