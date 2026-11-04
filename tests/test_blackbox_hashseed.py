"""#815: the blackbox run fixes PYTHONHASHSEED, so a candidate whose function returns a set's
iteration order (AMICI-dev/AMICI#3329) gives the same output on every run, not a new one each
time. Real runs through ``run_blackbox``; skipped where there is no sandbox backend."""

import json

import pytest
from conftest import commit_change, git

from cairnwork import blackbox, blackbox_exec
from cairnwork.blackbox_runner import HASH_SEED
from cairnwork.verify_context import VerifyContext

SET_ORDER = "def names():\n    return [s for s in {'x_a', 'x_b', 'x_c', 'y_a', 'y_b'}]\n"
SALT = "ab" * 32
CHECK = {"id": "n", "module": "calc", "function": "names",
         "cases": [{"id": "c1", "args": [], "expected": ["x_a"]}]}  # fmt: skip


def _outputs(repo, tmp_path, runs):
    declared = blackbox.parse(json.dumps({"salt": SALT, "checks": [CHECK]}).encode())
    head = commit_change(repo, {"calc.py": SET_ORDER})
    seen = []
    for number in range(runs):
        ctx = VerifyContext(repo, tmp_path / f"runs{number}")
        run = blackbox_exec.run_blackbox(declared, ctx, head, f"a{number}-v")
        if run.report is None:
            pytest.skip("no sandbox backend on this host")
        seen.append(json.loads(run.report)["checks"]["n"]["cases"]["c1"])
    assert git(repo, "rev-parse", "HEAD") == head
    return seen


def test_one_candidate_gives_one_output_on_every_run(repo, tmp_path):
    seen = _outputs(repo, tmp_path, 4)
    assert all("ok" in case for case in seen)
    assert all(case == seen[0] for case in seen)  # unset seed: 4 runs, about 4 different orders


def test_the_seed_is_a_fixed_string():
    assert HASH_SEED == "0"
