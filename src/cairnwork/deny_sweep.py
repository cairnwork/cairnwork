"""#1124: remove ``cairnwork-deny.*`` directories left in the temp dir by recorders that died
(SIGKILL skips ``recording()``'s ``finally``). Only real directories directly in the temp dir,
owned by this uid, untouched for over an hour, and not owned by a live recorder (``owner.pid``)."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

SWEEP_PREFIX = "cairnwork-deny."
SWEEP_MAX_REMOVE = 50
SWEEP_STALE_SECONDS = 3600.0


def _is_live_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False


def sweep_stale(
    temp_dir: Path | str | None = None,
    now: float | None = None,
    max_remove: int = SWEEP_MAX_REMOVE,
) -> int:
    """Remove leftover cairnwork-deny.* directories older than 1h from dead recorders."""
    target_dir = Path(tempfile.gettempdir() if temp_dir is None else temp_dir)
    current_time = time.time() if now is None else now
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    removed = 0
    try:
        entries = os.scandir(target_dir)
    except OSError:
        return 0
    with entries:
        for entry in entries:
            if removed >= max_remove:
                break
            if not entry.name.startswith(SWEEP_PREFIX):
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                if current_uid is not None and st.st_uid != current_uid:
                    continue
                newest = st.st_mtime
                try:
                    with os.scandir(entry.path) as children:
                        for child in children:
                            try:
                                cst = child.stat(follow_symlinks=False)
                                if cst.st_mtime > newest:
                                    newest = cst.st_mtime
                            except OSError:
                                pass
                except OSError:
                    pass
                if current_time - newest <= SWEEP_STALE_SECONDS:
                    continue
                pid_path = os.path.join(entry.path, "owner.pid")
                try:
                    pst = os.lstat(pid_path)
                    if stat.S_ISREG(pst.st_mode):
                        with open(pid_path, encoding="utf-8") as f:
                            pid = int(f.read().strip())
                        if _is_live_pid(pid):
                            continue
                except (OSError, ValueError):
                    pass
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
            except OSError:
                pass
    return removed
