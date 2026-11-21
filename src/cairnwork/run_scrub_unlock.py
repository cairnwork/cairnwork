"""Bounded, no-follow walker used by the verifier account to unlock its run tree (#437)."""

from __future__ import annotations

# Executed by the verifier's isolated Python as the account. Keep this self-contained: it is
# passed through sudo as one command and must not import code from the candidate tree.
# One pre-order pass. Per entry, before relying on lstat: clear its ACL (setattrlist with
# FSOPT_NOFOLLOW: the entry itself, a symlink too, never a target; no open, so a denied read or
# readsecurity does not stop it) and uchg/uappnd (the chflags syscall needs no stat). A directory
# on run's device gets u+rwx and is entered by an O_NOFOLLOW fd, closed once listed (no PATH_MAX).
# No depth limit: a listing keeps at most half the entries left and reads for at most half the
# time left, so what it read is still unlocked, and the next listing gets some of both. A
# cut-short directory once done, or the current one at the clock (checked before each entry), is
# moved up into run unless it is right under run already: rm frees the path above it and the next
# scrub goes on, not over. Whatever the clock says, a pass does one unit of work first: unlock a
# non-directory, finish a directory with nothing in it cut short or failed (rm can then remove
# it), or move one up. So past the clock it does at most that one unit, or tries one entry that
# fails (what fails every pass is item G: root only); a listing it reads then is cut at one name.
# A failure before the deadline now counts as work too: after the clock, the next entry stops the
# pass. Failure to clear an entry's lock also counts as failure. With the entry budget spent no
# directory is opened again: a deep one is moved up instead. Exit 1 if anything is left (cut
# short, an entry, listing or move that failed, another device, the clock): rm fails, the scrub
# refuses.
UNLOCK_SOURCE = """\
import ctypes, itertools, os, stat, struct, sys, time
top, max_nodes, seconds = sys.argv[1], *map(int, sys.argv[2:4])
deadline, listed, cut, done, stack = time.monotonic() + seconds, 0, False, False, []
failed = 0
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
        return False
    return True
def fail():
    global cut, failed
    cut, failed = True, failed + 1
def up(name):
    global done
    if attempt(os.rename, name, os.path.join(top, ".unlock-" + os.urandom(8).hex())):
        done = True
    else:
        fail()
def leave(cut_short):
    _names, _short, here, _failed = stack.pop()
    if stack:
        os.chdir("..")
        if cut_short and len(stack) > 1:
            up(here)
def stop():
    if stack:
        leave(True)
    sys.exit("unlock: limit reached, the rest is left in place")
def late():
    return time.monotonic() >= deadline and (done or failed > 0 or len(stack) > 2)
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
        if not attempt(os.lchflags, name, info.st_flags & ~LOCKS):
            return None
        no_acl(name)
    return info
def opened(name, dev):
    global listed, cut
    fd = os.open(name, DIR)
    try:
        if os.fstat(fd).st_dev != dev:
            raise OSError("another device: left in place")
        keep, names, short = (max_nodes - listed + 1) // 2, [], False
        until = (time.monotonic() + deadline) / 2
        with os.scandir(fd) as entries:
            for entry in itertools.islice(entries, keep + 1):
                names.append(entry.name)
                if time.monotonic() >= until:
                    short = True
                    break
        os.fchdir(fd)
    finally:
        os.close(fd)
    short = short or len(names) > keep
    cut, listed = cut or short, listed + min(len(names), keep)
    return iter(names[:keep]), short, name, failed
dev = os.stat(top, follow_symlinks=False).st_dev
stack.append(opened(top, dev))
while stack:
    name = next(stack[-1][0], None)
    if name is None:
        done = done or (not stack[-1][1] and stack[-1][3] == failed)
        leave(stack[-1][1])
        continue
    if late():
        stop()
    info = unlock(name)
    if info is None or info.st_dev != dev:
        fail()
        continue
    if not stat.S_ISDIR(info.st_mode):
        done = True
        continue
    attempt(os.chmod, name, stat.S_IMODE(info.st_mode) | 0o700, follow_symlinks=False)
    if listed >= max_nodes:
        cut = True
        if len(stack) > 1:
            up(name)
        continue
    try:
        stack.append(opened(name, dev))
    except OSError:
        fail()
if cut:
    stop()
"""

UNLOCK = f"exec({UNLOCK_SOURCE!r})"
