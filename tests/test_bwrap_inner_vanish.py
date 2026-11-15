"""A child credential directory that disappears during scanning is skipped and recorded."""

import os

import pytest

from cairnwork_sandbox import bwrap, bwrap_inner
from cairnwork_sandbox.backend import SandboxError


def _fail_open(monkeypatch, target, error):
    """Make ``os.open`` of one path raise ``error``; every other open is real."""
    original = os.open

    def opener(path, *args, **kwargs):
        if os.fspath(path) == str(target):
            raise error
        return original(path, *args, **kwargs)

    monkeypatch.setattr(bwrap_inner.os, "open", opener)


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
    _fail_open(monkeypatch, child, FileNotFoundError(2, "No such file or directory", str(child)))
    policy = bwrap.BACKEND.policy((), (), home=home)
    assert str(outside.resolve()) in policy.text
    assert f"{bwrap_inner.VANISHED_NOTE}1" in policy.notes


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "Permission denied"),
        NotADirectoryError(20, "Not a directory"),
    ],
)
def test_other_errors_on_subdirectory_fail_closed(tmp_path, monkeypatch, error):
    home = tmp_path / "home"
    child = home / ".aws" / "child"
    child.mkdir(parents=True)
    _fail_open(monkeypatch, child, error)
    with pytest.raises(SandboxError, match="cannot scan"):
        bwrap.BACKEND.policy((), (), home=home)


def test_missing_scan_base_remains_an_error(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(SandboxError, match="cannot scan"):
        bwrap_inner._links_in(str(missing))


def test_subdirectory_swapped_for_a_link_is_masked(tmp_path, monkeypatch):
    """The directory is replaced by a link between listing and opening: it is caught as a link."""
    home = tmp_path / "home"
    cred = home / ".aws"
    child = cred / "child"
    outside = tmp_path / "secret"
    cred.mkdir(parents=True)
    child.mkdir()
    outside.mkdir()
    original = os.open

    def opener(path, *args, **kwargs):
        if os.fspath(path) == str(child):
            child.rmdir()
            child.symlink_to(outside)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(bwrap_inner.os, "open", opener)
    links, _, _ = bwrap_inner._links_in(str(cred))
    assert links == [str(child)]
