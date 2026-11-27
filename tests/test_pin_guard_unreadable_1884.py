"""#1884: an unreadable dynamic pin table must fail closed, not fall back to the fixed list."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci/pin_guard.py"
PIN_FILE = "tests/_w1353_allow_pins.py"
GUARDED_BY_TABLE = "tests/_w1353_probe.py"
INCOMPLETE = (2, "PIN_GUARD PIN_GUARD_INCOMPLETE")

needs_perms = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_case(tmp_path: Path, *, table: bool) -> tuple[Path, Path, str, str]:
    """A repo whose head edits a file that only the dynamic pin table guards."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    git(repo.parent, "init", "-q", str(repo))
    (repo / GUARDED_BY_TABLE).write_bytes(b"original\n")
    base = commit(repo, "base")
    (repo / GUARDED_BY_TABLE).write_bytes(b"edited\n")
    head = commit(repo, "edit")
    trusted = tmp_path / "trusted"
    (trusted / "tests").mkdir(parents=True)
    if table:
        pin = (
            'TRUSTED_HELPER_PINS = dict(zip(("'
            + GUARDED_BY_TABLE
            + '",), ("0000000000000000000000000000000000000000000000000000000000000000",)))\n'
        )
        (trusted / PIN_FILE).write_text(pin, encoding="utf-8")
    return repo, trusted, base, head


def run(repo: Path, trusted: Path, base: str, head: str) -> tuple[int, str]:
    cmd = [sys.executable, "-I", str(SCRIPT), "--trusted", str(trusted), "--head", str(repo)]
    cmd += [f"--base-sha={base}", f"--head-sha={head}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def test_readable_table_still_guards_its_entry(tmp_path: Path) -> None:
    repo, trusted, base, head = make_case(tmp_path, table=True)
    code, out = run(repo, trusted, base, head)
    assert code == 1 and "PIN_GUARD" in out and GUARDED_BY_TABLE in out


def test_truly_absent_table_behaves_as_before(tmp_path: Path) -> None:
    repo, trusted, base, head = make_case(tmp_path, table=False)
    assert run(repo, trusted, base, head) == (0, "PIN_GUARD PIN_GUARD_OK")


@needs_perms
def test_unsearchable_tests_dir_fails_closed(tmp_path: Path) -> None:
    repo, trusted, base, head = make_case(tmp_path, table=True)
    (trusted / "tests").chmod(0)
    try:
        assert run(repo, trusted, base, head) == INCOMPLETE
    finally:
        (trusted / "tests").chmod(0o755)


@needs_perms
def test_unreadable_table_file_fails_closed(tmp_path: Path) -> None:
    repo, trusted, base, head = make_case(tmp_path, table=True)
    (trusted / PIN_FILE).chmod(0)
    try:
        assert run(repo, trusted, base, head) == INCOMPLETE
    finally:
        (trusted / PIN_FILE).chmod(0o644)


@needs_perms
def test_unsearchable_approver_dir_fails_closed(tmp_path: Path) -> None:
    repo, trusted, base, head = make_case(tmp_path, table=True)
    approvers = trusted / "scripts/ci"
    approvers.mkdir(parents=True)
    (approvers / "pin_guard_approvers.txt").write_text("alice\n", encoding="utf-8")
    approvers.chmod(0)
    try:
        assert run(repo, trusted, base, head) == INCOMPLETE
    finally:
        approvers.chmod(0o755)
