"""Remove a finished verifier's ``private/run`` without following symlinks (Issue #392).

Account mode leaves ``private`` group-executable (``share_run``) so the account can enter
``run/``. Only ``home`` and ``tmp`` used to be deleted, so the next account run could read
whatever the last one wrote there, including a black-box input under ``private/guard`` while
``private`` stayed ``0750``. After the verdict is read, ``close_private`` deletes the whole
``run`` directory and chmods ``private`` to ``0700`` (the log, ini, profile and guard stay, for
the owner). A run killed before that ``finally`` is caught by ``stale_run_error``, called under
the account lock after ``require_idle`` and before the next account run. Owner mode has no such
lock, so it only uses ``close_private``.

Nothing here is deleted unless ``lstat`` shows ``private`` and ``run`` are real directories, their
``realpath`` is strictly inside the run root, and the private directory's name is one
``mkdtemp`` makes (``<checkout>.<8 chars>``). A symlink is not removed and not followed.
``shutil.rmtree`` is used only where ``avoids_symlink_attacks`` is true. A failure of
``close_private`` never raises: the verdict is already decided, and the next account run refuses
to start while the residue is still there. That refusal names the path and how the owner cleans
it up by hand. ``private`` is chmod'd to 0700 only after ``run/`` is actually gone: locking it
sooner would stop the account from entering, and the printed recovery steps would not work.
Inode quotas are a separate issue.

Who can write which parent, after ``share_run`` (this is why the check-then-delete gap is not
the account's to win). ``run_root`` is owner:cairnwork 2750 (setgid): the owner can create and
unlink names there; the account is in the group, so it can list and traverse, not write, and
cannot replace a private directory with a symlink. ``private`` is 0750, owned by the owner: the
account can traverse and read, not write, and cannot replace ``run`` with a symlink. ``run`` is
03770 (sticky, setgid): the account can create names inside it, including symlinks, and can
unlink only names it owns. The owner's ``home`` and ``tmp`` stay. Those inner symlinks are not
followed (``rmtree`` only when ``avoids_symlink_attacks``). Only the owner can retarget
``private`` or ``run``, and the owner is this process, holding the account lock.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# tempfile.mkdtemp(prefix=f"{checkout.name}.") appends 8 chars from [a-z0-9_]. checkout.name
# ({run_id}-vN or {run_id}-vbb) has no dot, so a private dir has exactly one, before that suffix.
_PRIVATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.[a-z0-9_]{8}$")


def _real_dir(path: Path) -> Path | None:
    """Resolved path if ``path`` itself is a directory. A symlink is None: never follow it."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return None
    return Path(os.path.realpath(path))


def _private_name(name: str) -> bool:
    return _PRIVATE_NAME.fullmatch(name) is not None


def run_tree_ok(private: Path, root: Path) -> bool:
    """True when ``private`` and ``private/run`` may be deleted.

    Both must be real directories (not symlinks), ``private`` a direct child of ``root`` by
    realpath, ``run`` a direct child of ``private``, and the name a private-dir name.
    """
    base, own = _real_dir(root), _real_dir(private)
    scratch = _real_dir(private / "run")
    if not (base and own and scratch and _private_name(private.name)):
        return False
    if scratch.name != "run" or own.name != private.name:
        return False
    return own.parent == base and scratch.parent == own


def _shown(path: Path) -> str:
    """One line: a leftover name must not start a second line in the error."""
    return str(path).replace("\r", "\\r").replace("\n", "\\n")


def _hint(user: str, path: Path) -> str:
    """Owner instructions that work while ``private`` is still group-traversable (0750).

    The steps are separated by ``;``, not ``&&``: a failed account ``rm`` must not skip the
    owner's. ``chmod`` follows a symlink, so it is only in the real-directory steps. On macOS
    ``chflags -R nouchg`` clears the user-immutable flag first; Linux has no ``chflags``.
    """
    private = path.parent if path.name == "run" else path
    run = private / "run"
    q_run, q_priv, q_user = shlex.quote(str(run)), shlex.quote(str(private)), shlex.quote(user)
    steps = "; ".join(
        (
            f"chflags -R nouchg {q_run}",
            f"sudo -n -u {q_user} -- /bin/rm -rf -- {q_run}",
            f"rm -rf -- {q_run}",
            f"chmod 700 {q_priv}",
        )
    )
    return (
        f"leftover {_shown(path)} blocks the next account run. Clean it by hand without following "
        f"links: ls -ld {q_priv} {q_run}. If either is a symlink, unlink that symlink only and do "
        f"not remove or chmod its target. If both are real directories under the run root, run "
        f"every step even when an earlier one fails (macOS chflags first): {steps}."
    )


