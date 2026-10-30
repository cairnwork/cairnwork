"""The private, read-only /run of a bwrap run (#391), as policy text: on any host, with the trees
and /etc pointed at directories under tmp_path. The real mounts are witnessed on Linux CI by
tests/test_run_isolation_witness.py (no host socket under /run connectable, and a run still
works)."""

import os
from pathlib import Path

import pytest

from cairnwork_sandbox import bwrap, bwrap_private
from cairnwork_sandbox.backend import SandboxError


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake /run, /var/run (a link to it) and /etc, plus a home; the real ones are not read."""
    run, etc, home = tmp_path / "run", tmp_path / "etc", tmp_path / "home"
    for directory in (run, etc, home):
        directory.mkdir()
    (tmp_path / "varrun").symlink_to(run)
    trees = (str(run), str(tmp_path / "varrun"))
    monkeypatch.setattr(bwrap_private, "TREES", trees)
    monkeypatch.setattr(bwrap_private, "ETC", str(etc))
    return tmp_path


def _real(path: Path) -> str:
    return os.path.realpath(path)


def _policy(host: Path, reads=(), writes=(), owner=True) -> list[str]:
    policy = bwrap.BACKEND.policy(reads, writes, home=host / "home", mask_home_credentials=owner)
    return policy.text.splitlines()


def _at(lines: list[str], flag: str, *args: str) -> int:
    """Index of ``flag`` followed by ``args``; -1 when absent."""
    want = [flag, *args]
    for i in range(len(lines) - len(want) + 1):
        if lines[i : i + len(want)] == want:
            return i
    return -1


def test_the_tree_is_a_tmpfs_before_every_bind_and_read_only_last(host):
    work = host / "work"
    work.mkdir()
    (host / "home" / ".netrc").write_text("x\n")
    lines = _policy(host, writes=(work,))
    run = _real(host / "run")
    tmpfs = _at(lines, "--tmpfs", run)
    assert tmpfs > _at(lines, "--ro-bind", "/", "/") >= 0
    assert tmpfs < _at(lines, "--bind", _real(work), _real(work))
    assert tmpfs < _at(lines, "--ro-bind", "/dev/null", f"{_real(host / 'home')}/.netrc")
    assert lines[-2:] == ["--remount-ro", run]
    assert lines.count(run) == 2  # /var/run is a link to /run: one tree, mounted once


def test_a_separate_var_run_gets_its_own_tmpfs(host, monkeypatch):
    (host / "varrun").unlink()
    (host / "varrun").mkdir()
    lines = _policy(host)
    run, varrun = _real(host / "run"), _real(host / "varrun")
    assert _at(lines, "--tmpfs", run) >= 0 and _at(lines, "--tmpfs", varrun) >= 0
    assert lines[-4:] == ["--remount-ro", run, "--remount-ro", varrun]


def test_an_absent_tree_adds_nothing(host, monkeypatch):
    monkeypatch.setattr(bwrap_private, "TREES", (str(host / "absent"),))
    lines = _policy(host)
    assert "--remount-ro" not in lines and str(host / "absent") not in "\n".join(lines)


def test_roots_under_the_tree_are_put_back_and_others_are_not(host):
    run = host / "run"
    read, write, outside = run / "py", run / "w" / "work", host / "lib"
    for directory in (read, write, outside):
        directory.mkdir(parents=True)
    lines = _policy(host, reads=(read, outside, run, host), writes=(write,))
    read_bind = _at(lines, "--ro-bind", _real(read), _real(read))
    write_bind = _at(lines, "--bind", _real(write), _real(write))
    assert _at(lines, "--tmpfs", _real(run)) < read_bind < write_bind
    assert _real(outside) not in lines  # outside the tree: the root bind already gives it
    # a read root that is the tree or holds it would bring the host tree back
    assert _at(lines, "--ro-bind", _real(run), _real(run)) == -1
    assert _at(lines, "--ro-bind", _real(host), _real(host)) == -1


def test_top_level_links_are_recreated_and_nothing_else_is(host):
    run = host / "run"
    (run / "current-system").symlink_to("/nix/store/abc-system")
    (run / "shm").symlink_to("../dev/shm")
    (run / "evil").symlink_to("x\n--bind\n/\n/")  # cannot add a policy line
    (run / "dash").symlink_to("--bind")
    (run / "plain").write_text("x")
    (run / "dir").mkdir()
    (run / "dir" / "link").symlink_to("/nowhere")  # not top level
    os.mkfifo(run / "s")  # stands in for a socket: anything not a link is left out alike
    lines = _policy(host)
    real = _real(run)
    links = [lines[i + 1 : i + 3] for i, flag in enumerate(lines) if flag == "--symlink"]
    assert links == [
        ["/nix/store/abc-system", f"{real}/current-system"],
        ["../dev/shm", f"{real}/shm"],
    ]
    assert not [line for line in lines if line.startswith(f"{real}/") and "/dir" in line]


def test_etc_links_to_a_file_under_the_tree_are_bound_read_only(host):
    run, etc = host / "run", host / "etc"
    (run / "resolve").mkdir()
    (run / "resolve" / "stub-resolv.conf").write_text("nameserver 127.0.0.53\n")
    (run / "resolve" / "dir").mkdir()
    (host / "elsewhere").write_text("x\n")
    (etc / "resolv.conf").symlink_to(run / "resolve" / "stub-resolv.conf")
    (etc / "again.conf").symlink_to(run / "resolve" / "stub-resolv.conf")  # bound once
    (etc / "dirlink").symlink_to(run / "resolve" / "dir")  # a directory: nothing
    (etc / "out.conf").symlink_to(host / "elsewhere")  # not under the tree: nothing
    (etc / "gone.conf").symlink_to(run / "gone")  # dangling: nothing
    lines = _policy(host)
    stub = _real(run / "resolve" / "stub-resolv.conf")
    assert _at(lines, "--ro-bind", stub, stub) > _at(lines, "--tmpfs", _real(run))
    assert lines.count(stub) == 2
    assert _real(run / "resolve" / "dir") not in lines
    assert _real(host / "elsewhere") not in lines


def test_an_etc_file_the_account_cannot_reach_is_left_out(host, monkeypatch):
    run = host / "run"
    (run / "secret").mkdir()
    (run / "secret" / "f.conf").write_text("x\n")
    (host / "etc" / "f.conf").symlink_to(run / "secret" / "f.conf")
    seen = []
    monkeypatch.setattr(bwrap_private, "account_may_traverse", lambda d: seen.append(d) or False)
    target = _real(run / "secret" / "f.conf")
    assert target not in _policy(host, owner=False)
    assert seen == [os.path.dirname(target)]
    assert target in _policy(host, owner=True)  # the owner resolved it: bound


@pytest.mark.parametrize("which", ["run", "etc"])
def test_a_tree_or_etc_that_cannot_be_listed_fails_the_policy(host, monkeypatch, which):
    real_scandir = os.scandir
    blocked = _real(host / which)

    def scandir(path):
        if os.path.realpath(path) == blocked:
            raise PermissionError(13, "Permission denied", path)
        return real_scandir(path)

    monkeypatch.setattr(bwrap_private.os, "scandir", scandir)
    with pytest.raises(SandboxError, match="cannot list"):
        _policy(host)


def test_a_write_root_at_the_tree_is_still_refused(host):
    with pytest.raises(SandboxError, match="too broad"):
        _policy(host, writes=(Path("/run"),))
