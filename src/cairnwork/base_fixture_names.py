"""#986 step 5b: the fixture names pytest itself collects on the base, for ``check_conftests``.

One owner collect-only run on the base commit with the recorder on (``record_fixtures``, see
fixture_probe.py). It targets the existing base test files the test commit changes, so it sees the
plugins, ``pytest_plugins`` and dynamic fixtures those files meet. The result is advisory
(``check_conftests`` only unions it with the static names, so it can add a refusal and never remove
one); any fault, an account run, a conftest-free test commit or no base target gives None, and the
#916 rule then stands alone, as before this step.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from cairnwork.conftest_check import check_conftests, conftest_of
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

MAX_TARGETS = 20
LOG = logging.getLogger(__name__)
LOUD = ("RUN_COUNT", "NO_NAMES_RECORDED")  # plus every RUN_FAULT: an attempt that gave nothing


def _on_base(repo: Path, base: str, path: str) -> bool:
    done = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{base}:{path}"],
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def check_with_base(ctx, task, paths: tuple[str, ...], run_id: str) -> None:
    """``check_conftests`` with the base's collected fixture names (None when not obtained)."""
    collected = collect(ctx, task, paths, run_id)
    check_conftests(task.repo, task.base_commit, task.test_commit, paths, collected=collected)


def collect(ctx, task, paths: tuple[str, ...], run_id: str):
    """The names the base run recorded, or None; a None is logged with its reason code."""
    names, reason = probe(ctx, task, paths, run_id)
    if names is None:
        level = (
            logging.WARNING if reason in LOUD or reason.startswith("RUN_FAULT") else logging.INFO
        )
        LOG.log(level, "base fixture names not used (%s): the #916 rule stands alone", reason)
    return names


def probe(ctx, task, paths: tuple[str, ...], run_id: str):
    """(names, reason): the names the base run recorded and "OK", or (None, a reason code):
    ACCOUNT_RUN, NO_CONFTEST_CHANGE, NO_BASE_TARGET (not attempted), RUN_FAULT:<type>,
    RUN_COUNT or NO_NAMES_RECORDED (attempted, nothing usable). Diagnostics only."""
    conftests = conftest_of(paths)
    if ctx.account is not None:
        return None, "ACCOUNT_RUN"
    if not conftests:
        return None, "NO_CONFTEST_CHANGE"
    try:
        targets = tuple(
            p
            for p in paths
            if p.endswith(".py") and p not in conftests and _on_base(task.repo, task.base_commit, p)
        )[:MAX_TARGETS]
        if not targets:
            return None, "NO_BASE_TARGET"
        spec = VerifierSpec("fixture-names", targets, collect_only=True, record_fixtures=True)
        runs = run_verifiers(ctx, task.base_commit, (spec,), run_id)
        if len(runs) != 1:
            return None, "RUN_COUNT"
        names = runs[0].fixture_names
        return (names, "OK") if names is not None else (None, "NO_NAMES_RECORDED")
    except Exception as exc:  # noqa: BLE001 - any fault means no names (the #916 rule)
        return None, f"RUN_FAULT:{type(exc).__name__}"
