"""#423: a credential-link target with non-ASCII letters, parentheses or a colon is no longer a
false refusal; every line-forging character stays refused. A directory renamed mid-scan
restarts the scan (#1354): its link is found at the new place or the run is refused."""

import os
import shutil
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


def test_credential_path_itself_keeps_the_strict_check(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ssh").symlink_to(_outside(tmp_path, "文件"))
    with pytest.raises(SandboxError):
        bwrap.BACKEND.policy((), (), home=home)


def _outside(tmp_path, name):
    target = tmp_path / name
    target.mkdir(exist_ok=True)
    (target / "secret").write_text("x")
    return target


def _hook_scandir(monkeypatch, victim, action):
    """Run ``action`` right before ``victim`` is opened for scanning (a race at that moment)."""
    real = os.scandir

    def scandir(path):
        if str(path).endswith(victim):
            action(str(path))
        return real(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)


def _home(tmp_path, depth):
    outside = _outside(tmp_path, "outside")
    home = tmp_path / "home"
    leaf = home / ".aws" / "/".join(["a"] * depth)
    leaf.mkdir(parents=True)
    (leaf / "l").symlink_to(outside / "secret")
    return home


def _rename_to_dir(path):
    os.rename(path, path + "2")


def _rename_to_file(path):
    os.rename(path, path + "2")
    Path(path).write_text("x")


@pytest.mark.parametrize(
    ("depth", "action", "victim"),
    [
        (1, _rename_to_dir, "/a"),
        (1, _rename_to_file, "/a"),
        (2, _rename_to_dir, "/a"),
    ],
    ids=["renamed", "renamed-and-replaced-by-file", "renamed-above-depth-2"],
)
def test_renamed_credential_subtree_never_leaves_target_unmasked(
    tmp_path, monkeypatch, depth, action, victim
):
    home = _home(tmp_path, depth)
    if depth == 2:
        # the rename happens when "a/a" is about to be scanned: "a" is moved away first
        victim = "/a/a"
        action = lambda p: os.rename(p[: -len("/a")], p[: -len("/a")] + "2")  # noqa: E731
    _hook_scandir(monkeypatch, victim, action)
    try:  # the scan restarts and finds the moved link (#1354), or refuses: never unmasked
        text = bwrap.BACKEND.policy((), (), home=home).text
        assert os.path.realpath(tmp_path / "outside" / "secret") in text
    except SandboxError:
        pass


def test_a_credential_subdirectory_vanishing_with_its_link_may_succeed(tmp_path, monkeypatch):
    home = _home(tmp_path, 1)
    _hook_scandir(monkeypatch, "/a", lambda p: shutil.rmtree(p))
    bwrap.BACKEND.policy((), (), home=home)  # the link is gone with it: nothing left to mask


@pytest.mark.parametrize(
    "err", [FileNotFoundError(2, "x"), NotADirectoryError(20, "x"), PermissionError(13, "x")]
)
def test_links_in_refuses_when_a_subdirectory_cannot_be_opened(tmp_path, monkeypatch, err):
    base = tmp_path / "cred"
    (base / "sub").mkdir(parents=True)
    orig = os.scandir

    def scandir(path):
        if str(path).endswith("/sub"):
            raise err
        return orig(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    with pytest.raises(SandboxError):
        bwrap_inner._links_in(str(base))


@pytest.mark.parametrize("err", [FileNotFoundError(2, "x"), PermissionError(13, "x")])
def test_a_missing_credential_directory_scan_stays_refused(tmp_path, monkeypatch, err):
    base = tmp_path / "cred"
    base.mkdir()
    orig = os.scandir

    def scandir(path):
        if str(path) == str(base):
            raise err
        return orig(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    with pytest.raises(SandboxError):
        bwrap_inner._links_in(str(base))
