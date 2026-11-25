"""#1823: a regular file closed to its owner is opened (u+r) before the walk reads it; one that
stays unopenable is an error row. In process: bytecode_walk.opened reads each entry itself."""

import os
from typing import Any

import pytest

from cairnwork import bytecode_walk

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="chmod 0 does not close a file to root")


def _entries(path) -> dict[str, tuple[Any, ...]]:
    found = bytecode_walk.opened(str(path))[1]
    return {name: (kind, error) for name, kind, _ident, error in found}


def test_an_honest_suite_leaving_a_closed_file_still_passes(tmp_path):
    closed = tmp_path / "closed"
    closed.write_text("x = 1\n")
    closed.chmod(0)
    assert _entries(tmp_path)["closed"] == ("other", None)
    assert os.access(closed, os.R_OK)  # left readable by its owner


def test_a_closed_file_that_stays_unopenable_is_an_error(tmp_path, monkeypatch):
    closed = tmp_path / "closed"
    closed.write_text("x = 1\n")
    closed.chmod(0)
    real = os.open

    def deny(name, flags, *args, **kwargs):
        if name == "closed" and "dir_fd" in kwargs:
            raise PermissionError(13, "Permission denied", name)
        return real(name, flags, *args, **kwargs)

    def no_chmod(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "open", deny)
    monkeypatch.setattr(os, "chmod", no_chmod)
    _kind, error = _entries(tmp_path)["closed"]
    assert error is not None
