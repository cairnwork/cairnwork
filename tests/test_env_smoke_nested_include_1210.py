"""A config include inside an included file is checked too (Issue #1210). Real git, real files."""

import subprocess

import pytest

from scripts.env_smoke import _include_problem


def _g(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def mirror(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return repo


def test_nested_include_with_missing_target_is_refused(mirror, tmp_path):
    mid = tmp_path / "mid.cfg"
    mid.write_text(f"[include]\n\tpath = {tmp_path / 'gone.cfg'}\n")
    _g(mirror, "config", "include.path", str(mid))
    assert _include_problem(mirror) is not None


def test_nested_relative_include_with_missing_target_is_refused(mirror, tmp_path):
    mid = tmp_path / "mid.cfg"
    mid.write_text("[include]\n\tpath = gone.cfg\n")
    _g(mirror, "config", "include.path", str(mid))
    assert _include_problem(mirror) is not None


def test_nested_include_with_present_target_passes(mirror, tmp_path):
    (tmp_path / "leaf.cfg").write_text("[user]\n\tname = y\n")
    mid = tmp_path / "mid.cfg"
    mid.write_text(f"[include]\n\tpath = {tmp_path / 'leaf.cfg'}\n")
    _g(mirror, "config", "include.path", str(mid))
    assert _include_problem(mirror) is None


def test_include_cycle_terminates_and_is_refused(mirror, tmp_path):
    a, b = tmp_path / "a.cfg", tmp_path / "b.cfg"
    a.write_text(f"[include]\n\tpath = {b}\n")
    b.write_text(f"[include]\n\tpath = {a}\n")
    _g(mirror, "config", "include.path", str(a))
    assert _include_problem(mirror) is not None  # git itself fails on a cycle: fail closed
