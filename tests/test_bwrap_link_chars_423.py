"""#423: a credential-link target with non-ASCII letters, parentheses or a colon is no longer a
false refusal; every line-forging character stays refused. Vanished subdirectories are skipped."""

import os
from pathlib import Path

import pytest

from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError

ALLOWED = ["/tmp/文件/a", "/tmp/a(b)", "/tmp/a:b", "/tmp/café", "/tmp/́x", "/tmp/a b"]
# control characters, NUL, every str.splitlines() separator, format/private/space/surrogate
# characters, quotes and backslash, and a non-absolute path
FORGED = [
    "/tmp/a\nb", "/tmp/a\rb", "/tmp/a\x00b", "/tmp/a\x0bb", "/tmp/a\x0cb", "/tmp/a\x1cb",
    "/tmp/a\x1db", "/tmp/a\x1eb", "/tmp/a\x85b", "/tmp/a b", "/tmp/a b",
    "/tmp/a\x7fb", "/tmp/a​b", "/tmp/ab", "/tmp/a\udcffb", "/tmp/a b",
    "/tmp/a　b", '/tmp/a"b', "/tmp/a\\b", "/tmp/a\n", "/tmp/a‮b",
]  # fmt: skip


@pytest.mark.parametrize("path", ALLOWED)
def test_link_target_safe_characters_pass(path):
    assert bwrap._safe_link(path) == os.path.realpath(path)


@pytest.mark.parametrize("path", FORGED)
def test_link_target_line_forging_characters_refused(path):
    with pytest.raises((SandboxError, ValueError)):
        bwrap._safe_link(path)


@pytest.mark.parametrize("path", ["/tmp/a(b", "/tmp/a:b", "/tmp/é"])
def test_roots_and_home_keep_the_strict_check(path):
    with pytest.raises(SandboxError):
        bwrap._safe(path)


def test_policy_for_a_link_into_a_non_ascii_dir_is_one_flag_per_line(tmp_path):
    home = tmp_path / "home"
    target = tmp_path / "文件 (x):y"
    target.mkdir()
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "credentials").symlink_to(target)
    lines = bwrap.BACKEND.policy((), (), home=home).text.split("\n")
    real = os.path.realpath(target)
    assert real in lines
    assert lines[lines.index(real) - 1] == "--tmpfs"


def test_a_vanished_subdirectory_is_skipped_and_counted(tmp_path, monkeypatch):
    base = tmp_path / "cred"
    (base / "gone").mkdir(parents=True)
    (base / "kept").mkdir()
    (base / "kept" / "l").symlink_to("/nowhere")
    real_scandir = os.scandir

    def scandir(path):
        if str(path).endswith("/gone"):
            raise FileNotFoundError(2, "No such file or directory")
        return real_scandir(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    links, unscanned, vanished = bwrap_inner._links_in(str(base))
    assert [Path(p).name for p in links] == ["l"] and unscanned == 0 and vanished == 1


@pytest.mark.parametrize("err", [FileNotFoundError(2, "x"), PermissionError(13, "x")])
def test_eacces_below_and_a_missing_credential_directory_stay_refused(tmp_path, monkeypatch, err):
    base = tmp_path / "cred"
    (base / "sub").mkdir(parents=True)
    orig = os.scandir
    # ENOENT on the base itself, EACCES on a subdirectory: both refuse (fail closed)
    fail_on = str(base) if isinstance(err, FileNotFoundError) else "/sub"

    def scandir(path):
        if str(path).endswith(fail_on):
            raise err
        return orig(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    with pytest.raises(SandboxError):
        bwrap_inner._links_in(str(base))


def _flaky_scandir(monkeypatch, name, method, err):
    """Make one child's DirEntry.<method> raise ``err`` (a racing delete or an unreadable entry)."""
    real = os.scandir

    class Entry:
        def __init__(self, e):
            self._e, self.path = e, e.path

        def is_symlink(self):
            return self._call("is_symlink")

        def is_dir(self, **kw):
            return self._call("is_dir", **kw)

        def _call(self, which, **kw):
            if self._e.name == name and which == method:
                raise err
            return getattr(self._e, which)(**kw)

    class Scan:
        def __init__(self, path):
            self._s = real(path)

        def __enter__(self):
            return (Entry(e) for e in self._s.__enter__())

        def __exit__(self, *a):
            return self._s.__exit__(*a)

    monkeypatch.setattr(bwrap_inner.os, "scandir", Scan)


@pytest.mark.parametrize("method", ["is_dir", "is_symlink"])
@pytest.mark.parametrize("err", [FileNotFoundError(2, "x"), NotADirectoryError(20, "x")])
def test_a_child_vanishing_mid_scan_skips_only_that_child(tmp_path, monkeypatch, method, err):
    base = tmp_path / "cred"
    base.mkdir()
    for n in ("a", "racer", "z"):
        (base / n).symlink_to("/nowhere")
    _flaky_scandir(monkeypatch, "racer", method, err)
    links, _, vanished = bwrap_inner._links_in(str(base))
    assert sorted(Path(p).name for p in links) == ["a", "z"] and vanished == 0


@pytest.mark.parametrize("method", ["is_dir", "is_symlink"])
def test_a_child_with_eacces_still_refuses(tmp_path, monkeypatch, method):
    base = tmp_path / "cred"
    base.mkdir()
    (base / "racer").symlink_to("/nowhere")
    _flaky_scandir(monkeypatch, "racer", method, PermissionError(13, "x"))
    with pytest.raises(SandboxError):
        bwrap_inner._links_in(str(base))


def test_only_opening_current_vanishing_skips_the_directory(tmp_path, monkeypatch):
    base = tmp_path / "cred"
    (base / "gone").mkdir(parents=True)
    (base / "l").symlink_to("/nowhere")
    real = os.scandir

    def scandir(path):
        if str(path).endswith("/gone"):
            raise NotADirectoryError(20, "x")
        return real(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    links, _, vanished = bwrap_inner._links_in(str(base))
    assert [Path(p).name for p in links] == ["l"] and vanished == 1