def seal_private(private: Path, root: Path) -> None:
    """chmod ``private`` to 0700 through a no-follow directory fd. A symlink is left alone."""
    base, own = _real_dir(root), _real_dir(private)
    if not (base and own and _private_name(private.name) and own.parent == base):
        return
    fd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _alert(text: str) -> None:
    print(f"ALERT verifier cleanup: {text}", file=sys.stderr, flush=True)


def _account_remove(account, run: Path) -> None:
    """Best-effort ``account.remove``. Importing ``VerifierAccountError`` would cycle."""
    try:
        account.remove(run)
    except (OSError, subprocess.SubprocessError) as exc:
        _alert(f"account remove of {_shown(run)} failed ({type(exc).__name__})")
    except Exception as exc:  # noqa: BLE001 - only VerifierAccountError is swallowed
        if type(exc).__name__ != "VerifierAccountError":
            raise
        _alert(f"account remove of {_shown(run)} failed ({type(exc).__name__})")


def _delete_run(account, run: Path) -> None:
    """``account.remove`` then the owner's ``rmtree``. Never follows a symlink; never raises.

    Refuses outright when ``rmtree`` would follow symlinks. The account cannot swap ``run`` for a
    symlink in the gap after this check: it cannot write ``private`` (0750, owner) or ``run_root``
    (2750, owner). See the module docstring.
    """
    if not shutil.rmtree.avoids_symlink_attacks:
        _alert(f"{_shown(run)} left in place; rmtree on this platform follows symlinks")
        return
    if _real_dir(run) is None:
        _alert(f"{_shown(run)} is not a safe directory; not removed")
        return
    if account is not None:
        _account_remove(account, run)
    shutil.rmtree(run, ignore_errors=True)
    if os.path.lexists(run):
        _alert(f"{_shown(run)} was not removed")


def close_private(private: Path, account) -> None:
    """Delete ``private/run`` and chmod ``private`` to 0700. Never raises.

    A symlink, a path outside the run root or an unexpected name is left in place (stderr
    ALERT). The verdict already stands. Owner mode passes ``account=None``.
    """
    try:
        root = private.parent if account is None else account.run_root
        run = private / "run"
        if run_tree_ok(private, root):
            _delete_run(account, run)
        elif _real_dir(private) is not None and os.path.lexists(run):
            _alert(f"{_shown(run)} is not a safe directory; not removed")
        elif os.path.lexists(private) and _real_dir(private) is None:
            _alert(f"{_shown(private)} is not a safe directory; not removed")
        # 0700 only once run/ is gone. Locking it earlier blocks the account rm in the
        # recovery steps, which need to traverse private (still 0750).
        if _real_dir(private) is not None and not os.path.lexists(run):
            seal_private(private, root)
    except OSError:
        _alert(f"{_shown(private)} could not be closed")


def _consider(account, root: Path, name: str, stuck: list[Path]) -> None:
    """One direct child of the run root. ``.work`` is not passed here."""
    private = root / name
    try:
        info = os.lstat(private)
    except OSError:
        stuck.append(private)
        return
    if stat.S_ISLNK(info.st_mode):  # do not follow, do not unlink: the owner decides
        stuck.append(private)
        return
    if not stat.S_ISDIR(info.st_mode):
        return
    run = private / "run"
    # ``private`` is a real directory, so joining ``run`` does not escape through a link.
    if not os.path.lexists(run):
        if _private_name(name):
            _seal(private, root, stuck)
        return
    if not run_tree_ok(private, root):
        stuck.append(run)  # leave private traversable so the recovery steps can enter it
        return
    _delete_run(account, run)
    if os.path.lexists(run):
        stuck.append(run)
        return
    _seal(private, root, stuck)


def _seal(private: Path, root: Path, stuck: list[Path]) -> None:
    try:
        seal_private(private, root)
    except OSError:
        stuck.append(private)


def _refuse(account, stuck: list[Path]) -> str:
    shown = ", ".join(_shown(path) for path in stuck[:8])
    extra = f" (+{len(stuck) - 8} more)" if len(stuck) > 8 else ""
    return f"{shown}{extra}. {_hint(account.user, stuck[0])}"


def stale_run_error(account) -> str | None:
    """Why the next account run must not start, or None when no private/run residue remains.

    Call under the account lock, after ``require_idle``, before creating this run's directories.
    A directory still in use cannot be here: the lock is exclusive and owner mode does not call
    this. ``.work`` checkouts are left to recovery.
    """
    root = account.run_root
    if _real_dir(root) is None:
        shown = _shown(root)
        return (
            f"{shown} is not a real directory. The owner must fix it without following links: "
            f"ls -ld {shown}. If it is a symlink, unlink it only and do not remove its target."
        )
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        return f"cannot list {_shown(root)}: {exc}. {_hint(account.user, root)}"
    stuck: list[Path] = []
    for name in names:
        if name.endswith(".work"):
            continue
        _consider(account, root, name, stuck)
    return _refuse(account, stuck) if stuck else None
