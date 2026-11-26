"""#1849 evidence: a collect-like run under the real verifier account, and the account's process
tree (name, pid, ppid, pgid, parent name) at the moment reap() starts, right after main exited.
It asserts nothing about who is left: the CI jobs show whether the leftovers are in main's group
(see -s output / the recorded property). Skipped when no verifier account is configured."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairnwork import confined
from cairnwork.verifier_account import DEFAULT_ROOT, VerifierAccount

pytestmark = [pytest.mark.verifier_account]

# starts python's multiprocessing resource tracker (a helper that outlives the call for a moment)
COLLECT_LIKE = "import multiprocessing.resource_tracker as r; r.ensure_running()"


def _tree(user):
    flag = ["-x"] if sys.platform == "darwin" else []
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/ps", *flag, "-U", user, "-o", "pid=,ppid=,pgid=,comm="],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    rows = [line.split(None, 3) for line in done.stdout.splitlines() if line.strip()]
    names = {r[0]: r[3] for r in rows if len(r) == 4}
    return [
        f"name={r[3]} pid={r[0]} ppid={r[1]} pgid={r[2]} parent={names.get(r[1], '-')}"
        for r in rows
        if len(r) == 4
    ]


def test_sample_the_accounts_tree_right_after_main_exits(tmp_path, record_property):
    account, taken = VerifierAccount(), {}
    env = {confined.RUN_TOKEN_VAR: "1849-sample", "PATH": "/usr/bin:/bin"}

    def reap(main=0, group=0):
        taken.update(group=group, main=main, tree=_tree(account.user))
        return account.reap(main, group=group)

    # Never "/": execute's cwd is the leftover sweep's scope, and "/" made it SIGKILL every
    # env-withheld process of the owner (Chrome, Terminal, Apple agents) on macOS (M5 P0 11/26).
    # A fresh dir under the verifier account's own runs base (the account can enter it; macOS
    # confined.execute wants a narrow root there, cwd strictly below it) keeps the sweep to
    # this run.
    runs = DEFAULT_ROOT / "runs"
    if not os.access(runs, os.W_OK | os.X_OK):
        pytest.skip(f"{runs} is not writable here: no narrow root the verifier account can enter")
    root = os.path.realpath(tempfile.mkdtemp(prefix="cw1849-", dir=runs))
    work = os.path.join(root, "work")
    os.mkdir(work)
    for d in (root, work):
        os.chmod(d, 0o750)  # group (cairnwork, setgid) can enter; never "/"
    try:
        with account.exclusive():
            account.require_idle()
            argv = account.wrap([account.python, "-c", COLLECT_LIKE], None, env)
            code, timed_out, left = confined.execute(
                argv,
                work,
                env,
                60,
                tmp_path / "log",
                reap=reap,
                pgrp=True,
                scope_roots=(Path(root),),
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    text = "\n".join(taken["tree"]) or "(empty)"
    print(
        f"\n#1849 group={taken['group']} main={taken['main']} exit={code} left={int(left)}\n{text}"
    )
    record_property("tree_after_main_exit", text)
    assert taken and not timed_out
