"""experimental.local_worker.precheck: tell the agent what the Core would refuse, before it
finishes (report only, never a rewrite)."""

from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest
from local_worker_fakes import NO_AUTO_MAINTENANCE, TAG, TASK, World, make_loop, make_runs, reply
from local_worker_fakes import run_wrapper as run

from experimental.local_worker import precheck
from experimental.local_worker.agent import initial_messages

CHECK = b"src/app.py:2: trailing whitespace.\n+y = 2 \nnotes.md:3: trailing whitespace.\n+hi  \n"


def digest(*paths: str) -> bytes:
    return "".join(f":000000 100644 {'0' * 40} {'a' * 40} A\0{p}\0" for p in paths).encode()


# -- the parsing --------------------------------------------------------------------------------
def test_parse_check_names_path_line_and_kind():
    got = precheck.parse_check(CHECK)
    assert got == (
        precheck.Finding("src/app.py", 2, "trailing whitespace."),
        precheck.Finding("notes.md", 3, "trailing whitespace."),
    )


def test_a_candidate_line_never_becomes_a_finding():
    """Negative: added text that looks like a header (or an unknown kind) is not reported."""
    forged = b"+evil.py:9: trailing whitespace.\nsrc/x.py:1: something git never says.\n"
    assert precheck.parse_check(forged) == ()
    assert precheck.parse_check(b"\xff\xfe") is None  # not strict UTF-8: no report


def test_protected_paths_are_the_ones_under_a_test_root():
    got = precheck.protected_paths(digest("tests/test_new.py", "src/app.py"), ("tests",))
    assert got == ("tests/test_new.py",)
    assert precheck.protected_paths(digest("src/app.py", "README.md"), ("tests",)) == ()
    assert precheck.protected_paths(b"garbage", ("tests",)) is None


def test_the_report_lists_path_line_kind_and_caps_the_list():
    rep = precheck.Report(
        tuple(precheck.Finding(f"f{i}.py", i, "trailing whitespace.") for i in range(1, 26)),
        ("tests/test_a.py",),
    )
    text = rep.render()
    assert "f1.py:1: trailing whitespace." in text and "(+6 more)" in text
    assert "tests/test_a.py" not in text.split("(+6 more)")[0]  # beyond the cap
    assert precheck.Report().problems is False and rep.problems is True


# -- the wrapper's check query, real git --------------------------------------------------------
@pytest.mark.sandbox
def test_check_query_reports_only_lines_the_worker_added(tmp_path):
    runs = make_runs(tmp_path)
    co = runs / TAG / "checkout"
    (co / ".git").rmdir()
    git = ["git", "-C", str(co), "-c", "user.email=t@t", "-c", "user.name=t", *NO_AUTO_MAINTENANCE]
    subprocess.run([*git, "init", "-q"], check=True)
    (co / "old.py").write_text("a = 1 \nb = 2\n")  # an existing trailing space, committed
    (co / "doc.md").write_text("line\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    root = str(runs / TAG)
    clean = run(runs, "check", root)
    assert clean.returncode == 0 and clean.stdout == b""
    assert run(runs, "write", root, "old.py", stdin=b"a = 1 \nb = 3\nc = 4 \n").returncode == 0
    assert run(runs, "write", root, "doc.md", stdin=b"line\nhard  \n").returncode == 0
    out = run(runs, "check", root)
    assert out.returncode == 0, out.stdout
    got = precheck.parse_check(out.stdout)
    assert got == (  # old.py line 1 was already there: not named
        precheck.Finding("doc.md", 2, "trailing whitespace."),
        precheck.Finding("old.py", 3, "trailing whitespace."),
    ), out.stdout
    assert (co / "old.py").read_bytes() == b"a = 1 \nb = 3\nc = 4 \n"  # nothing rewritten
    assert (co / "doc.md").read_bytes() == b"line\nhard  \n"


# -- the loop -----------------------------------------------------------------------------------
class CheckWorld(World):
    check_out = b""
    digest_out = b""

    def handle(self, action, args, stdin):
        if action == "check":
            return 0, self.check_out
        if action == "digest":
            return 0, self.digest_out
        return super().handle(action, args, stdin)


def loop_with(tmp_path, replies, world, roots=("tests",)):
    loop, world, endpoint, _ = make_loop(tmp_path, replies, world)
    loop.settings = replace(loop.settings, precheck_roots=roots)
    return loop, world, endpoint


def tool_texts(endpoint) -> list[str]:
    last = endpoint.payloads[-1]["messages"]
    return [m["content"] for m in last if m["role"] == "tool"]


def test_finish_with_problems_is_answered_once_then_allowed(tmp_path):
    world = CheckWorld()
    world.check_out, world.digest_out = CHECK, digest("src/app.py", "tests/test_new.py")
    done = reply("finish", status="DONE")
    loop, world, endpoint = loop_with(tmp_path, [done, done], world)
    out = loop.run(initial_messages(TASK))
    assert out.exit_reason == "COMPLETED" and len(out.turns) == 2
    report = tool_texts(endpoint)[0]
    assert "src/app.py:2: trailing whitespace." in report and "notes.md:3" in report
    assert "tests/test_new.py" in report and "Nothing was changed" in report
    assert [c[0] for c in world.calls if c[0] == "write"] == []  # report only
    assert [c[0] for c in world.calls].count("check") == 1  # the second finish is not re-checked


def test_a_clean_change_finishes_at_once(tmp_path):
    world = CheckWorld()
    world.digest_out = digest("src/app.py")
    loop, world, _ = loop_with(tmp_path, [reply("finish", status="DONE")], world)
    out = loop.run(initial_messages(TASK))
    assert out.exit_reason == "COMPLETED" and len(out.turns) == 1


def test_off_by_default_nothing_is_queried(tmp_path):
    """Negative: without precheck_roots the loop is exactly as before (no query, no reply)."""
    world = CheckWorld()
    world.check_out = CHECK
    loop, world, _, _ = make_loop(tmp_path, [reply("finish", status="DONE")], world)
    out = loop.run(initial_messages(TASK))
    assert out.exit_reason == "COMPLETED" and len(out.turns) == 1
    assert not [c for c in world.calls if c[0] in ("check", "digest")]


def test_needs_spec_is_not_checked(tmp_path):
    world = CheckWorld()
    world.check_out = CHECK
    loop, world, _ = loop_with(tmp_path, [reply("finish", status="NEEDS_SPEC")], world)
    out = loop.run(initial_messages(TASK))
    assert out.needs_spec and len(out.turns) == 1
    assert not [c for c in world.calls if c[0] in ("check", "digest")]


def test_a_failed_query_gives_no_report(tmp_path):
    """Failure: a check that exits nonzero must not block a finish (the Core still decides)."""

    class Broken(CheckWorld):
        def handle(self, action, args, stdin):
            return (
                (3, b"git failed\n") if action == "check" else super().handle(action, args, stdin)
            )

    loop, _, _ = loop_with(tmp_path, [reply("finish", status="DONE")], Broken())
    out = loop.run(initial_messages(TASK))
    assert out.exit_reason == "COMPLETED" and len(out.turns) == 1
