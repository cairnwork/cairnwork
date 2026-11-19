"""What the Haiku gate reviews: the task statement plus the change itself, with relative paths.

Everything read here is worker-controlled (the checkout and its ``.git``), so: git runs with hooks,
fsmonitor, external diff, textconv and attribute files off; files are opened with O_NOFOLLOW and
read with a hard byte cap (a symlink is shown as its target text, never followed); and a plan that
cannot show the whole change raises ``PlanIncomplete`` instead of being silently cut, so an
unseen part of the change can never be approved.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from experimental.pause.git_env import hardened_git_env

JUDGE_HEADER = Path(__file__).with_name("judge_v1_1_header.txt")
# The G1-G3 experiments' frozen judge_v1.1 prompt header, byte for byte (verdicts 可直接合併 /
# 需修改 / 不可用); the relay's gate must ask the same question the experiments measured.
JUDGE_HEADER_SHA256 = "8cb36edb5f3f5ff9d5a86b329187482c075f40a526474d09cb6fb6afad87cc56"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_PLAN_CHARS = 30000
MAX_DIFF_BYTES = 60000
MAX_NEW_FILE_BYTES = 8000
MAX_LISTING_BYTES = 20000
GIT_TIMEOUT = 60.0  # wall clock for any one git call (a worker repo must not stall the relay)
_GIT = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotepath=off",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "diff.ignoreSubmodules=none",
    "-c",
    "core.fileMode=true",
    "-c",
    "core.symlinks=true",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.sparseCheckout=false",
    "-c",
    "core.ignoreCase=false",
    "-c",
    "core.precomposeUnicode=false",
)


class PlanIncomplete(Exception):
    """The change cannot be shown to the gate in full; the stage must not be approved."""


def _env(scratch: Path, checkout: Path, objects: Path) -> dict[str, str]:
    """Git environment that never reads the worker's ``.git`` (config, index, attributes, objects).

    A scratch GIT_DIR with an empty config means no filter, textconv or core.* setting can come
    from the worker; the base objects come from the TRUSTED source repo through an alternate
    object directory (a worker-tampered base object would otherwise change what "base" means).
    """
    env = hardened_git_env()
    env.update(
        GIT_ATTR_NOSYSTEM="1",
        GIT_DIR=str(scratch / "gitdir"),
        GIT_WORK_TREE=str(checkout),
        GIT_INDEX_FILE=str(scratch / "index"),
        GIT_ALTERNATE_OBJECT_DIRECTORIES=str(objects),
    )
    return env


def _trusted_objects(source: Path) -> Path:
    run = subprocess.run(
        [*_GIT, "-C", str(source), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
        env=hardened_git_env(),
    )
    if run.returncode or not run.stdout.strip():
        raise RuntimeError("cannot locate the trusted repository's objects")
    return (source / run.stdout.strip()).resolve() / "objects"


def _git(checkout: Path, *args: str, limit: int, env: dict[str, str]) -> bytes:
    """Run git and read at most ``limit`` bytes; more output means an incomplete plan."""
    proc = subprocess.Popen(
        [*_GIT, "-C", str(checkout), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    assert proc.stdout is not None
    timed_out = threading.Event()

    def expire() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(GIT_TIMEOUT, expire)
    timer.start()
    try:
        data = proc.stdout.read(limit + 1)
        over = len(data) > limit
        if over:
            proc.kill()
        proc.stdout.close()
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise RuntimeError(f"git {args[0]} timed out after {GIT_TIMEOUT:g}s")
    if over:
        raise PlanIncomplete(f"git {args[0]} output exceeds {limit} bytes")
    if code:
        raise RuntimeError("cannot read the candidate change for the gate")
    return data


@contextlib.contextmanager
def _scratch_index(source: Path, checkout: Path, base: str):
    """A scratch GIT_DIR whose index is the base tree: the worker's ``.git`` is never read."""
    if not _SHA.fullmatch(base):
        raise ValueError("base commit must be 40 lowercase hex characters")
    objects = _trusted_objects(Path(source))
    with tempfile.TemporaryDirectory(prefix="relay-gate-git-") as tmp:
        scratch = Path(tmp)
        env = _env(scratch, checkout, objects)
        _git(checkout, "init", "-q", "--template=", str(scratch / "gitdir"), limit=4096, env=env)
        _git(checkout, "read-tree", base, limit=MAX_LISTING_BYTES, env=env)  # index := base tree
        yield env


def _names(raw: bytes) -> list[str]:
    return [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]


def _tracked_names(checkout: Path, env: dict[str, str]) -> list[str]:
    return _names(
        _git(
            checkout,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--ignore-submodules=none",
            limit=MAX_LISTING_BYTES,
            env=env,
        )
    )


def changed_paths(source: Path, checkout: Path, base: str) -> set[str]:
    """Every tracked change and every untracked file, read without trusting the worker's repo."""
    with _scratch_index(source, checkout, base) as env:
        tracked = _tracked_names(checkout, env)
        others = _git(checkout, "ls-files", "--others", "-z", limit=MAX_LISTING_BYTES, env=env)
    return set(tracked) | set(_names(others))


