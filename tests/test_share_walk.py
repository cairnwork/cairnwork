"""#240: SHARE_HELPER (account_checkout.py) fails closed where the comparing owner could read what
the checkout's owner cannot, run here as the owner on its own files.

A directory its user may list and search but the helper cannot walk (a macOS ACL denying the
account alone, or any open/list/stat failure) and a chmod that fails make it exit 1, so the run
fails ("checkout not shared with the owner"); the checkout's top directory is shared like any
other. A macOS extended ACL anywhere the owner can reach (one can grant the owner a directory whose
mode closes it to its user) fails the run too.
"""

import ctypes
import os
import shutil
import stat
import subprocess
import sys

import pytest
from test_account_checkout import ME, _differences, _LocalAccount, _share

import cairnwork.account_checkout as module

DARWIN = sys.platform == "darwin"
ACL_LIST, ACL_SEARCH = 1 << 1, 1 << 3  # ACL_LIST_DIRECTORY, ACL_SEARCH (sys/acl.h)


def _acl(path, perms, tag=2) -> None:
    """Add an ACL entry (2: deny, 1: allow) for ``perms`` to the running uid alone: what a
    candidate can do to its own copy from inside the sandbox, for itself or for the owner."""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    uuid = (ctypes.c_ubyte * 16)()
    assert lib.mbr_uid_to_uuid(ctypes.c_uint(os.getuid()), uuid) == 0
    lib.acl_init.restype = ctypes.c_void_p
    acl, entry, permset = ctypes.c_void_p(lib.acl_init(1)), ctypes.c_void_p(), ctypes.c_void_p()
    assert lib.acl_create_entry(ctypes.byref(acl), ctypes.byref(entry)) == 0
    assert lib.acl_set_tag_type(entry, tag) == 0
    assert lib.acl_set_qualifier(entry, uuid) == 0
    assert lib.acl_get_permset(entry, ctypes.byref(permset)) == 0
    for perm in perms:
        lib.acl_add_perm(permset, perm)
    assert lib.acl_set_permset(entry, permset) == 0
    assert lib.acl_set_file(str(path).encode(), 0x100, acl) == 0, ctypes.get_errno()


def _tree(tmp_path):
    root = tmp_path / "co"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "x.txt").write_text("x")
    os.chmod(root / "sub" / "x.txt", 0o044)  # closed to its owner, open to group and other
    return root


@pytest.mark.skipif(not DARWIN, reason="macOS extended ACLs")
@pytest.mark.parametrize(
    ("perms", "problem"),
    [((ACL_LIST, ACL_SEARCH), "cannot open sub"), ((ACL_SEARCH,), "cannot stat")],
)
def test_a_directory_denied_to_the_account_alone_fails_the_share(tmp_path, perms, problem):
    """The directory's mode lets its user list and search it; an ACL denies that to the account
    alone. The helper cannot narrow what is inside, which the comparing owner could read through
    group and other: it must exit 1, not skip the directory."""
    root = _tree(tmp_path)
    _acl(root / "sub", perms)
    try:
        done = _share(root)
    finally:
        subprocess.run(["/bin/chmod", "-N", str(root / "sub")], check=True)  # noqa: S603
    assert done.returncode == 1 and problem in done.stderr, done.stderr


def test_a_directory_that_cannot_be_opened_fails_the_share(tmp_path):
    """Any platform: an open/list failure on a directory open to its user is a problem."""
    root = _tree(tmp_path)
    deny = (
        "import os\n_open = os.open\n"
        "def _denying(path, *args, **kwargs):\n"
        "    if path == 'sub':\n"
        "        raise PermissionError(13, 'denied', path)\n"
        "    return _open(path, *args, **kwargs)\n"
        "os.open = _denying\n"
    )
    done = _share(root, prefix=deny)
    assert done.returncode == 1 and "cannot open sub" in done.stderr, done.stderr
    assert stat.S_IMODE(os.lstat(root / "sub" / "x.txt").st_mode) == 0o044


def test_a_directory_closed_to_its_user_is_left_closed_without_error(tmp_path):
    """No r or x for its user: the comparing owner gets the same bits and cannot read it."""
    root = _tree(tmp_path)
    os.chmod(root / "sub", 0o055)
    try:
        done = _share(root)
        mode = stat.S_IMODE(os.lstat(root / "sub").st_mode)
    finally:
        os.chmod(root / "sub", 0o755)
    assert done.returncode == 0, done.stderr
    assert mode == 0


