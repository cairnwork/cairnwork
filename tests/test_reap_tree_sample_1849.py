"""#1849 evidence: a collect-like run under the real verifier account, and the account's process
tree (name, pid, ppid, pgid, parent name) at the moment reap() starts, right after main exited.
It asserts nothing about who is left: the CI jobs show whether the leftovers are in main's group
(see -s output / the recorded property). Skipped when no verifier account is configured."""

import subprocess
import sys

import pytest

from cairnwork import confined
from cairnwork.verifier_account import VerifierAccount

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

    with account.exclusive():
        account.require_idle()
        argv = account.wrap([account.python, "-c", COLLECT_LIKE], None, env)
        code, timed_out, left = confined.execute(
            argv, "/", env, 60, tmp_path / "log", reap=reap, reap_group=True
        )
    text = "\n".join(taken["tree"]) or "(empty)"
    print(
        f"\n#1849 group={taken['group']} main={taken['main']} exit={code} left={int(left)}\n{text}"
    )
    record_property("tree_after_main_exit", text)
    assert taken and not timed_out
