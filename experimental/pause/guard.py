"""AC8 of #476: the checkout digest that checks the read-only guard (step 3b).

The guard itself is AC8 (a): until the plan is delivered, the worker's tests run only through
local_worker's ``pytest-ro``, whose Seatbelt profile denies every write in the checkout, to the
test process and everything it starts (controller.PrePlanGuard). This digest checks that it held:
taken when the guard starts and again when the plan arrives, and any difference stops the run
(GuardUnavailable). Nothing is repaired: TreeGuard is a CheckoutGuard, not a RollbackGuard.

``TreeGuard.digest`` walks the checkout itself (lstat, never following a symlink) rather than
asking git: every entry under the root, ``.git`` included, as its path, kind, permission bits,
the sha256 of its content or its symlink target, and its owner, extended attributes and ACL
(guard_meta). git's view can be narrowed from inside the checkout (assume-unchanged,
skip-worktree, status.showUntrackedFiles, info/exclude); the file system's cannot. An entry it
cannot read, more than ``max_files`` entries or ``max_bytes`` of content, a missing or
symlinked root, or a regular file with more than one hard link raises GuardUnavailable.

The last is the read-only guard's precondition: pytest-ro's deny is by path, so a second name
for a checkout file outside the checkout would take writes the deny does not see. None may exist
when the guard starts (checked by this walk, and again when the plan arrives), and none can
appear in the window: the tool profile denies every hard link (``deny file-link``) to the tests
and everything they start."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from experimental.pause import guard_meta
from experimental.pause.interfaces import GuardUnavailable

MAX_FILES = 100_000
MAX_BYTES = 1 << 30
_CHUNK = 1 << 20
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def outside(path: Path | str, checkout: Path | str) -> bool:
    """Neither path lies inside the other (after resolving symlinks)."""
    real, top = os.path.realpath(path), os.path.realpath(checkout)
    return os.path.commonpath([real, top]) not in (real, top)


class TreeGuard:
    """The CheckoutGuard of AC8 over a real checkout; see the module docstring."""

    def __init__(self, root: Path, *, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES):
        self.root, self.max_files, self.max_bytes = Path(root), max_files, max_bytes

    def digest(self) -> str:
        try:
            root_fd = os.open(self.root, _DIR)
        except OSError as e:
            raise GuardUnavailable(f"checkout root: {e.strerror}") from e
        h = hashlib.sha256()
        try:
            st = os.fstat(root_fd)
            meta = guard_meta.read(os.fsencode(self.root), st)
            self._add(h, ".", "d", stat.S_IMODE(st.st_mode), b"", meta)
            self._walk(h, root_fd, "", [1, meta.size()])
        except (OSError, RecursionError) as e:
            raise GuardUnavailable(f"checkout walk: {e}") from e
        finally:
            os.close(root_fd)
        return h.hexdigest()

    @staticmethod
    def _add(h, rel: str, kind: str, mode: int, ref: bytes, meta: guard_meta.Meta) -> None:
        h.update(b"%s\0%s\0%o\0%s\0%s\0" % (os.fsencode(rel), kind.encode(), mode, ref, meta.key()))

    def _walk(self, h, fd: int, prefix: str, count: list[int]) -> None:
        """Entries in sorted order, depth first; ``count`` is [entries, bytes] so far."""
        with os.scandir(fd) as it:
            names = sorted(e.name for e in it)
        for name in names:
            rel = prefix + name
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            meta = guard_meta.read(os.fsencode(self.root / rel), st)
            count[0] += 1
            count[1] += meta.size() + (st.st_size if stat.S_ISREG(st.st_mode) else 0)
            if count[0] > self.max_files or count[1] > self.max_bytes:
                raise GuardUnavailable(
                    f"the checkout exceeds {self.max_files} entries or {self.max_bytes} bytes"
                )
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                self._add(h, rel, "d", mode, b"", meta)
                sub = os.open(name, _DIR, dir_fd=fd)
                try:
                    self._walk(h, sub, rel + "/", count)
                finally:
                    os.close(sub)
            elif stat.S_ISLNK(st.st_mode):
                self._add(h, rel, "l", 0, os.fsencode(os.readlink(name, dir_fd=fd)), meta)
            elif stat.S_ISREG(st.st_mode):
                if st.st_nlink != 1:  # a second name, maybe outside the checkout's write deny
                    raise GuardUnavailable(f"{rel!r} has {st.st_nlink} hard links")
                self._add(h, rel, "f", mode, self._content(fd, name, st), meta)
            else:
                self._add(h, rel, "o", mode, b"", meta)

    @staticmethod
    def _content(fd: int, name: str, st: os.stat_result) -> bytes:
        src = os.open(name, _READ, dir_fd=fd)
        h = hashlib.sha256()
        try:
            got = os.fstat(src)
            if not stat.S_ISREG(got.st_mode) or (got.st_dev, got.st_ino) != (st.st_dev, st.st_ino):
                raise GuardUnavailable(f"{name} changed while the guard read it")
            while chunk := os.read(src, _CHUNK):
                h.update(chunk)
        finally:
            os.close(src)
        return h.hexdigest().encode()
