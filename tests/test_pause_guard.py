"""PAUSE (#476) AC8 (a): until the plan arrives the worker's tests run under pytest-ro, whose
Seatbelt profile denies every checkout write to them and to every process they leave behind; the
checkout digest checks it held; one controller numbers every event; fail closed."""

import os
import shutil
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from local_worker_fakes import TAG, WRAPPER, make_runs

from experimental.local_worker.tools import PrePlanReadOnly, WorkerTools, subprocess_invoker
from experimental.pause.controller import JsonlEventLog, PrePlanGuard, read_events
from experimental.pause.guard import TreeGuard
from experimental.pause.interfaces import (
    CheckoutGuard,
    EventLog,
    GuardUnavailable,
    RollbackGuard,
)
from experimental.pause.schema import Reason
from tests.conftest import git

PATCH = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 4\n"
SNAP_TEST = """import os, pathlib
def test_snap():
    snap = pathlib.Path(__file__).parent / "__snapshots__" / "snap.txt"
    if os.environ.get("UPDATE_SNAPSHOTS", "1"):
        snap.parent.mkdir(exist_ok=True)
        snap.write_text("new\\n")
"""
FORMATTER = """import pathlib, sys
for p in pathlib.Path(sys.argv[1]).rglob("*.py"):
    p.write_text(p.read_text().replace(" = ", "="))
"""
# The run venv's "python": answers pytest-ro's --version probe, else runs the test's job script.
SHIM = '#!/bin/sh\ncase " $* " in *" --version "*) echo "pytest 0"; exit 0 ;; esac\n'
SHIM += 'exec /bin/sh "$(dirname "$0")/../../tmp/job.sh"\n'
BASE_PY = os.path.realpath(getattr(sys, "_base_executable", sys.executable))


def _fill(co):
    git(co, "init", "-q", "-b", "main", "--template=")
    git(co, "config", "user.email", "test@example.invalid")
    git(co, "config", "user.name", "test")
    for rel, text in {
        "src/a.py": "x = 1\n",
        "src/b.py": "y = 1\n",
        ".gitignore": "*.log\n",
        "run.sh": "#!/bin/sh\n",
        "tests/test_snap.py": SNAP_TEST,
    }.items():
        (co / rel).parent.mkdir(parents=True, exist_ok=True)
        (co / rel).write_text(text)
    (co / "run.sh").chmod(0o755)
    os.symlink("src/a.py", co / "link")
    git(co, "add", "-A")
    git(co, "commit", "-q", "-m", "base")


@pytest.fixture
def checkout(tmp_path):
    co = tmp_path / "repo"
    co.mkdir()
    _fill(co)
    return co


@pytest.fixture
def run_root(tmp_path):
    runs = make_runs(tmp_path)
    root = runs / uuid.uuid4().hex  # its own run tag: a sweep here never reaches another test's
    (runs / TAG).rename(root)
    shutil.rmtree(root / "checkout")
    (root / "checkout").mkdir()
    _fill(root / "checkout")
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text(SHIM)
    (root / "venv" / "bin" / "python").chmod(0o755)
    (root / "tmp" / "fix.patch").write_text(PATCH)
    (root / "tmp" / "fmt.py").write_text(FORMATTER)
    (root / "tmp" / "edit.sh").write_text('printf "x = 9\\n" > "$1/src/a.py"\n')
    return root


def _tools(root, tmp_path, wrapper=WRAPPER):
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(root.parent),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    return WorkerTools(
        root,
        tmp_path / "calls.jsonl",
        mode="direct",
        wrapper=wrapper,
        invoker=subprocess_invoker(env),
        timeout_seconds=60,
    )


def _log(checkout, tmp_path):
    (tmp_path / "ctl").mkdir(mode=0o700)
    return JsonlEventLog(tmp_path / "ctl" / "events.jsonl", checkout=checkout)


def _job(root, script):
    (root / "tmp" / "job.sh").write_text(f'T="{root / "tmp"}"; PY="{BASE_PY}"\n{script}\n')


