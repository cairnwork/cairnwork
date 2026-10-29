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
``shutil.rmtree`` is used only where ``avoids_symlink_attacks`` is true. An expected failure of
``close_private`` (``OSError``, a subprocess error, ``VerifierAccountError``) does not raise: the
verdict is already decided, and the next account run refuses to start while the residue is still
there. That refusal names the path and how the owner cleans it up by hand. Any other exception is
a bug and propagates on purpose, replacing the verdict (fail-closed). ``private`` is chmod'd to
0700 only after ``run/`` is actually gone: locking it sooner would stop the account from
entering, and the printed recovery steps would not work.
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

A candidate owns its files under HOME and TMPDIR, so on macOS it can set ``UF_IMMUTABLE`` or
``UF_APPEND``, an ACL that denies delete, or one that denies even ``readsecurity`` (then ``lstat``
fails for the owner of the file too). Only that account (or root) can undo them. The account
therefore runs ``_UNLOCK`` through the sandbox binary sudoers already allows, before ``rm``
(Issue #437). ``chflags -R`` and ``chmod -R`` stat each entry first and stop at a directory they
cannot read, so nested locks of different kinds defeat any fixed order of them, and Apple's
``chmod -R -N`` clears a symlink's target. Seatbelt cannot stop the locks being set: denying
``file-write-flags`` breaks ``shutil.copy2``, and an ACL given at creation (``mkdirx_np``,
``openx_np``) passes a ``file-write-acl`` deny.
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


_SUDO = "/usr/bin/sudo"
_ALLOW_ALL = "(version 1) (allow default)"

# Run by the verifier python as the account (macOS): argv is run, then the node, depth and second
# limits. One pre-order pass. Per entry, before relying on lstat: clear its ACL (setattrlist with
# FSOPT_NOFOLLOW: the entry itself, a symlink too, never a target; no open, so a denied read or
# readsecurity does not stop it) and uchg/uappnd (the chflags syscall needs no stat). A directory
# on run's device gets u+rwx and is entered through an O_NOFOLLOW descriptor, so names stay
# relative (no PATH_MAX). At a limit it exits 1 and leaves the rest: rm fails, the scrub refuses.
_UNLOCK_SOURCE = """\
import ctypes, os, stat, struct, sys, time
top, max_nodes, max_depth, seconds = sys.argv[1], *map(int, sys.argv[2:5])
LOCKS = stat.UF_IMMUTABLE | stat.UF_APPEND
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
lib.setattrlist.argtypes = (ctypes.c_char_p,) * 3 + (ctypes.c_size_t, ctypes.c_uint32)
ATTRS = struct.pack("=HHIIIII", 5, 0, 0x400000, 0, 0, 0, 0)
FILESEC = struct.pack("=I16s16sII", 0x012CC16D, bytes(16), bytes(16), 0xFFFFFFFF, 0)
NO_ACL = struct.pack("=iI", 8, len(FILESEC)) + FILESEC
def attempt(call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except OSError:
        pass
def no_acl(name):
    lib.setattrlist(os.fsencode(name), ATTRS, NO_ACL, len(NO_ACL), 1)
def unlock(name):
    no_acl(name)
    try:
        info = os.lstat(name)
    except OSError:
        attempt(os.lchflags, name, 0)
        no_acl(name)
        try:
            info = os.lstat(name)
        except OSError:
            return None
    if info.st_flags & LOCKS:
        attempt(os.lchflags, name, info.st_flags & ~LOCKS)
        no_acl(name)
    return info
def opened(name, dev):
    fd = os.open(name, DIR)
    try:
        if os.fstat(fd).st_dev != dev:
            return None
        names = os.listdir(fd)
        os.fchdir(fd)
    finally:
        os.close(fd)
    return iter(names)
deadline, nodes = time.monotonic() + seconds, 0
dev = os.stat(top, follow_symlinks=False).st_dev
stack = [opened(top, dev)]
while stack:
    name = next(stack[-1], None)
    if name is None:
        stack.pop()
        if stack:
            os.chdir("..")
        continue
    nodes += 1
    if nodes > max_nodes or len(stack) > max_depth or time.monotonic() > deadline:
        sys.exit("unlock: limit reached, the rest is left in place")
    info = unlock(name)
    if info is None or not stat.S_ISDIR(info.st_mode) or info.st_dev != dev:
        continue
    attempt(os.chmod, name, stat.S_IMODE(info.st_mode) | 0o700, follow_symlinks=False)
    try:
        child = opened(name, dev)
    except OSError:
        child = None
    if child is not None:
        stack.append(child)
"""
_UNLOCK = f"exec({_UNLOCK_SOURCE!r})"  # one line, so the printed recovery step is one line
_UNLOCK_LIMITS = (500_000, 400, 45)  # entries, depth, seconds: under remove()'s 60 s timeout


def _unlock_argv(python: str, path: str) -> list[str]:
    """macOS: ``_UNLOCK`` by ``python`` (isolated). Linux: ``chmod -R u+rwx``, enough there."""
    if sys.platform == "darwin":
        return [python, "-I", "-c", _UNLOCK, path, *map(str, _UNLOCK_LIMITS)]
    return ["/bin/chmod", "-R", "u+rwx", "--", path]


def account_delete_argv(user: str, path: str, wrap, python: str) -> list[list[str]]:
    """Commands as ``user``, in order, before the owner's rmtree.

    ``wrap`` is the sandbox binary with an allow-all policy (what sudoers permits); ``python`` is
    the verifier python (owner-owned, the account can only run it). The flags and ACLs that stop
    ``rm`` belong to the account: the owner gets EPERM. ``rm`` is in sudoers.
    """

    def as_account(argv: list[str]) -> list[str]:
        return [_SUDO, "-n", "-u", user, "--", *wrap(argv)]

    return [
        as_account(_unlock_argv(python, path)),
        [_SUDO, "-n", "-u", user, "--", "/bin/rm", "-rf", "--", path],
    ]


def _as_account_shell(user: str, argv: list[str]) -> str:
    """One recovery step, naming the account. The sandbox form matches ``unconfined``."""
    inner = " ".join(shlex.quote(part) for part in argv)
    tool = (
        f"/usr/bin/sandbox-exec -p {shlex.quote(_ALLOW_ALL)} {inner}"
        if sys.platform == "darwin"
        else f"bwrap --bind / / --dev /dev -- {inner}"
    )
    return f"as the verifier account: sudo -n -u {shlex.quote(user)} -- {tool}"


def _recovery_steps(user: str, python: str, run: Path, private: Path) -> str:
    """Who runs each step. Joined with ``;``, not ``&&``, and no period after the last command."""
    q_run, q_priv = shlex.quote(str(run)), shlex.quote(str(private))
    steps = [_as_account_shell(user, _unlock_argv(python, str(run)))]
    account_rm = f"sudo -n -u {shlex.quote(user)} -- /bin/rm -rf -- {q_run}"
    steps.append(f"as the verifier account: {account_rm}")
    if sys.platform == "darwin":  # past a limit only root clears it: root ignores ACLs and modes
        root_rm = f"/usr/bin/chflags -R -- nouchg,nouappnd {q_run}; /bin/rm -rf -- {q_run}"
        steps.append(f"as root, only if {q_run} is still there (a limit was reached): {root_rm}")
    steps.append(f"as the owner: rm -rf -- {q_run}")
    steps.append(f"as the owner: chmod 700 {q_priv}")
    return "; ".join(steps)


def _hint(user: str, python: str, path: Path) -> str:
    """Recovery while ``private`` is still group-traversable (0750). Not for the run root itself."""
    private = path.parent if path.name == "run" else path
    run = private / "run"
    q_run, q_priv = shlex.quote(str(run)), shlex.quote(str(private))
    steps = _recovery_steps(user, python, run, private)
    return (
        f"leftover {_shown(path)} blocks the next account run. "
        f"Clean it by hand without following links: ls -ld {q_priv} {q_run}. "
        f"If either is a symlink, unlink that symlink only and do not remove or chmod its target. "
        f"If both are real directories under the run root, run every step even when an earlier "
        f"one fails. {steps}"
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
    """``account.remove`` then the owner's ``rmtree``. Never follows a symlink; an expected
    failure of ``account.remove`` is an ALERT, not raised.

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
    """Delete ``private/run`` and chmod ``private`` to 0700. Expected failures do not raise.

    A symlink, a path outside the run root or an unexpected name is left in place (stderr
    ALERT). The verdict already stands. Owner mode passes ``account=None``. An exception other
    than those ``_account_remove`` and this function catch is a bug: it propagates and replaces
    the verdict, and run/ stays for the next scrub to refuse (see the module docstring).
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
    return f"{shown}{extra}. {_hint(account.user, account.python, stuck[0])}"


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
        # Not _hint: that prints `chmod 700` of a private dir, and here the path is the run root.
        shown = shlex.quote(str(root))
        return (
            f"cannot list {_shown(root)}: {exc}. The owner must fix this directory without "
            f"following links: ls -ld {shown}. If it is a symlink, unlink it only and do not "
            f"remove its target."
        )
    stuck: list[Path] = []
    for name in names:
        if name.endswith(".work"):
            continue
        _consider(account, root, name, stuck)
    return _refuse(account, stuck) if stuck else None
