"""A config include inside an included file is checked too (Issue #1210). Real git, real files."""

import subprocess

import pytest

from scripts import env_smoke
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


def test_include_chain_deeper_than_git_allows_is_refused(mirror, tmp_path):
    files = [tmp_path / f"c{i}.cfg" for i in range(14)]
    for here, nxt in zip(files, files[1:], strict=False):
        here.write_text(f"[include]\n\tpath = {nxt}\n")
    files[-1].write_text("[user]\n\tname = y\n")
    _g(mirror, "config", "include.path", str(files[0]))
    assert _include_problem(mirror) is not None


def test_cycle_is_refused_by_the_walk_itself(mirror, tmp_path, monkeypatch):
    """Real git fails on a cycle before the walk sees it; fake the listing so the walk's own
    guard must refuse (a skipped cycle would read as no problem)."""
    a = tmp_path / "a.cfg"
    a.write_text("[user]\n\tname = y\n")

    def fake(repo, *args):
        out = f"file:{a}\0include.path\n{a}\0"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(env_smoke, "_run_git", fake)
    monkeypatch.setattr(env_smoke, "_include_applies", lambda *a, **k: True)
    assert "cycle" in (_include_problem(mirror) or "")


def test_nested_missing_under_relative_repo_path_is_refused(mirror, tmp_path, monkeypatch):
    (tmp_path / "mid.cfg").write_text(f"[include]\n\tpath = {tmp_path / 'gone.cfg'}\n")
    git_cfg = mirror / ".git" / "config"
    git_cfg.write_text(git_cfg.read_text() + "[include]\n\tpath = ../../mid.cfg\n")
    monkeypatch.chdir(mirror.parent)
    assert _include_problem(type(mirror)(mirror.name)) is not None


def test_nested_nonmatching_includeif_with_missing_target_passes(mirror, tmp_path):
    mid = tmp_path / "mid.cfg"
    mid.write_text(f'[includeIf "gitdir:/nonexistent/"]\n\tpath = {tmp_path / "gone.cfg"}\n')
    _g(mirror, "config", "include.path", str(mid))
    assert _include_problem(mirror) is None