WORKAROUNDS = {
    "sed_i": "sed -i.bak 's/x = 1/x = 2/' src/a.py",
    "redirect": "echo 'x = 3' > src/a.py",
    "git_apply": 'git apply -- "$T/fix.patch"',
    "python_c": "\"$PY\" -c \"open('src/a.py','a').write('z=1')\"",
    "patch": 'patch -p1 -i "$T/fix.patch"',
    "formatter": '"$PY" "$T/fmt.py" .',
    "pytest_snapshot": '"$PY" -c "import sys; sys.path[:0] = [\'tests\']; '
    'import test_snap; test_snap.test_snap()"',
    "tmp_script": 'sh "$T/edit.sh" "$PWD"',
    "ignored_file": "echo log > build.log",
    "chmod": "chmod 755 src/a.py",
    "symlink_target": "ln -sfh src/b.py link",
    "dir_to_symlink": 'rm -rf src && ln -s "$T" src',
    "git_hook": "mkdir -p .git/hooks && echo x > .git/hooks/pre-commit",
    "git_config": "git config --local core.hooksPath /x",
    "assume_unchanged": "git update-index --assume-unchanged -- src/a.py && echo y > src/a.py",
    "xattr": "xattr -w user.cw v src/a.py",
}


@pytest.mark.sandbox
@pytest.mark.parametrize("name", sorted(WORKAROUNDS))
def test_every_write_before_the_plan_is_denied(name, run_root, tmp_path):
    co = run_root / "checkout"
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), _tools(run_root, tmp_path))
    pre.start()
    _job(run_root, f'( {WORKAROUNDS[name]} ) > "$T/out" 2>&1; echo $? > "$T/rc"')
    pre.run_tests(["-q"])
    out = (run_root / "tmp" / "out").read_text()
    refused = (run_root / "tmp" / "rc").read_text().strip() != "0"
    assert refused or "Operation not permitted" in out, out  # patch(1) exits 0 all the same
    pre.at_plan_arrival()  # 0 changes kept: the checkout digest is the one taken at start


@pytest.mark.sandbox
def test_a_process_left_behind_can_never_write_but_the_plan_can(run_root, tmp_path):
    """The left-behind process clears its environment and leaves the run root, so local
    worker's sweep does not find it; it outlives the plan and a writable test run."""
    co, tmp = run_root / "checkout", run_root / "tmp"
    tools = _tools(run_root, tmp_path)
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), tools)
    pre.start()
    late = (
        f'cd /; while [ ! -e "{tmp}/go" ]; do sleep 0.1; done; '
        f'{{ echo "x = 9" > "{co}/src/a.py"; }} 2> "{tmp}/late.err"; echo $? > "{tmp}/late.rc"'
    )
    _job(run_root, f"/usr/bin/env -i /bin/sh -c '{late}' </dev/null >/dev/null 2>&1 &")
    pre.run_tests(["-q"])
    pre.at_plan_arrival()
    _job(run_root, 'echo "y = 2" > src/b.py')
    after = tools.run_tests(["-q"])  # after the plan: the writable profile
    assert after.exit_code == 0 and (co / "src" / "b.py").read_text() == "y = 2\n"
    (tmp / "go").write_text("")
    for _ in range(300):
        if (tmp / "late.rc").exists() and (tmp / "late.rc").read_text().strip():
            break
        subprocess.run(["/bin/sleep", "0.1"], check=True)
    assert (tmp / "late.rc").read_text().strip() != "0"
    assert "Operation not permitted" in (tmp / "late.err").read_text()
    assert (co / "src" / "a.py").read_text() == "x = 1\n"


@pytest.mark.sandbox
def test_a_wrapper_without_pytest_ro_fails_closed_and_runs_nothing(run_root, tmp_path):
    text = WRAPPER.read_text().replace("  pytest-ro)\n", "  pytest-ro-unknown)\n")
    old = tmp_path / "local-worker-tool"
    old.write_text(text)
    co = run_root / "checkout"
    _job(run_root, f'echo ran > "{run_root / "tmp" / "ran"}"')
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), _tools(run_root, tmp_path, old))
    with pytest.raises(GuardUnavailable, match="pytest-ro") as err:
        pre.start()
    assert err.value.reason == Reason.PAUSE_GUARD_UNAVAILABLE
    assert "unknown action 'pytest-ro'" in err.value.args[0]
    assert not (run_root / "tmp" / "ran").exists()  # never the writable pytest instead


