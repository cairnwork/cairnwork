"""The optional write scope (#88 follow-up): hard links. A file's other names may lie outside
the scope, so a scoped write refuses a target with a second name, or any such file in the run root,
and the tool profile lets test code make no hard link at all (Codex round 1 on #250 and the Claude
reviews of its fix)."""

import os
import sys

import pytest
from local_worker_fakes import TAG, make_runs
from local_worker_fakes import run_wrapper as run


@pytest.fixture
def runs(tmp_path):
    return make_runs(tmp_path)


def write(runs, path, *scope, data=b"new\n"):
    extra = ("--scope", *scope) if scope else ()
    return run(runs, "write", str(runs / TAG), path, *extra, stdin=data)


def refused(proc) -> bool:
    return proc.returncode == 64 and b"refusing" in proc.stderr and proc.stdout == b""


@pytest.mark.sandbox
def test_a_hard_link_in_scope_is_refused(runs):
    """Another name of the same file may lie outside the scope (test code can make one)."""
    co = runs / TAG / "checkout"
    (co / "out").mkdir()
    (co / "out" / "f").write_text("orig\n")
    os.link(co / "out" / "f", co / "sub" / "h")
    proc = write(runs, "sub/h", "sub")
    assert refused(proc) and b"out of write scope: sub/h has 2 hard links" in proc.stderr
    assert (co / "out" / "f").read_text() == "orig\n"
    (co / "out" / "f").unlink()  # its only name now: in scope
    assert write(runs, "sub/h", "sub").returncode == 0
    assert (co / "sub" / "h").read_bytes() == b"new\n"


def test_any_hard_link_in_the_checkout_refuses_a_scoped_write(runs):
    """A file with a second name anywhere in the checkout could have its directory moved into the
    scope after the check (Claude review of the Codex round-1 fix): every scoped write is refused
    meanwhile, though the target itself is new."""
    co = runs / TAG / "checkout"
    (co / "x").mkdir()
    (co / "out").mkdir()
    (co / "x" / "a").write_text("a\n")
    os.link(co / "x" / "a", co / "out" / "b")
    proc = write(runs, "sub/new.py", "sub")
    msg = b"local-worker-tool: refusing: out of write scope: a file of the run has hard links: "
    named = (msg + b"checkout/out/b\n", msg + b"checkout/x/a\n")  # either: find's walk order
    assert refused(proc) and proc.stderr in named, proc.stderr
    assert not (co / "sub" / "new.py").exists() and (co / "out" / "b").read_text() == "a\n"


def test_a_hard_link_parked_in_tmp_refuses_a_scoped_write_too(runs):
    """Claude review of 40befbd: the pair could wait in tmp during the check and come back after."""
    tmp = runs / TAG / "tmp"
    (tmp / "x").mkdir()
    (tmp / "x" / "a").write_text("a\n")
    os.link(tmp / "x" / "a", tmp / "b")
    proc = write(runs, "sub/new.py", "sub")
    assert refused(proc) and b"a file of the run has hard links: tmp/" in proc.stderr, proc.stderr
    assert not (runs / TAG / "checkout" / "sub" / "new.py").exists()


def test_an_unsearchable_directory_refuses_a_scoped_write(runs):
    """The search fails closed: a directory test code made unreadable could hide a pair."""
    hidden = runs / TAG / "checkout" / "hidden"
    hidden.mkdir()
    hidden.chmod(0)
    try:
        proc = write(runs, "sub/new.py", "sub")
    finally:
        hidden.chmod(0o700)
    assert refused(proc) and b"cannot be searched for hard links" in proc.stderr, proc.stderr
    assert not (runs / TAG / "checkout" / "sub" / "new.py").exists()


@pytest.mark.sandbox
def test_hard_links_inside_git_or_without_a_scope_refuse_nothing(runs):
    co = runs / TAG / "checkout"
    (co / ".git" / "o1").write_text("o\n")
    os.link(co / ".git" / "o1", co / ".git" / "o2")  # .git is never writable: skipped
    assert write(runs, "sub/new.py", "sub").returncode == 0
    (co / "out").mkdir()
    os.link(co / ".git" / "o1", co / "out" / "o3")  # now a second name outside .git
    assert refused(write(runs, "sub/new2.py", "sub"))
    assert write(runs, "sub/new2.py").returncode == 0  # unscoped: no such check


_LINKS = """import os, sys
co, tmp = sys.argv[1:3]
def link(src, dst):
    try:
        os.link(src, dst)
        print("LINKED", dst)
    except PermissionError:
        print("DENIED", dst)
link(co + "/a.txt", co + "/out/a")
link(co + "/a.txt", tmp + "/a")
link(tmp + "/t", co + "/out/t")
link(tmp + "/t", tmp + "/t2")
os.rename(co + "/sub", tmp + "/d")  # the directory route: out to tmp, link there, back again
link(tmp + "/d/f.py", tmp + "/g")
os.rename(tmp + "/d", co + "/sub")
print("RENAMED BACK")
"""


@pytest.mark.sandbox
def test_test_code_cannot_give_a_checkout_file_a_second_name(runs):
    """Codex round 1, MF1: a process a sweep missed must not hard-link a file after a scoped
    write's link-count check (the write would then change the other name too), not even by moving
    its directory into tmp, linking there and moving it back. The tool profile denies every link."""
    root = runs / TAG
    co, tmp = root / "checkout", root / "tmp"
    (co / "out").mkdir()
    (co / "sub" / "f.py").write_text("f\n")
    (tmp / "t").write_text("t\n")
    (tmp / "links.py").write_text(_LINKS)
    base = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    py = root / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f"#!/bin/sh\nexec {base} -I {tmp}/links.py {co} {tmp}\n")
    py.chmod(0o755)
    proc = run(runs, "pytest", str(root))
    assert proc.stdout.decode().split("\n")[:6] == [
        f"DENIED {co}/out/a",
        f"DENIED {tmp}/a",
        f"DENIED {co}/out/t",
        f"DENIED {tmp}/t2",
        f"DENIED {tmp}/g",
        "RENAMED BACK",
    ], proc.stdout
    assert (co / "a.txt").stat().st_nlink == 1 and (co / "sub" / "f.py").stat().st_nlink == 1
    assert not (co / "out" / "t").exists() and not (tmp / "g").exists()
