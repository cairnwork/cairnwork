"""#1454 step 2b: the saved gate_rc must be the regular file the analysis job wrote.

A symbolic link (even to a file that holds "0"), a directory or a FIFO is refused, so the status
can never be taken from something a link points at.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.ci import pra1353_trusted_status as status

SHA = "a" * 40


def _argv(path: Path) -> list[str]:
    return ["--rc-file", str(path), "--repo", "o/r", "--sha", SHA, "--run-url", "https://x/y"]


def _link_to(tmp_path: Path, content: bytes) -> Path:
    target = tmp_path / "real"
    target.write_bytes(content)
    link = tmp_path / "gate_rc"
    link.symlink_to(target)
    return link


@pytest.mark.parametrize("content", [b"0", b"1", b"3"])
def test_a_symlink_to_a_valid_value_is_refused(tmp_path: Path, content: bytes) -> None:
    assert status.read_rc(_link_to(tmp_path, content)) is None


def test_a_dangling_symlink_a_directory_and_a_fifo_are_refused(tmp_path: Path) -> None:
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    assert status.read_rc(dangling) is None
    assert status.read_rc(tmp_path) is None
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert status.read_rc(fifo) is None


def test_a_regular_file_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "gate_rc"
    path.write_bytes(b"3")
    assert status.read_rc(path) == "3"


def test_cli_posts_failure_for_a_symlink_to_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake)
    assert status.main(_argv(_link_to(tmp_path, b"0"))) == 1
    (call,) = calls
    assert "state=failure" in call and "state=success" not in call


def test_the_old_following_reader_would_have_accepted_it(tmp_path: Path) -> None:
    """Mutation: the previous implementation followed the link, so this test needs the fix."""
    link = _link_to(tmp_path, b"0")
    with link.open("rb") as handle:
        assert handle.read(17) == b"0"
    assert status.read_rc(link) is None
