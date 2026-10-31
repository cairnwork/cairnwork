"""Owner, extended attributes and ACL of one checkout entry, for the AC8 digest (#476 step 3b).

Read by path, never following a final symlink. Linux: os.listxattr and os.getxattr (POSIX ACLs
are the system.posix_acl_* attributes). macOS: libc's listxattr and getxattr with XATTR_NOFOLLOW,
and the extended ACL through acl_get_link_np and acl_to_text. A platform without them, or any
error but "this file system keeps no attributes", raises GuardUnavailable."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import sys
from dataclasses import dataclass

from experimental.pause.interfaces import GuardUnavailable

_NO_ATTRS = (errno.ENOTSUP, errno.EOPNOTSUPP)  # the file system keeps no extended attributes


@dataclass(frozen=True)
class Meta:
    uid: int
    gid: int
    xattrs: tuple[tuple[bytes, bytes], ...]  # (name, value), sorted by name
    acl: bytes  # the extended ACL as text (macOS); b"" when there is none

    def key(self) -> bytes:
        h = hashlib.sha256()
        for name, value in self.xattrs:
            h.update(b"%d:%s%d:%s" % (len(name), name, len(value), value))
        acl = hashlib.sha256(self.acl).hexdigest().encode()
        return b"%d:%d:%s:%s" % (self.uid, self.gid, h.hexdigest().encode(), acl)

    def size(self) -> int:
        return sum(len(n) + len(v) for n, v in self.xattrs) + len(self.acl)


if sys.platform == "darwin":
    _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    _NOFOLLOW, _ACL_EXTENDED = 0x0001, 0x00000100
    _P, _S, _I, _V = ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p
    for _fn, _args, _res in (
        ("listxattr", [_P, _P, _S, _I], ctypes.c_ssize_t),
        ("getxattr", [_P, _P, _P, _S, ctypes.c_uint32, _I], ctypes.c_ssize_t),
        ("acl_get_link_np", [_P, _I], _V),
        ("acl_to_text", [_V, _V], _V),
        ("acl_free", [_V], _I),
    ):
        getattr(_libc, _fn).argtypes, getattr(_libc, _fn).restype = _args, _res

    def _ok(rc: int | None, path: bytes) -> int:
        if rc is None or rc < 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e), os.fsdecode(path))
        return rc

    def _sized(path: bytes, call) -> bytes:
        size = _ok(call(None, 0), path)
        buf = ctypes.create_string_buffer(size)
        got = _ok(call(buf, size), path)  # grew in between: ERANGE, fail closed
        return buf.raw[:got]

    def _names(path: bytes) -> list[bytes]:
        raw = _sized(path, lambda b, n: _libc.listxattr(path, b, n, _NOFOLLOW))
        return [n for n in raw.split(b"\0") if n]

    def _get(path: bytes, name: bytes) -> bytes:
        return _sized(path, lambda b, n: _libc.getxattr(path, name, b, n, 0, _NOFOLLOW))

    def _acl_get(path: bytes) -> bytes:
        acl = _libc.acl_get_link_np(path, _ACL_EXTENDED)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:  # the entry exists (just stat-ed): no ACL
                return b""
            _ok(-1, path)
        try:
            text = _libc.acl_to_text(acl, None)
            if not text:
                _ok(-1, path)
            try:
                return ctypes.string_at(text)
            finally:
                _libc.acl_free(text)
        finally:
            _libc.acl_free(acl)

else:  # Linux; where os has no listxattr, the first read fails closed

    def _names(path: bytes) -> list[bytes]:
        if not hasattr(os, "listxattr"):
            raise GuardUnavailable(f"cannot read extended attributes on {sys.platform}")
        return [os.fsencode(n) for n in os.listxattr(path, follow_symlinks=False)]

    def _get(path: bytes, name: bytes) -> bytes:
        return os.getxattr(path, name, follow_symlinks=False)

    def _acl_get(path: bytes) -> bytes:
        return b""  # POSIX ACLs are the system.posix_acl_* attributes, read above


def read(path: bytes, st: os.stat_result) -> Meta:
    try:
        try:
            xattrs = tuple(sorted((n, _get(path, n)) for n in _names(path)))
        except OSError as e:
            if e.errno not in _NO_ATTRS:
                raise
            xattrs = ()
        acl = _acl_get(path)
    except OSError as e:
        raise GuardUnavailable(f"attributes of {os.fsdecode(path)!r}: {e.strerror}") from e
    return Meta(st.st_uid, st.st_gid, xattrs, acl)
