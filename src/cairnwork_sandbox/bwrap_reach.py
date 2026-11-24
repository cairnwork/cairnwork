"""Whether the dedicated verifier account may enter a directory, for the account-mode credential
masks (#93, #341, #358).

bwrap mounts as the verifier uid, which keeps its own groups (sudo sets them, and an unprivileged
user namespace cannot drop them), so the kernel lets it through a directory by the *other* bits,
by a group it is in, or by a POSIX ACL. The policy is built by the owner without naming the
account, so a directory counts as enterable unless shown otherwise: ``o+x``; ``g+x`` when its
group holds any user other than the directory's owner (a user-private group, as on the usual
``0750`` home, does not); ``u+x`` when the owner is neither root nor the operator (it may be the
account); or any POSIX access ACL, or an error reading one. Errs toward masking: an over-mask fails
the run at mount (EPERM), an under-mask leaks the credential.
"""

from __future__ import annotations

import errno
import grp
import os
import pwd
import stat
from pathlib import Path

_ACL = "system.posix_acl_access"


def account_may_traverse(directory: str) -> bool:
    """Every directory from ``/`` down to ``directory`` is a real directory the account may enter.
    ``lstat``: a symlink or a stat error (bar a refusal below an enterable parent) is no."""
    current = ""
    for part in Path(directory).parts:
        current = os.path.join(current, part) if current else part
        try:
            info = os.lstat(current)
        except PermissionError:  # the owner's bits shut it out below an enterable parent: unknown
            return current != part
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode) or not _may_enter(current, info):
            return False
    return True


def _may_enter(path: str, info: os.stat_result) -> bool:
    if info.st_mode & 0o001 or (info.st_mode & 0o100 and info.st_uid not in (0, os.geteuid())):
        return True
    if info.st_mode & 0o010 and _group_has_others(info):
        return True
    return _has_acl(path)


def _group_has_others(info: os.stat_result) -> bool:
    """A user other than the directory's owner is in its group: a listed member, or a user whose
    primary group it is. Unknown group or owner, or an unlistable user table or one lacking the
    owner (NSS/LDAP enumeration off), counts as yes."""
    try:
        members = grp.getgrgid(info.st_gid).gr_mem
        owner = pwd.getpwuid(info.st_uid).pw_name
        users = pwd.getpwall()
    except (KeyError, OSError):
        return True
    if any(member != owner for member in members) or all(u.pw_uid != info.st_uid for u in users):
        return True
    return any(u.pw_gid == info.st_gid and u.pw_uid != info.st_uid for u in users)


def _has_acl(path: str) -> bool:
    """The directory has a POSIX access ACL, or reading its xattrs fails; no xattrs means no ACL."""
    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        return False
    try:
        return _ACL in listxattr(path, follow_symlinks=False)
    except OSError as err:
        return err.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP)
