"""Filesystem and Git metadata checks for completed R1 workspaces."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

from experimental.pause.git_env import hardened_git_env

_GIT_OPTIONS = (
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
)


def _scope_ok(
    checkout: Path, scope: list[str] | None, base: str, git_config_sha256: str | None = None
) -> bool:
    """Check all changed paths, file modes, and workspace entries against write_scope."""
    if len(base) != 40 or any(c not in "0123456789abcdef" for c in base):
        return False
    if not _regular_tree(checkout):
        return False
    if _git_output(checkout, "rev-parse", "--show-toplevel").strip() != str(checkout.resolve()):
        return False
    if not _repo_metadata_ok(checkout, git_config_sha256):
        return False
    proc = _git_run(checkout, "diff", "--name-only", "-z", "--no-renames", base)
    if proc.returncode:
        return False
    allowed = tuple(scope or ())
    untracked = _git_run(checkout, "ls-files", "--others", "--exclude-standard", "-z")
    ignored = _git_run(checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    index_flags = _git_run(checkout, "ls-files", "-v", "-z")
    if index_flags.returncode or any(
        row[:1].islower() or row.startswith(b"S ") for row in index_flags.stdout.split(b"\0") if row
    ):
        return False
    if untracked.returncode or ignored.returncode:
        return False
    tracked = {p.decode("utf-8", "surrogateescape") for p in proc.stdout.split(b"\0") if p}
    new_paths = {
        p.decode("utf-8", "surrogateescape")
        for p in (untracked.stdout + ignored.stdout).split(b"\0")
        if p
    }
    paths = tracked | new_paths
    raw = _git_run(checkout, "diff", "--raw", "--no-renames", base)
    if raw.returncode or not _regular_modes(raw.stdout):
        return False
    for path in paths:
        if path == ".git" or path.startswith(".git/"):
            return False
        if path in new_paths:
            try:
                kind = (checkout / path).lstat().st_mode
            except OSError:
                return False
            if not stat.S_ISREG(kind):
                return False
        if scope is not None and not any(
            path == root or path.startswith(root + "/") for root in allowed
        ):
            return False
    return True


def _regular_modes(raw: bytes) -> bool:
    """Reject symlinks, gitlinks, and other non-regular tracked changes."""
    for line in raw.decode("utf-8", "surrogateescape").splitlines():
        header = line.split("\t", 1)[0].split()
        if len(header) >= 2 and any(
            mode.lstrip(":") not in ("000000", "100644", "100755") for mode in header[:2]
        ):
            return False
    return True


def _git_run(checkout: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        capture_output=True,
        check=False,
        env=hardened_git_env(),
    )


def _git_output(checkout: Path, *args: str) -> str:
    result = _git_run(checkout, *args)
    return result.stdout.decode("utf-8", "surrogateescape") if result.returncode == 0 else ""


def _regular_tree(checkout: Path) -> bool:
    try:
        for current, dirs, files in os.walk(checkout, followlinks=False):
            parent = Path(current)
            dirs[:] = [name for name in dirs if not (parent == checkout and name == ".git")]
            for name in [*dirs, *files]:
                path = parent / name
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    if name in dirs:
                        dirs.remove(name)
                    continue
                if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                    return False
        return True
    except OSError:
        return False


def _repo_metadata_ok(checkout: Path, git_config_sha256: str | None = None) -> bool:
    git_dir = checkout / ".git"
    config = git_dir / "config"
    if (
        git_dir.is_symlink()
        or git_config_sha256 is None
        or not _config_matches(config, git_config_sha256)
        or (git_dir / "refs" / "replace").exists()
    ):
        return False
    keys = subprocess.run(
        ["git", *_GIT_OPTIONS, "config", "--file", str(config), "--name-only", "--list"],
        capture_output=True,
        check=False,
        env=hardened_git_env(),
    )
    if keys.returncode:
        return False
    forbidden = (
        "include",
        "includeif",
        "core.worktree",
        "core.autocrlf",
        "core.eol",
        "core.safecrlf",
        "core.attributesfile",
        "core.excludesfile",
        "core.hookspath",
        "core.fsmonitor",
        "status.showuntrackedfiles",
    )
    names = keys.stdout.decode("utf-8", "replace").lower().splitlines()
    if any(name == key or name.startswith(key + ".") for name in names for key in forbidden):
        return False
    return _filemode_is_safe(config) and _replace_refs_are_absent(git_dir)


def _config_matches(config: Path, expected: str) -> bool:
    """Require a regular config file with the runner-captured bytes."""
    try:
        info = config.lstat()
        actual = hashlib.sha256(config.read_bytes()).hexdigest()
        return stat.S_ISREG(info.st_mode) and actual == expected
    except OSError:
        return False


def _filemode_is_safe(config: Path) -> bool:
    result = subprocess.run(
        ["git", *_GIT_OPTIONS, "config", "--file", str(config), "--get", "core.filemode"],
        capture_output=True,
        check=False,
        env=hardened_git_env(),
    )
    return result.returncode != 0 or result.stdout.strip().lower() != b"false"


def _replace_refs_are_absent(git_dir: Path) -> bool:
    replace_dir = git_dir / "refs" / "replace"
    if replace_dir.exists() or replace_dir.is_symlink():
        return False
    packed = git_dir / "packed-refs"
    return not packed.is_file() or b"refs/replace/" not in packed.read_bytes()
