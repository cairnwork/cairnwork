"""A verifier checkout the verifier account owns (Issue #240), and checkout removal.

In owner mode the sandboxed verifier runs as the owner of its checkout: it may write files, set
explicit times (``os.utime(path, (t, t))``), chmod and atomically replace them. Write permission
alone gives another uid only the first: explicit times and chmod need ownership (POSIX), so an
honest test such as a template cache keyed by mtime failed under the account with EACCES.

So under the account the checkout is the account's own copy:

1. the owner clones the candidate as always (``create_attempt_clone``: ``--no-local``, base
   history only, checked against the tree) into ``work/.cairnwork-source/<clone_id>`` and opens it
   to the account for reading only (``VerifierAccount.grant_read``, #91);
2. AS THE ACCOUNT, a fixed helper (``COPY_HELPER``, run by the verifier's own Python with ``-I -S``
   through the same sudo + sandbox-binary path ``account.remove`` uses) copies it to
   ``work/<clone_id>``: directories recreated (iteratively: any depth), regular files copied with
   mode and times (every byte written), symlinks copied as symlinks (never followed), any other
   file type refused before it is opened, and the destination must not exist;
3. the owner checks the copy (a real directory, by lstat, owned by the account, equal to the
   candidate tree) and removes the staging clone. The verifier then runs in ``work/<clone_id>``.

After the run the owner compares the copy (``checkout_differences``): ``_same_directory`` first;
then, once ``kill_all`` confirms no process of the account is left, AS THE ACCOUNT ``SHARE_HELPER``
sets each directory's and regular file's group and other r and x bits equal to its user r and x
bits (whatever they were before), and then the owner runs ``tree_differences``. So the owner reads
exactly what it could read as the checkout's owner in owner mode: a file replaced from a 0600
temporary file or a directory left at 0700 is compared as in owner mode (mode bits other than the
owner's execute bit are not judged), and a path closed to its own owner (0000, or 0044 / 0077,
open to group and other only) is "unreadable", which fails the run in both modes. The helper walks
with descriptors opened component by component with O_NOFOLLOW and never follows a symlink. Parity
limits, failing closed in account mode only: a regular file with more than one link whose mode
would need a change is left as it is (its inode may have a name outside the copy, which this must
never touch), and the run fails with "checkout not shared with the owner"; so it does when a
directory open to its user cannot be walked (a macOS ACL denying the account alone), anything
the owner can reach carries a macOS extended ACL (one can grant the owner what the mode closes to
the user), a chmod fails, the share step fails or outlives ``SHARE_TIMEOUT_SECONDS``, or the
account's processes cannot be confirmed ended.

A helper that outlives its timeout (``subprocess.run`` kills only sudo) is ended with
``account.kill_all()``: callers hold the account's lock (``VerifierAccount.exclusive``), so every
process of the account is this run's. ``remove_checkout`` removes the work dir: the account first,
then the owner; a failure there is reported (ALERT) and never changes the run's verdict. Nothing
in the checkout is ever executed, used as git state or trusted by the owner.
"""

from __future__ import annotations

import ctypes
import errno
import os
import pwd
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from cairnwork.treecompare import tree_differences
from cairnwork.verifier_account import VerifierAccountError
from cairnwork.verifier_exec import _same_directory
from cairnwork.workspace import WorkspaceError, create_attempt_clone, remove_attempt_clone

STAGE = ".cairnwork-source"
COPY_TIMEOUT_SECONDS = 600
SHARE_TIMEOUT_SECONDS = 120
ACL_TYPE_EXTENDED = 0x100  # sys/acl.h (macOS)

# Runs as the account with ``python -I -S -c COPY_HELPER <source> <target>``. Exit 0 only if
# every entry was copied; any error (a refused file type included) exits non-zero.
COPY_HELPER = r"""
import os, stat, sys

def copy_file(s, d, info):
    fd = os.open(s, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            sys.exit(f"swapped while copying: {s}")
        out = os.open(d, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                chunk = memoryview(os.read(fd, 1 << 20))
                if not chunk:
                    break
                while chunk:  # os.write may write less than it was given
                    chunk = chunk[os.write(out, chunk):]
            os.fchmod(out, stat.S_IMODE(info.st_mode))
            os.utime(out, ns=(info.st_atime_ns, info.st_mtime_ns))
        finally:
            os.close(out)
    finally:
        os.close(fd)

def copy(source, target):
    stack = [(source, target, None)]  # an explicit stack, not recursion: any depth
    while stack:
        src, dst, done = stack.pop()
        if done is not None:  # after its contents: the directory's own mode and times
            os.chmod(dst, stat.S_IMODE(done.st_mode))
            os.utime(dst, ns=(done.st_atime_ns, done.st_mtime_ns))
            continue
        os.mkdir(dst, 0o700)
        stack.append((src, dst, os.lstat(src)))
        for name in sorted(os.listdir(src)):
            s, d = os.path.join(src, name), os.path.join(dst, name)
            info = os.lstat(s)
            if stat.S_ISLNK(info.st_mode):
                os.symlink(os.readlink(s), d)
            elif stat.S_ISDIR(info.st_mode):
                stack.append((s, d, None))
            elif stat.S_ISREG(info.st_mode):
                copy_file(s, d, info)
            else:
                sys.exit(f"refused file type: {s}")

source, target = sys.argv[1], sys.argv[2]
if os.path.lexists(target):
    sys.exit(f"target exists: {target}")
if not stat.S_ISDIR(os.lstat(source).st_mode):
    sys.exit(f"source is not a directory: {source}")
copy(source, target)
"""

