"""A child credential directory that disappears during scanning is skipped and recorded."""

import os

import pytest

from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError


def test_vanished_subdirectory_is_counted_and_other_links_are_found(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cred = home / ".aws"
    child = cred / "child"
    outside = tmp_path / "secret"
    cred.mkdir(parents=True)
    child.mkdir()
    outside.write_text("secret")
    (child / "link").symlink_to(outside)
    (cred / "other").symlink_to(outside)
    original = os.scandir

    def scandir(path):
        if os.fspath(path) == str(child):
            raise FileNotFoundError(2, "No such file or directory", str(child))
        return original(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    policy = bwrap.BACKEND.policy((), (), home=home)
    assert str(outside.resolve()) in policy.text
    assert f"{bwrap_inner.VANISHED_NOTE}1" in policy.notes


def test_eacces_on_subdirectory_still_fails_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    child = home / ".aws" / "child"
    child.mkdir(parents=True)
    original = os.scandir

    def scandir(path):
        if os.fspath(path) == str(child):
            raise PermissionError(13, "Permission denied", str(child))
        return original(path)

    monkeypatch.setattr(bwrap_inner.os, "scandir", scandir)
    with pytest.raises(SandboxError, match="cannot scan"):
        bwrap.BACKEND.policy((), (), home=home)


def test_missing_scan_base_remains_an_error(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(SandboxError, match="cannot scan"):
        bwrap_inner._links_in(str(missing))
