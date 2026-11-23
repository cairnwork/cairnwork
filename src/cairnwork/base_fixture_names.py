"""#986 step 5b: the fixture names pytest itself collects on the base, for ``check_conftests``.

One owner collect-only run on the base commit with the recorder on (``record_fixtures``, see
fixture_probe.py). It targets the existing base test files the test commit changes, so it sees the
plugins, ``pytest_plugins`` and dynamic fixtures those files meet. The result is advisory
(``check_conftests`` only unions it with the static names, so it can add a refusal and never remove
one); any fault, an account run, a conftest-free test commit or no base target gives None, and the
#916 rule then stands alone, as before this step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from cairnwork import deferred_collected
from cairnwork.conftest_check import check_conftests, conftest_of
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

MAX_TARGETS = 20


def _on_base(repo: Path, base: str, path: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{base}:{path}"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def check_with_base(ctx, task, paths: tuple[str, ...], run_id: str, targets=None) -> None:
    """``check_conftests`` with the base's collected fixture names (None when not obtained); with
    ``targets`` (``new_tests``), also the collected-set cross-check (#974)."""
    collected = collect(ctx, task, paths, run_id)
    check_conftests(task.repo, task.base_commit, task.test_commit, paths, collected=collected)
    if targets is not None:
        deferred_collected.check(ctx, task, paths, targets, f"{run_id}c")


def collect(ctx, task, paths: tuple[str, ...], run_id: str):
    """The names the base run recorded, or None."""
    conftests = conftest_of(paths)
    if ctx.account is not None or not conftests:
        return None
    try:
        targets = tuple(
            p
            for p in paths
            if p.endswith(".py") and p not in conftests and _on_base(task.repo, task.base_commit, p)
        )[:MAX_TARGETS]
        if not targets:
            return None
        spec = VerifierSpec("fixture-names", targets, collect_only=True, record_fixtures=True)
        runs = run_verifiers(ctx, task.base_commit, (spec,), run_id)
        return runs[0].fixture_names if len(runs) == 1 else None
    except Exception:  # noqa: BLE001 - any fault means no names (the #916 rule)
        return None