@pytest.mark.skipif(not hasattr(os, "chflags"), reason="needs chflags (UF_IMMUTABLE)")
def test_a_failing_chmod_fails_the_share(tmp_path):
    root = _tree(tmp_path)
    os.chflags(root / "sub" / "x.txt", stat.UF_IMMUTABLE)
    try:
        done = _share(root)
    finally:
        os.chflags(root / "sub" / "x.txt", 0)
    assert done.returncode == 1 and "chmod sub/x.txt" in done.stderr, done.stderr


@pytest.mark.parametrize(("before", "after"), [(0o700, 0o755), (0o750, 0o755), (0o055, 0)])
def test_the_top_directory_is_shared_too(tmp_path, before, after):
    root = tmp_path / "co"
    root.mkdir()
    os.chmod(root, before)
    try:
        done = _share(root)
        mode = stat.S_IMODE(os.lstat(root).st_mode)
    finally:
        os.chmod(root, 0o755)
        shutil.rmtree(root)
    assert done.returncode == 0, done.stderr
    assert mode == after


def test_a_directory_that_cannot_be_listed_fails_the_share(tmp_path):
    root = _tree(tmp_path)
    deny = (
        "import os\n_listdir, CALLS = os.listdir, []\n"
        "def _denying(path):\n"
        "    CALLS.append(path)\n"
        "    if len(CALLS) == 2:  # the top first, then sub\n"
        "        raise PermissionError(13, 'denied')\n"
        "    return _listdir(path)\n"
        "os.listdir = _denying\n"
    )
    done = _share(root, prefix=deny)
    assert done.returncode == 1 and "cannot list sub" in done.stderr, done.stderr


def test_a_directory_its_user_may_list_but_not_search_is_shared_not_entered(tmp_path):
    root = _tree(tmp_path)
    os.chmod(root / "sub", 0o400)
    try:
        done = _share(root)
        mode = stat.S_IMODE(os.lstat(root / "sub").st_mode)
    finally:
        os.chmod(root / "sub", 0o755)
    assert done.returncode == 0, done.stderr
    assert mode == 0o444
    assert stat.S_IMODE(os.lstat(root / "sub" / "x.txt").st_mode) == 0o044


@pytest.mark.skipif(not DARWIN, reason="macOS extended ACLs")
def test_an_extended_acl_the_owner_can_reach_is_found(tmp_path):
    """A directory closed to its user by mode (0o100) with an ACL allowing the owner to list it:
    the helper leaves it closed, the owner would list it and read 0o044 files inside."""
    root = _tree(tmp_path)
    (root / ".git").mkdir()
    (root / ".git" / "f").write_text("g")
    assert module._extended_acls(root) == []
    _acl(root / ".git" / "f", (1 << 1,), tag=1)  # the top-level .git is not compared
    _acl(root / "sub", (1 << 1,), tag=1)  # ACL_EXTENDED_ALLOW, ACL_LIST_DIRECTORY
    os.chmod(root / "sub", 0o100)
    try:
        found = module._extended_acls(root)
    finally:
        os.chmod(root / "sub", 0o755)
        subprocess.run(["/bin/chmod", "-R", "-N", str(root)], check=True)  # noqa: S603
    assert found == ["sub"]


def test_an_extended_acl_fails_the_run(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_extended_acls", lambda _checkout: ["pkg/sub"])
    found, ran = _differences(
        monkeypatch,
        tmp_path,
        _LocalAccount(ME, tmp_path),
        lambda: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert len(ran) == 1
    assert found == ("checkout not shared with the owner: extended ACL on pkg/sub",)


@pytest.mark.skipif(not DARWIN, reason="macOS extended ACLs")
def test_an_acl_hidden_from_the_owner_and_one_on_the_top_directory_are_found(tmp_path):
    """An ACL entry denying the owner read-security hides the ACL itself: "cannot tell" counts as
    found. The checkout's top directory is checked too."""
    root = _tree(tmp_path)
    _acl(root / "sub", (1 << 11,))  # deny ACL_READ_SECURITY to the running uid
    _acl(root, (1 << 1,), tag=1)
    try:
        found = module._extended_acls(root)
    finally:
        subprocess.run(["/bin/chmod", "-R", "-N", str(root)], check=True)  # noqa: S603
    assert found == [".", "sub"]
