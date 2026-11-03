"""Remove an episode's run root once its corpus line is written (#677), with --clean-run-root.

Each episode runs in a run root the harness made for it (``<runs>/<32 hex>``: checkout, tmp, q
and a venv of about 20 MB). Nothing in this package reads the root after the line: a resume needs
a fresh root (cli.py), and the line, the transcript and the call log live under ``--out``. A
driver may still use it after the CLI exits (PAUSE's rescue driver runs deferred tests and Core
verification there, then deletes the root itself), so the CLI touches it only when asked: with
``--clean-run-root`` (off by default; without it nothing here runs, no claim either), after the
line the CLI deletes every entry of the root except top-level regular files named ``*.json``,
``*.jsonl`` or ``*.md`` (small result files a driver may have left there), then the root itself
when nothing was kept.

Which root is this episode's own is never read from process text (#563). Before any tool call
the episode creates ``CLAIM`` in its root (O_EXCL, O_NOFOLLOW) holding its episode id; the worker
cannot write there (the root is 0750, the Owner's). A root that already holds a claim refuses the
start, so no flagged episode runs in a root another one deletes. Only the episode whose claim it
is deletes the root, and only after its line exists; an episode that ends without a line releases
its claim, and one that dies leaves it (the root then stays, as before). An episode without the
flag claims nothing, so it is not protected from a flagged one in the same root; one root per
episode is the corpus rule anyway. Only a root that is a real directory (not a link) named by 32
lowercase hex digits, directly inside a canonical runs directory and canonical itself, is ever
claimed or touched; anything else is left alone.
Residual: an entry the Owner cannot delete (a directory the worker made unwritable) stays, the
root and its claim stay with it, and the report says how many entries are left.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from experimental.local_worker.canary import INSTALLED_RUNS  # noqa: F401  (cli.py reads it here)
from experimental.local_worker.corpus_io import InputRefused

CLAIM = ".cairnwork-episode-claim"
KEEP_SUFFIXES = (".json", ".jsonl", ".md")
_TAG = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Claim:
    root: Path
    episode_id: str


def eligible(root: Path, runs: Path) -> bool:
    """root is a real directory <runs>/<32 hex>, and both paths are canonical."""
    if not root.is_absolute() or root.parent != runs or not _TAG.fullmatch(root.name):
        return False
    if os.path.realpath(runs) != str(runs) or os.path.realpath(root) != str(root):
        return False
    try:
        return stat.S_ISDIR(os.lstat(root).st_mode)
    except OSError:
        return False


def _why(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def claim(root: Path, runs: Path, episode_id: str) -> Claim | None:
    """Claim root for this episode; None when it is not an eligible run root (left alone)."""
    if not eligible(root, runs):
        return None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(root / CLAIM, flags, 0o600)
    except FileExistsError:
        raise InputRefused(f"run root {root.name} is claimed by another episode") from None
    except OSError as exc:
        raise InputRefused(
            f"run root {root.name}: claim could not be created ({_why(exc)})"
        ) from exc
    try:
        os.write(fd, (episode_id + "\n").encode())
    except OSError as exc:
        (root / CLAIM).unlink(missing_ok=True)  # an empty claim would refuse every later start
        raise InputRefused(
            f"run root {root.name}: claim could not be written ({_why(exc)})"
        ) from exc
    finally:
        os.close(fd)
    return Claim(root, episode_id)


def _claim_matches(c: Claim, dir_fd: int | None = None) -> bool:
    """The claim file holds this episode's id; read through dir_fd when given."""
    try:
        if dir_fd is None:
            fd = os.open(c.root / CLAIM, os.O_RDONLY | os.O_NOFOLLOW)
        else:
            fd = os.open(CLAIM, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as f:  # bytes: a claim that is not text is simply not ours
        return f.read() == (c.episode_id + "\n").encode()


def _mine(c: Claim, runs: Path) -> bool:
    return eligible(c.root, runs) and _claim_matches(c)


def remove(c: Claim, runs: Path) -> str:
    """Delete this episode's root (but its kept result files); the outcome, for the report."""
    if not eligible(c.root, runs):
        return "kept (not this episode's claim)"
    # The directory is opened first and the claim is read through that same fd, then every
    # delete goes through it too: whatever the root's name points to afterwards (a link, or a
    # real directory renamed into place), the claim checked and the entries deleted are the
    # same directory's, so nothing else is touched.
    try:
        fd = os.open(c.root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError:
        return "kept (not this episode's claim)"
    try:
        if not _claim_matches(c, fd):
            return "kept (not this episode's claim)"
        kept, left = 0, 0
        with os.scandir(fd) as it:
            entries = [e for e in it if e.name != CLAIM]
        for e in entries:
            if e.is_file(follow_symlinks=False) and e.name.endswith(KEEP_SUFFIXES):
                kept += 1
                continue
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.name, ignore_errors=True, dir_fd=fd)  # never follows a link
            else:
                try:
                    os.unlink(e.name, dir_fd=fd)  # a file or a link (only the link itself goes)
                except FileNotFoundError:
                    pass
            try:
                os.lstat(e.name, dir_fd=fd)
                left += 1
            except FileNotFoundError:
                pass
        if left:
            return f"partial ({left} entries left)"
        os.unlink(CLAIM, dir_fd=fd)
    finally:
        os.close(fd)
    if kept:
        return f"reduced ({kept} result files kept)"
    os.rmdir(c.root)  # path-based on purpose: rmdir needs the parent, and it refuses a link
    return "removed"


def finish(c: Claim | None, runs: Path, delete: bool) -> None:
    """At the episode's end: delete the root (delete: its line exists) or release the claim.
    Never raises, so it cannot replace the episode's own outcome."""
    if c is None:
        return
    try:
        if delete:
            outcome = remove(c, runs)
        else:
            outcome = "kept"
            if _mine(c, runs):
                (c.root / CLAIM).unlink()
    except OSError as exc:
        outcome = f"partial ({type(exc).__name__})"
    try:
        print(f"LOCAL_WORKER_RUN_ROOT {outcome} {c.root.name}", file=sys.stderr)
    except (OSError, ValueError):  # a broken or closed stderr must not replace the outcome
        pass