# Runs as the account with ``python -I -S -c SHARE_HELPER <checkout>`` after the run, once no
# process of the account is left (``checkout_differences``). Each directory and regular file gets
# group and other r/x equal to the user's (other bits unchanged), so the comparing owner may read
# exactly what the checkout's owner may. Directories are opened one component at a time with
# O_NOFOLLOW from the work dir, and chmod never follows a symlink, so the walk cannot leave the
# copy. Symlinks and other file types are skipped, so is the top-level .git (not compared). A
# directory its user may not list and search is not entered: the comparing owner, with the same
# bits, cannot read it either. Exit 1 (the owner then fails the run) if a directory its user may
# list and search cannot be walked (a macOS ACL can deny the account alone), a regular file with
# more than one link needs a change (its inode may have a name outside the copy, which this must
# never touch), or a chmod fails.
SHARE_HELPER = r"""
import os, stat, sys

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
problems = []

def opened(base, parts):
    fd = os.open(base, DIR)
    try:
        for name in parts:  # one component at a time: a symlink anywhere is refused
            inner = os.open(name, DIR, dir_fd=fd)
            os.close(fd)
            fd = inner
    except BaseException:
        os.close(fd)
        raise
    return fd

def share(fd, name, info, rel):
    user = info.st_mode & 0o500
    want = user >> 3 | user >> 6
    if info.st_mode & 0o055 == want:
        return
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        problems.append(f"more than one link: {rel}")
        return
    try:
        mode = stat.S_IMODE(info.st_mode) & ~0o055 | want
        os.chmod(name, mode, dir_fd=fd, follow_symlinks=False)
    except (OSError, NotImplementedError, ValueError) as exc:
        problems.append(f"chmod {rel}: {exc}")

def entries(base, parts, info):
    # a directory its user may list and search must be walked whole; any other is left closed
    walk = info.st_mode & 0o500 == 0o500
    rel = "/".join(parts[1:]) or "."
    try:
        fd = opened(base, parts)
    except OSError as exc:
        if walk:
            problems.append(f"cannot open {rel}: {exc}")
        return
    try:
        for name in sorted(os.listdir(fd)):
            try:
                yield fd, name, os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as exc:
                if walk:
                    problems.append(f"cannot stat {rel}/{name}: {exc}")
    except OSError as exc:
        if walk:
            problems.append(f"cannot list {rel}: {exc}")
    finally:
        os.close(fd)

base, top = os.path.split(os.path.abspath(sys.argv[1]))
fd = opened(base, ())
try:
    info = os.stat(top, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        sys.exit(f"not a directory: {top}")
    share(fd, top, info, ".")
finally:
    os.close(fd)
stack = [((top,), info)]  # an explicit stack, not recursion: any depth
while stack:
    parts, dir_info = stack.pop()
    for fd, name, info in entries(base, parts, dir_info):
        rel = "/".join((*parts[1:], name))
        if stat.S_ISDIR(info.st_mode) and rel != ".git":
            share(fd, name, info, rel)
            stack.append(((*parts, name), info))
        elif stat.S_ISREG(info.st_mode):
            share(fd, name, info, rel)
if problems:
    sys.exit("\n".join(problems[:20]))
"""


def _as_account(account, python: str, helper: str, args: list[str], timeout: int):
    """Run a fixed helper as the account, unconfined; a timeout also ends the helper itself."""
    argv = account.wrap([python, "-I", "-S", "-c", helper, *args], None)
    try:
        return subprocess.run(  # noqa: S603 - fixed helper, paths made here
            argv, capture_output=True, text=True, timeout=timeout, check=False, cwd=account.root
        )
    except subprocess.TimeoutExpired:
        account.kill_all()  # the caller holds the account's lock: its processes are this run's
        raise


def make_account_checkout(account, python: str, repo: Path, commit: str, work: Path, clone_id: str):
    """Return ``work/<clone_id>``, a checkout of ``commit`` owned by the account; raise
    WorkspaceError if it cannot be made exactly. ``python`` is the verifier's interpreter, already
    proven runnable as the account (``available``). The staging clone is always removed."""
    stage = work / STAGE
    stage.mkdir(mode=0o750)
    target = work / clone_id
    try:
        source = create_attempt_clone(repo, commit, stage, clone_id).path
        account.grant_read(stage)
        done = _as_account(
            account, python, COPY_HELPER, [str(source), str(target)], COPY_TIMEOUT_SECONDS
        )
        if done.returncode != 0:
            raise WorkspaceError(f"account copy failed: {done.stderr.strip()[-500:]}")
        _check_copy(target, account, repo, commit)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspaceError(f"account copy failed: {exc}") from exc
    finally:
        try:
            remove_attempt_clone(stage, clone_id)
        except WorkspaceError:
            pass  # a leftover staging clone is only disk space; rmtree below tries again
        shutil.rmtree(stage, ignore_errors=True)
    return target