@pytest.mark.sandbox
def test_a_hard_link_to_a_checkout_file_stops_the_guard_before_it_starts(run_root, tmp_path):
    co = run_root / "checkout"
    os.link(co / "src" / "a.py", run_root / "tmp" / "a-link.py")  # a second name, outside
    _job(run_root, f'echo ran > "{run_root / "tmp" / "ran"}"')
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), _tools(run_root, tmp_path))
    with pytest.raises(GuardUnavailable, match="hard links"):
        pre.start()
    assert pre.baseline is None and not (run_root / "tmp" / "ran").exists()


@pytest.mark.sandbox
def test_pytest_ro_cannot_make_a_hard_link_in_or_out_of_the_checkout(run_root, tmp_path):
    co, tmp = run_root / "checkout", run_root / "tmp"
    (tmp / "f.txt").write_text("f\n")
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), _tools(run_root, tmp_path))
    pre.start()
    links = {
        "out": f'ln "{co}/src/a.py" "{tmp}/out.py"',
        "in": f'ln "{co}/src/a.py" "{co}/in.py"',
        "into": f'ln "{tmp}/f.txt" "{co}/into.txt"',
        "tmp": f'ln "{tmp}/f.txt" "{tmp}/g.txt"',
    }
    _job(
        run_root,
        "\n".join(f'{cmd} 2> "$T/{k}.err"; echo $? > "$T/{k}.rc"' for k, cmd in links.items()),
    )
    pre.run_tests(["-q"])
    for k in links:
        assert (tmp / f"{k}.rc").read_text().strip() != "0", k
        assert "Operation not permitted" in (tmp / f"{k}.err").read_text(), k
    assert not any(p.exists() for p in (tmp / "out.py", co / "in.py", co / "into.txt"))
    assert (co / "src" / "a.py").stat().st_nlink == 1 and (tmp / "f.txt").stat().st_nlink == 1
    pre.at_plan_arrival()


@pytest.mark.sandbox
def test_the_guard_refuses_to_start_after_any_tool_call(run_root, tmp_path):
    co, tools = run_root / "checkout", _tools(run_root, tmp_path)
    _job(run_root, "true")
    assert tools.run_tests(["-q"]).exit_code == 0  # writable, before the guard
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), tools)
    with pytest.raises(GuardUnavailable, match="first tool call"):
        pre.start()
    assert pre.baseline is None and tools.read_only_until_plan is False


@pytest.mark.sandbox
def test_writable_actions_are_refused_until_the_plan_arrives(run_root, tmp_path):
    co, tmp, tools = run_root / "checkout", run_root / "tmp", _tools(run_root, tmp_path)
    pre = PrePlanGuard(TreeGuard(co), _log(co, tmp_path), tools)
    pre.start()
    _job(run_root, f'echo ran >> "{tmp / "ran"}"')
    for attempt in (
        lambda: tools.run_tests(["-q"]),
        lambda: tools.write_file("src/a.py", "x = 2\n"),
        lambda: tools.edit_file("src/a.py", "x = 1", "x = 2"),
    ):
        with pytest.raises(PrePlanReadOnly):
            attempt()
    assert not (tmp / "ran").exists() and (co / "src" / "a.py").read_text() == "x = 1\n"
    pre.at_plan_arrival()
    assert tools.run_tests(["-q"]).exit_code == 0 and (tmp / "ran").read_text() == "ran\n"
    assert tools.write_file("src/a.py", "x = 2\n").exit_code == 0


def _profile(root, action):
    """The profile text the wrapper hands sandbox-exec (a stand-in prints it)."""
    fake = root.parent / "fake-sandbox-exec"
    fake.write_text('#!/bin/sh\nprintf "%s" "$2"\n')
    fake.chmod(0o755)
    text = WRAPPER.read_text().replace("SANDBOX_EXEC=/usr/bin/sandbox-exec", f"SANDBOX_EXEC={fake}")
    copy = root.parent / "wrapper-copy"
    copy.write_text(text)
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(root.parent),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    argv = ["/bin/sh", str(copy), action, str(root), "-q"]
    return subprocess.run(argv, capture_output=True, env=env, cwd="/", check=True).stdout.decode()


