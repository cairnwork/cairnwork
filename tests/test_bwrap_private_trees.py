"""The private, read-only /run, /var/run and /tmp of a bwrap run (#391), as policy text: on any
host, with the trees and /etc pointed at directories under tmp_path. The real mounts are witnessed
on Linux CI by tests/test_run_isolation_witness.py and tests/test_tmp_isolation_witness.py (no
host socket there connectable, and a run, with its roots under /tmp in owner mode, still works)."""

import os
from pathlib import Path

import pytest

from cairnwork_sandbox import bwrap, bwrap_private
from cairnwork_sandbox.backend import SandboxError


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake /run, /var/run (a link to it), /tmp and /etc, plus a home; the real ones are not
    read."""
    run, etc, home, tmp = tmp_path / "run", tmp_path / "etc", tmp_path / "home", tmp_path / "tmp"
    for directory in (run, etc, home, tmp):
        directory.mkdir()
    (tmp_path / "varrun").symlink_to(run)
    trees = (str(run), str(tmp_path / "varrun"), str(tmp))
    monkeypatch.setattr(bwrap_private, "TREES", trees)
    monkeypatch.setattr(bwrap_private, "HOLDS_ROOTS", str(tmp))
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
    assert lines[-4:] == ["--remount-ro", run, "--remount-ro", _real(host / "tmp")]
    assert lines.count(run) == 2  # /var/run is a link to /run: one tree, mounted once


def test_a_separate_var_run_gets_its_own_tmpfs(host, monkeypatch):
    (host / "varrun").unlink()
    (host / "varrun").mkdir()
    lines = _policy(host)
    run, varrun = _real(host / "run"), _real(host / "varrun")
    assert _at(lines, "--tmpfs", run) >= 0 and _at(lines, "--tmpfs", varrun) >= 0
    tmp = _real(host / "tmp")
    assert lines[-6:] == ["--remount-ro", run, "--remount-ro", varrun, "--remount-ro", tmp]


def test_an_absent_tree_adds_nothing(host, monkeypatch):
    monkeypatch.setattr(bwrap_private, "TREES", (str(host / "absent"),))
    lines = _policy(host)
    assert "--remount-ro" not in lines and str(host / "absent") not in "\n".join(lines)


@pytest.mark.parametrize("kind", ["read", "write"])
@pytest.mark.parametrize("where", ["run/py", "run", "varrun/py", "tmp"])
def test_a_root_in_run_or_at_a_tree_is_refused(host, kind, where):
    # No caller puts one in /run; binding it would bring host content (and sockets) into the
    # tree. A root that is a whole tree would undo it.
    root = host / where
    root.mkdir(exist_ok=True)
    reads, writes = ((root,), ()) if kind == "read" else ((), (root,))
    with pytest.raises(SandboxError, match="private"):
        _policy(host, reads, writes)


def test_a_root_under_tmp_is_put_back_on_the_tmpfs_before_read_only(host):
    # A run root under /tmp (the tests' owner-mode runs: pytest's tmp_path) comes back on the
    # private tmpfs: a read root read-only, a write root by the caller's --bind. A read root absent
    # at build time is left out (no source another user could plant in /tmp before bwrap mounts).
    # --remount-ro does not recurse, so the write bind stays writable: the run witnesses in
    # test_run_isolation_witness.py write there on Linux CI. Nothing else of /tmp comes back.
    tmp = host / "tmp"
    read, write, other = tmp / "r" / "py", tmp / "w", tmp / "other"
    for directory in (read, write, other):
        directory.mkdir(parents=True)
    lines = _policy(host, reads=(read, tmp / "absent"), writes=(write,))
    tmpfs, real, wreal = _at(lines, "--tmpfs", _real(tmp)), _real(read), _real(write)
    bind = _at(lines, "--bind", wreal, wreal)
    assert 0 <= tmpfs < _at(lines, "--ro-bind", real, real) < bind < len(lines) - 2
    assert "--ro-bind-try" not in lines and _real(tmp / "absent") not in lines
    assert lines[-2:] == ["--remount-ro", _real(tmp)] and _real(other) not in lines


def test_a_read_root_beside_or_holding_the_tree_adds_nothing(host):
    outside, sibling = host / "lib", host / "run2"  # run2 only shares the tree's name prefix
    outside.mkdir()
    sibling.mkdir()
    (host / "run" / "sib").symlink_to("../run2")  # leads out of the tree: not recreated
    lines = _policy(host, reads=(outside, host, sibling))
    assert "--symlink" not in lines and _real(sibling) not in lines
    assert _real(outside) not in lines  # the root bind already gives it
    assert _at(lines, "--ro-bind", _real(host), _real(host)) == -1  # would bring the tree back


def test_only_links_that_stay_in_the_tree_are_recreated_at_the_checked_path(host):
    run = host / "run"
    (run / "sub").mkdir()
    (host / "out").mkdir()
    (run / "inside").symlink_to("sub/x")
    (run / "inside-abs").symlink_to(run / "sub")
    (run / "mid").symlink_to("sub")  # an intermediate link: the checked path is what is recreated
    (run / "via").symlink_to("mid/x")
    (run / "mid-out").symlink_to(host / "out")
    (run / "via-out").symlink_to("mid-out/x")  # leaves the tree through mid-out: not recreated
    (run / "current-system").symlink_to("/nix/store/abc-system")  # outside the tree: #469
    (run / "out-abs").symlink_to("/tmp/agent.sock")  # noqa: S108 - escapes: not recreated
    (run / "out-rel").symlink_to("../../tmp/x")
    (run / "shm").symlink_to("../dev/shm")
    (run / "evil").symlink_to("x\n--bind\n/\n/")  # cannot add a policy line
    (run / "dash").symlink_to("--bind")
    (run / "x\nbad").symlink_to("sub")  # a name that would add a policy line
    (run / "-lead").symlink_to("sub")
    (run / "plain").write_text("x")
    (run / "sub" / "link").symlink_to("sub")  # not top level
    os.mkfifo(run / "s")  # stands in for a socket: anything not a link is left out alike
    lines = _policy(host)
    real = _real(run)
    links = [lines[i + 1 : i + 3] for i, flag in enumerate(lines) if flag == "--symlink"]
    assert links == [
        [f"{real}/--bind", f"{real}/dash"],  # an absolute path now: no option, no line break
        [f"{real}/sub/x", f"{real}/inside"],
        [f"{real}/sub", f"{real}/inside-abs"],
        [f"{real}/sub", f"{real}/mid"],
        [f"{real}/sub/x", f"{real}/via"],
    ]
    assert "bad" not in lines and f"{real}/-lead" not in lines


def test_a_run_link_to_an_outside_daemon_socket_is_masked_not_recreated(host, monkeypatch):
    # Rancher Desktop or Colima: /run/docker.sock -> ~/.rd/docker.sock. The private /run must
    # not point the run back at it, and the daemon-socket mask covers its real target.
    run, rd = host / "run", host / "home" / ".rd"
    rd.mkdir()
    (rd / "docker.sock").write_text("")  # stands in for the socket inode
    (run / "docker.sock").symlink_to(rd / "docker.sock")
    monkeypatch.setattr(bwrap, "_DANGEROUS_SOCKETS", (str(run / "docker.sock"),))
    lines = _policy(host)
    target = _real(rd / "docker.sock")
    assert f"{_real(run)}/docker.sock" not in lines
    assert _at(lines, "--ro-bind", "/dev/null", target) > _at(lines, "--tmpfs", _real(run))


def test_a_tree_whose_real_path_cannot_be_written_in_a_policy_fails(host, monkeypatch):
    odd = host / "r\nun"
    odd.mkdir()
    monkeypatch.setattr(bwrap_private, "TREES", (str(odd),))
    with pytest.raises(SandboxError, match="private"):
        _policy(host)


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
    (run / "resolve" / "x\nname.conf").write_text("x\n")
    (etc / "odd.conf").symlink_to(run / "resolve" / "x\nname.conf")  # cannot be a policy line
    lines = _policy(host)
    stub = _real(run / "resolve" / "stub-resolv.conf")
    assert _at(lines, "--ro-bind", stub, stub) > _at(lines, "--tmpfs", _real(run))
    assert lines.count(stub) == 2
    assert _real(run / "resolve" / "dir") not in lines
    assert _real(host / "elsewhere") not in lines
    assert "name.conf" not in lines


def test_an_etc_link_into_tmp_is_not_put_back(host):
    # A file under /tmp may belong to any local user, who could swap it for a socket between the
    # check and the mount: only /run and /var/run (system daemons' trees) get /etc files back.
    (host / "tmp" / "f.conf").write_text("x\n")
    (host / "run" / "f.conf").write_text("x\n")
    (host / "etc" / "tmp.conf").symlink_to(host / "tmp" / "f.conf")
    (host / "etc" / "run.conf").symlink_to(host / "run" / "f.conf")
    lines = _policy(host)
    assert _real(host / "tmp" / "f.conf") not in lines
    assert _at(lines, "--ro-bind", *[_real(host / "run" / "f.conf")] * 2) > 0


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


@pytest.mark.parametrize(
    "link,target", [("tmp", "run"), ("tmp", "run/x"), ("varrun", "tmp/vr"), ("varrun", "run/x")]
)
def test_a_tree_that_resolves_into_another_is_refused(host, link, target):
    # /tmp -> /run (or below): a root under /tmp would be a root in /run, bound back from the host
    # over the private /run. Only /var/run -> /run itself is one tree (the standard layout).
    (host / target).mkdir(parents=True, exist_ok=True)
    (host / link).unlink() if (host / link).is_symlink() else (host / link).rmdir()
    (host / link).symlink_to(host / target)
    with pytest.raises(SandboxError, match="overlaps"):
        _policy(host)


def test_a_write_root_that_holds_a_tree_is_refused(host):
    # /tmp -> /data/tmp with a write root /data: the write bind would cover the private /tmp.
    data = host / "data"
    (data / "tmp").mkdir(parents=True)
    (host / "tmp").rmdir()
    (host / "tmp").symlink_to(data / "tmp")
    with pytest.raises(SandboxError, match="holds"):
        _policy(host, writes=(data,))
    assert "--tmpfs" in _policy(host, reads=(data,))  # a read root outside the trees adds nothing


def test_a_write_root_at_the_tree_is_still_refused(host):
    with pytest.raises(SandboxError, match="too broad"):
        _policy(host, writes=(Path("/run"),))


def test_an_etc_entry_that_cannot_be_checked_fails_the_policy(host, monkeypatch):
    class Unreadable:
        name, path = "x", "/x"

        def is_symlink(self):
            raise OSError(5, "Input/output error")

    real_entries = bwrap_private._entries
    etc = bwrap_private.ETC
    monkeypatch.setattr(
        bwrap_private, "_entries", lambda d: [Unreadable()] if d == etc else real_entries(d)
    )
    with pytest.raises(SandboxError, match="/x"):
        _policy(host)