def _parent_fd(root_fd: int, rel: str) -> tuple[int, str]:
    """Open the parent directory of ``rel`` below ``root_fd`` without following any link."""
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PlanIncomplete(f"unsafe path: {rel}")
    fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=root_fd)
    try:
        for part in path.parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except OSError as exc:
        os.close(fd)
        raise PlanIncomplete(f"cannot reach {rel}: {exc.strerror}") from exc
    return fd, path.parts[-1]


def _check_tracked(root_fd: int, device: int, rel: str) -> tuple | None:
    """A changed tracked path must be a plain file (nlink 1, same device), a link or gone.

    Returns an identity signature (None when deleted) so the same path can be compared before and
    after git reads it.
    """
    fd, name = _parent_fd(root_fd, rel)
    try:
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None  # deleted: git diff shows the removal
        except OSError as exc:
            raise PlanIncomplete(f"cannot inspect {rel}: {exc.strerror}") from exc
    finally:
        os.close(fd)
    signature = (
        info.st_dev,
        info.st_ino,
        info.st_nlink,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
    )
    if stat.S_ISLNK(info.st_mode):
        return signature  # git diff shows the link target text, never the pointed-to content
    if not stat.S_ISREG(info.st_mode) or info.st_dev != device or info.st_nlink != 1:
        raise PlanIncomplete(f"changed file is not a plain single-link file: {rel}")
    return signature


def _read_new_file(root_fd: int, device: int, rel: str) -> str:
    """Contents of one untracked file, or the link target for a symlink; never follows links."""
    fd, name = _parent_fd(root_fd, rel)
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            return "(symlink to " + os.readlink(name, dir_fd=fd) + ")"
        if not stat.S_ISREG(info.st_mode) or info.st_dev != device or info.st_nlink != 1:
            raise PlanIncomplete(f"cannot show non-regular or linked file: {rel}")
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            data = os.read(file_fd, MAX_NEW_FILE_BYTES + 1)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise PlanIncomplete(f"cannot read new file {rel}: {exc.strerror}") from exc
    finally:
        os.close(fd)
    if len(data) > MAX_NEW_FILE_BYTES:
        raise PlanIncomplete(f"new file {rel} exceeds {MAX_NEW_FILE_BYTES} bytes")
    return data.decode("utf-8", "replace")


def build_gate_plan(task: dict[str, Any], candidate: Any) -> str:
    """Task prompt + ``git diff base`` + every untracked file; paths are repo-relative.

    Untracked files are enumerated exactly as acceptance does (no ignore rules).
    """
    checkout = getattr(candidate, "checkout", None)
    base = getattr(candidate, "base_sha", None)
    if checkout is None or not isinstance(base, str):
        raise ValueError("gate review needs the candidate checkout and its base commit")
    checkout = Path(checkout)
    source = task.get("repo")
    if not source:
        raise ValueError("gate review needs the trusted source repository")
    root_fd = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY)
    try:
        device = os.fstat(root_fd).st_dev
        with _scratch_index(Path(source), checkout, base) as env:
            tracked = _tracked_names(checkout, env)
            # nothing but plain files may feed the diff that goes to the cloud: check each changed
            # path before git reads it and again afterwards (a leftover worker process must not be
            # able to swap a file for a hard link just while git reads it)
            before = {rel: _check_tracked(root_fd, device, rel) for rel in tracked}
            diff = _git(
                checkout,
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--text",  # attributes (-diff, binary) must not turn content into a placeholder
                "--no-renames",
                "--ignore-submodules=none",
                limit=MAX_DIFF_BYTES,
                env=env,
            ).decode("utf-8", "replace")
            listing = _git(checkout, "ls-files", "--others", "-z", limit=MAX_LISTING_BYTES, env=env)
            after = {rel: _check_tracked(root_fd, device, rel) for rel in tracked}
        if before != after:
            raise PlanIncomplete("tracked files changed while the gate plan was built")
        if any(line.startswith("Binary files ") for line in diff.splitlines()):
            raise PlanIncomplete("the diff hides content behind a binary placeholder")
        issue = str(task.get("prompt", "")).strip()
        issue += "\n\nAllowed files: " + ", ".join(task.get("allowed_files", []))
        parts = [diff or "(no tracked changes)"]
        for rel in _names(listing):
            parts.append(f"New file {rel}:\n{_read_new_file(root_fd, device, rel)}")  # as text
    finally:
        os.close(root_fd)
    header = JUDGE_HEADER.read_text(encoding="utf-8")
    plan = f"{header}【Issue】\n{issue}\n\n【修正（unified diff）】\n" + "\n\n".join(parts) + "\n"
    if len(plan) > MAX_PLAN_CHARS:
        raise PlanIncomplete(f"plan exceeds {MAX_PLAN_CHARS} characters")
    return plan