def test_pytest_ro_is_the_pytest_profile_without_any_checkout_write(run_root):
    co, tmp = run_root / "checkout", run_root / "tmp"
    writable, read_only = _profile(run_root, "pytest"), _profile(run_root, "pytest-ro")
    allow = f'(allow file-write* (subpath "{co}") (subpath "{tmp}")'
    assert writable.count(allow) == 1
    expected = writable.replace(allow, f'(allow file-write*  (subpath "{tmp}")')
    assert read_only == expected + f'\n(deny file-write* (subpath "{co}"))'
    assert "\n(deny file-link)\n" in read_only  # no new hard link in the window either


class FakeTools:
    def __init__(self, *exit_codes):
        self.codes, self.calls = list(exit_codes), []
        self.calls_started, self.read_only_until_plan = 0, False

    def enter_read_only_if_unused(self):
        self.read_only_until_plan = self.calls_started == 0
        return self.read_only_until_plan

    def run_tests(self, args, read_only=False):
        self.calls.append((list(args), read_only))
        return SimpleNamespace(exit_code=self.codes.pop(0), output="", raw=b"")


def test_no_seatbelt_no_guard(checkout, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    tools: Any = FakeTools(0)
    with pytest.raises(GuardUnavailable, match="macOS"):
        PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools).start()
    assert tools.calls == []


@pytest.mark.skipif(sys.platform != "darwin", reason="the guard runs on macOS only")
def test_the_guard_runs_tests_read_only_and_numbers_every_event(checkout, tmp_path):
    tools: Any = FakeTools(0, 1, 0)
    log = _log(checkout, tmp_path)
    pre = PrePlanGuard(TreeGuard(checkout), log, tools)
    assert isinstance(pre.guard, CheckoutGuard) and isinstance(log, EventLog)
    with pytest.raises(GuardUnavailable):
        pre.run_tests(["-q"])  # before start
    pre.start()
    pre.run_tests(["-q"])
    pre.run_tests(["-x"])
    threads = [
        threading.Thread(target=lambda: [log.record("x") for _ in range(50)]) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pre.at_plan_arrival()
    assert tools.calls == [(["--version"], True), (["-q"], True), (["-x"], True)]
    events = read_events(tmp_path / "ctl" / "events.jsonl")
    assert [e["seq"] for e in events] == list(range(1, 1 + 4 + 200 + 1 + 1))
    assert events[0]["kind"] == "guard_start" and events[-1]["kind"] == "plan_arrival"
    with pytest.raises(GuardUnavailable):
        pre.run_tests(["-q"])  # after the plan: no longer this guard's


@pytest.mark.skipif(sys.platform != "darwin", reason="the guard runs on macOS only")
def test_a_change_found_when_the_plan_arrives_stops_the_run(checkout, tmp_path):
    tools: Any = FakeTools(0)
    pre = PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools)
    pre.start()
    (checkout / "src" / "a.py").write_text("x = 6\n")  # cannot happen under pytest-ro
    with pytest.raises(GuardUnavailable, match="changed"):
        pre.at_plan_arrival()
    assert (checkout / "src" / "a.py").read_text() == "x = 6\n"  # nothing repaired
    kinds = [e["kind"] for e in read_events(tmp_path / "ctl" / "events.jsonl")]
    assert kinds == ["guard_start", "guard_changed"]
    assert not isinstance(pre.guard, RollbackGuard)


@pytest.mark.skipif(sys.platform != "darwin", reason="the guard runs on macOS only")
def test_a_hard_link_found_when_the_plan_arrives_stops_the_run(checkout, tmp_path):
    tools: Any = FakeTools(0)
    pre = PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools)
    pre.start()
    os.link(checkout / "src" / "b.py", tmp_path / "b-link.py")  # cannot happen under pytest-ro
    with pytest.raises(GuardUnavailable, match="hard links"):
        pre.at_plan_arrival()


def test_a_failed_probe_fails_closed(checkout, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    tools: Any = FakeTools(64)
    with pytest.raises(GuardUnavailable, match="pytest-ro"):
        PrePlanGuard(TreeGuard(checkout), _log(checkout, tmp_path), tools).start()