def _check_copy(target: Path, account, repo: Path, commit: str) -> None:
    info = os.lstat(target)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != pwd.getpwnam(account.user).pw_uid:
        raise WorkspaceError(f"{target} is not a directory owned by {account.user}")
    differences = tree_differences(repo, commit, target)
    if differences:
        raise WorkspaceError(f"account copy differs from {commit}: {', '.join(differences[:5])}")


def checkout_differences(repo: Path, commit: str, checkout: Path, before, account, python: str):
    """What the run changed in its checkout, as ``tree_differences`` names it; under the account,
    first open the account's copy to the owner as described above. If that cannot be done exactly
    the run fails: the answer is then why the copy was not shared."""
    if not _same_directory(checkout, before):  # renamed away, or a symlink put in place
        return ("checkout replaced",)
    if account is not None:
        unshared = _share_with_owner(account, python, checkout)
        if unshared:
            return (f"checkout not shared with the owner: {unshared}",)
    return tuple(tree_differences(repo, commit, checkout))


def _share_with_owner(account, python: str, checkout: Path) -> str:
    """Run SHARE_HELPER on the account's copy; "" when it did, else why not. It runs only once
    ``kill_all`` confirms that no process of the account is left (the caller holds its lock), so
    nothing can change the copy while the helper walks it."""
    try:
        left = account.kill_all()
    except (OSError, subprocess.SubprocessError):
        left = -1
    if left != 0:
        return "the account's processes could not be confirmed ended"
    try:
        done = _as_account(account, python, SHARE_HELPER, [str(checkout)], SHARE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"share step failed: {exc}"
    if done.returncode != 0:
        return f"share step failed: {done.stderr.strip()[-300:]}"
    acls = _extended_acls(checkout)
    return f"extended ACL on {', '.join(acls[:5])}" if acls else ""


def _extended_acls(checkout: Path) -> list[str]:
    """macOS: what the owner can reach in the account's copy that carries an extended ACL. An ACL
    can grant the owner a directory or file whose mode closes it to its user, which the share step
    can neither see nor narrow, so any ACL fails the run, and so does one the owner cannot read (an
    ACL may deny it read-security). Linux POSIX ACLs are bounded by the mask, which the share
    step's chmod sets to the user's r/x. The top-level .git is not compared."""
    if sys.platform != "darwin":
        return []
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.acl_get_link_np.restype = ctypes.c_void_p
    lib.acl_get_link_np.argtypes = (ctypes.c_char_p, ctypes.c_int)
    lib.acl_free.argtypes = (ctypes.c_void_p,)
    found = set()
    for top, dirs, files in os.walk(checkout, followlinks=False):  # what the owner can reach
        if top == os.fspath(checkout):
            dirs[:] = [d for d in dirs if d != ".git"]
            files = [f for f in files if f != ".git"]
        for path in (top, *(os.path.join(top, name) for name in (*dirs, *files))):
            ctypes.set_errno(0)
            acl = lib.acl_get_link_np(os.fsencode(path), ACL_TYPE_EXTENDED)  # never follows
            if acl:
                lib.acl_free(acl)
            if acl or ctypes.get_errno() != errno.ENOENT:  # ENOENT: none; other: cannot tell
                found.add(os.path.relpath(path, checkout))
    return sorted(found)


def remove_checkout(work: Path, clone_id: str, account) -> None:
    """Remove a verifier's work dir and checkout. A leftover is only disk space and never changes
    the run's verdict (decided before this runs), but it is reported (ALERT on stderr).

    Under the account the checkout is the account's own copy: only the account can remove it
    (``account.remove``), and that is what guarantees it goes. Its failure (a path outside its run
    root, ``rm`` timing out on a huge tree) is caught; a timed-out ``rm`` is ended by the next
    ``kill_all`` (the run's reap or the next ``require_idle``). The owner then removes the rest of
    ``work`` (it owns ``work`` and any staging clone a failure left). That rmtree may walk what the
    account left, but it never follows a symlink out (``shutil.rmtree.avoids_symlink_attacks``,
    true on macOS and Linux, pinned by a test) and only unlinks names. remove_attempt_clone is the
    owner path only: its chmod pass cannot touch the account's directories."""
    try:
        if account is not None:
            account.remove(work)
        else:
            remove_attempt_clone(work, clone_id)
    except (WorkspaceError, VerifierAccountError, OSError, subprocess.SubprocessError):
        pass  # the run's verdict stands; what is left is reported below
    finally:  # whatever the first removal did, the owner removes what it can
        shutil.rmtree(work, ignore_errors=True)
        if os.path.lexists(work):
            print(f"ALERT verifier cleanup: {work} was not removed", file=sys.stderr, flush=True)
