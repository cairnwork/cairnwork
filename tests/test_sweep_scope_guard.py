"""A sweep scoped to "/" or another broad directory kills nothing and fails closed (#1849).

A test once passed cwd "/" to ``confined.execute``; the cwd sweep then SIGKILLed every
env-withheld desktop process of the user sitting in "/". ``os.kill``/``os.killpg`` are
recorders in every test here, so nothing on the host is ever signalled."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest

from cairnwork import confined, proc_cwd


@pytest.fixture(autouse=True)
def kills(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(os, "kill", lambda *a: calls.append(("kill", a)))
    monkeypatch.setattr(os, "killpg", lambda *a: calls.append(("killpg", a)))
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    return calls


def _fake_reads(monkeypatch, cwd: str) -> None:
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"t")
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: True)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: cwd)


HOME = os.path.realpath(os.path.expanduser("~"))


@pytest.mark.parametrize(
    "dirs", [("/",), (HOME,), ("/Users",), ("/private/tmp",), ("/tmp",), "/x/y/z", b"/x/y/z"]
)
def test_a_broad_or_bare_string_scope_raises_and_kills_nothing(monkeypatch, kills, dirs):
    _fake_reads(monkeypatch, "/")
    rows = [(4242, 4242, os.getuid(), {})]
    with pytest.raises(ValueError):
        proc_cwd.kill_withheld_in(rows, dirs, os.getuid())
    assert kills == []


def test_one_broad_top_among_narrow_ones_still_raises(monkeypatch, kills, tmp_path):
    _fake_reads(monkeypatch, "/")
    deep = os.path.realpath(tmp_path)
    with pytest.raises(ValueError):
        proc_cwd.kill_withheld_in([(1, 1, os.getuid(), {})], (deep, "/"), os.getuid())
    assert kills == []


def test_a_deep_run_directory_still_kills_only_the_row_inside_it(monkeypatch, kills, tmp_path):
    deep = os.path.realpath(tmp_path / "a1")
    os.mkdir(deep)
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"t")
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: True)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: deep if pid == 7 else "/")
    rows = [(7, 7, os.getuid(), {}), (8, 8, os.getuid(), {})]
    killed, denied, vanished = proc_cwd.kill_withheld_in(rows, (deep,), os.getuid())
    assert (killed, denied, vanished) == ([7], [], 0)
    assert [c[1][0] for c in kills] == [7]


@pytest.mark.parametrize("cwd", ["/", HOME, "/private/tmp", "/Users"])
def test_execute_refuses_a_broad_cwd_before_starting(monkeypatch, kills, tmp_path, cwd):
    def no_popen(*a, **k):
        pytest.fail("Popen must not be called for a broad cwd")

    monkeypatch.setattr(subprocess, "Popen", no_popen)
    env = {confined.RUN_TOKEN_VAR: "t"}
    with pytest.raises(ValueError):
        confined.execute(["true"], cwd, env, 5, tmp_path / "log")
    assert kills == []
    assert not (tmp_path / "log").exists()


def test_too_broad_draws_the_line_at_three_components_and_home():
    assert proc_cwd.too_broad("/")
    assert proc_cwd.too_broad("/private/tmp")
    assert proc_cwd.too_broad(HOME)
    assert not proc_cwd.too_broad("/private/tmp/x")
    assert not proc_cwd.too_broad(os.path.join(HOME, "workspace", "x"))
