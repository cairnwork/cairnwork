"""Run existing (pass-to-pass) tests over a candidate next to the task's own verifiers (#117).

After the worker is done, for a task with deferred tests: select existing test files by the
state's ``p2p_selection`` mode (p2p_selection.py), run them on the BASE to find the tests that
pass there, then run exactly those on the judged commit as one more verifier (``p2p-<mode>``)
that Core must see pass. A test that already failed or was skipped on the base is not a
regression and is left out.

Failure paths, none of which can end VERIFIED:

* base probe times out, cannot report, its report exceeds the size cap (a suite too large for
  one guard report, #184: ``base-probe-report-over-cap``), or the base run itself is unclean (a
  process outlived it, ``base-probe-survivors``, or it changed the checkout,
  ``base-probe-mutated``: the candidate run of the same tests would block on the base's own
  leftovers, #285) in ``whole-suite``: degrade to
  ``module-dir`` once and say so in the record; when ``module-dir`` itself cannot be probed, or
  would run the same files again, or selects no test file at all, or none of its tests passes on
  the base, the task is UNCOMPARABLE (``P2P_BASE_PROBE_UNUSABLE``, #234: no evidence, never
  verified; a degraded probe never turns into "nothing to run"). A probe that worked but found no
  base-passing test is only recorded (``targets: 0``);
* the candidate run times out: its observation is not a pass, so Core says not verified;
* the candidate run fails only because a test failed (``VerifierRun.plain_failure``: no timeout,
  mutated checkout, surviving process or oversized log, and the guard reported a failed test):
  it is re-run once (flaky rule). A pass on the re-run is what Core sees, with the first run's
  failing tests and integrity signals recorded in the rule (``rerun``) and ``p2p_rerun_passed``
  set, so a clean pass and a pass after re-run can be told apart (the verdict is the same); a
  second failure is what Core sees, so not verified. Any other failed run blocks as it is,
  without a re-run;
* the p2p verifier id clashes with an author verifier: REFUSED.

Before the worker starts (or, in a verify call without deferred tests, before the candidate's
verifiers), ``base_env_check`` probes the ``module-dir`` files on the unchanged base the same way; a
probe failure there is UNCOMPARABLE ``BASE_ENV_FAILED`` (environment, #234, #259). In a verify call
without deferred tests, this result applies only if the candidate also fails its own verifiers.

``p2p_time_budget_seconds`` bounds each run (probe, degraded probe, candidate run, re-run).
The rule actually used is a dict (``P2PPlan.rule``): it goes into the p2p observation's artifact
hash (so Core's record binds it), into ``p2p-selection.json`` beside the verifier log, and into the
task result. Core's notarisation contract is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from posixpath import basename

import cairnwork_sandbox
from cairnwork.deferred_tests import BaseUncomparable, Comparison, TestCommitRejected
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.issue_task import test_target
from cairnwork.p2p_selection import DEFAULT_BUDGET_SECONDS, select_p2p_targets
from cairnwork.verifiers import VerifierRun, VerifierSpec, run_verifiers


class P2PRejected(TestCommitRejected):
    """The P2P step could not run safely: the task is REFUSED, never verified."""


@dataclass(frozen=True)
class P2PPlan:
    rule: dict
    spec: VerifierSpec | None  # None: nothing to run (mode off, or no base-passing test)
    timeout: int = DEFAULT_BUDGET_SECONDS

    @property
    def note(self) -> str:
        """What is bound into the observation's artifact hash."""
        return json.dumps(self.rule, sort_keys=True)


def _base_tests(task, exclude: frozenset[str]) -> tuple[str, ...]:
    """Test files (test_*.py, *_test.py) under the roots on the base that are plain targets."""
    try:
        raw = run_git(task.repo, "ls-tree", "-r", "-z", "--name-only", task.base_commit,
                      "--", *task.test_roots)  # fmt: skip
    except GitCommandError as exc:
        raise P2PRejected("P2P_BASE_UNREADABLE", str(exc)) from exc
    target = test_target(task.test_roots)
    return tuple(
        sorted(
            p for p in raw.split("\0")
            if p and p not in exclude and target.fullmatch(p)
            and (basename(p).startswith("test_") or p.endswith("_test.py"))
        )
    )  # fmt: skip


def _probe(task, files, run_root, result, verifying, budget, tag="p"):
    """Per-test outcomes of ``files`` on the base, or (None, reason) when the run is unusable."""
    runs = run_verifiers(
        task.repo, task.base_commit, (VerifierSpec("p2p-probe", files),), run_root,
        f"{result.attempt_id}-{tag}", attempt_id=result.attempt_id, account=verifying[0],
        python=verifying[1], base_commit=task.base_commit, timeout=budget,
        import_paths=task.import_paths,
    )  # fmt: skip
    for run in runs:  # base evidence is not for Core
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    run = runs[0]
    if run.timed_out:
        return None, "base-probe-timeout"
    if run.report_too_big:
        return None, "base-probe-report-over-cap"
    if run.survivors:  # the base leaves a process (or cannot tell): the candidate run would too
        return None, "base-probe-survivors"
    if run.mutated:  # the base's own tests write into the checkout: same for the candidate
        return None, "base-probe-mutated"
    if run.returncode not in (0, 1) or not run.outcomes:
        return None, "base-probe-failed"
    return run.outcomes, ""


def _passing_targets(outcomes: dict[str, str]) -> tuple[str, ...]:
    """A file whose every test passed on the base is a target by itself; in a file with other
    outcomes only the test functions (all parametrisations passed) are."""
    by_file: dict[str, dict[str, bool]] = {}
    for nodeid, outcome in outcomes.items():
        path = nodeid.split("::", 1)[0]
        func = nodeid.split("[", 1)[0]
        funcs = by_file.setdefault(path, {})
        funcs[func] = funcs.get(func, True) and outcome == "passed"
    targets: list[str] = []
    for path, funcs in sorted(by_file.items()):
        if all(funcs.values()):
            targets.append(path)
        else:
            targets.extend(sorted(f for f, ok in funcs.items() if ok))
    return tuple(targets)


def plan_p2p(task, specs, test_files, mode, budget, run_root, result, verifying) -> P2PPlan:
    """Select and probe; see the module docstring. ``test_files`` (the deferred tests) are never
    P2P tests: the author's verifiers cover them."""
    rule: dict = {"configured": mode, "applied": mode, "budget_seconds": budget, "degraded": ""}
    if mode == "off":
        return P2PPlan(rule, None, budget)
    existing = _base_tests(task, frozenset(test_files))
    tried: tuple[str, ...] | None = None
    for applied in (mode, "module-dir") if mode == "whole-suite" else (mode,):
        files = select_p2p_targets(task.allowed_files, existing, task.test_roots, applied)
        rule |= {"applied": applied, "selected_files": len(files)}
        if not files and rule["degraded"]:  # #117 F1: a failed probe is not "nothing to run"
            raise _unusable(rule, "no module-dir test to fall back on")
        if not files:
            return P2PPlan(rule | {"targets": 0}, None, budget)
        if files == tried:  # the narrower mode would run the same files again
            raise _unusable(rule, "module-dir selects the same files")
        outcomes, why = _probe(task, files, run_root, result, verifying, budget)
        if outcomes is not None:
            plan = _plan(task, specs, rule, _passing_targets(outcomes), budget)
            if plan.spec is None and rule["degraded"]:  # G1: and none passes on the base
                raise _unusable(rule, "no module-dir test passes on the base")
            return plan
        rule["degraded"], tried = why, files
    raise _unusable(rule, f"in {rule['applied']} mode")


def _unusable(rule: dict, why: str) -> BaseUncomparable:
    """A failed base probe: no evidence about the base, so UNCOMPARABLE, not a refusal (#234)."""
    detail = f"{rule['degraded']}; {why}"
    return BaseUncomparable(Comparison("UNCOMPARABLE", "P2P_BASE_PROBE_UNUSABLE", detail))


def _plan(task, specs, rule, targets, budget) -> P2PPlan:
    digest = hashlib.sha256("\n".join(targets).encode()).hexdigest()
    rule = rule | {"targets": len(targets), "targets_sha256": digest}
    if not targets:
        return P2PPlan(rule, None, budget)
    verifier_id = f"p2p-{rule['applied']}"
    if verifier_id in {spec.verifier_id for spec in specs}:
        raise P2PRejected("P2P_VERIFIER_ID_CLASH", verifier_id)
    return P2PPlan(rule, VerifierSpec(verifier_id, targets), budget)


def run_plan(
    plan: P2PPlan, task, judged: str, run_root, result, verifying
) -> tuple[tuple[VerifierRun, ...], dict]:
    """The base-passing tests on the judged commit, and the rule actually used. A run that fails
    without timing out is re-run once; Core sees the re-run, whose note records the first run's
    failures (``rerun``), so a flaky pass is not blocking but is bound into the record."""
    if plan.spec is None:
        return (), plan.rule
    run = _run_once(plan.spec, plan, plan.note, task, judged, run_root, result, verifying, "q")
    if run.observation.passed or not run.plain_failure:  # timeout, mutation, survivors...: block
        return (run,), plan.rule
    failing = sorted(_clean(n) for n, outcome in run.outcomes.items() if outcome != "passed")
    first = {"failed": failing, "returncode": run.returncode, "timed_out": run.timed_out,
             "mutated": list(run.mutated), "survivors": run.survivors, "ran": run.ran}  # fmt: skip
    rule = plan.rule | {"rerun": first}
    note = json.dumps(rule, sort_keys=True)
    run = _run_once(plan.spec, plan, note, task, judged, run_root, result, verifying, "r")
    # Not in the note: the flag is the re-run's outcome, which the note is hashed before.
    return (run,), rule | {"p2p_rerun_passed": bool(run.observation.passed)}


def _clean(name: str) -> str:
    """A test id for the record: printable, bounded."""
    return "".join(c if c.isprintable() else "?" for c in str(name))[:200]


def _run_once(spec, plan, note, task, judged, run_root, result, verifying, tag) -> VerifierRun:
    (run,) = run_verifiers(
        task.repo, judged, (spec,), run_root, f"{result.attempt_id}-{tag}",
        attempt_id=result.attempt_id, account=verifying[0], python=verifying[1],
        base_commit=task.base_commit, timeout=plan.timeout, note=note,
        import_paths=task.import_paths,
    )  # fmt: skip
    try:
        (Path(run.log_path).parent / "p2p-selection.json").write_text(note)
    except OSError:
        pass  # the hash already binds the rule
    return run


ENV_FAILED = "BASE_ENV_FAILED"


def base_env_check(
    task, run_root, result, verifying, *, without_tests=False, key_repo=None
) -> None:
    """Before the worker starts (#234): can the unchanged base run its own existing tests at all?
    For a task that will be probed after the worker (deferred tests, P2P not off), or with
    ``without_tests`` a verify call without deferred tests (#259; ``key_repo``: the repository it
    was cloned from, which keys the marker in place of ``task.repo``), the module-dir selection of
    existing base tests is probed now, the same way (``_probe``): a timeout, a report over the cap,
    an exit other than 0 or 1, or no guard record raises UNCOMPARABLE ``BASE_ENV_FAILED``. On a
    dispatch, this means no worker quota is spent on a base that gives no evidence and the attempt
    is not counted against the worker. Failing tests are fine (the base has the bug). Nothing from
    the test commit is read. A pass is remembered per repository, base, files, import paths, budget,
    verifier account and interpreter (``_env_fingerprint``) as a regular file in
    ``base-env-ok/`` in the run root,
    so only the first check of that base pays; the marker can only skip this check, never make
    anything pass. On a dispatch the probes after the worker run as before; a verify call without
    deferred tests has no later base run, so an environment that broke after a recorded pass in a
    way the key misses (sandbox backend, system libraries, the account's home) shows up there as
    a candidate failure again, as before #259."""
    if task.p2p_mode == "off" or not (task.test_commit or without_tests):
        return
    try:
        existing = _base_tests(task, frozenset())
    except P2PRejected as exc:
        raise _env_failed(str(exc)) from exc
    files = select_p2p_targets(task.allowed_files, existing, task.test_roots, "module-dir")
    if not files:  # nothing to probe now; the steps after the worker decide, as before
        return
    python = verifying[1] or getattr(verifying[0], "python", None) or sys.executable
    account = getattr(verifying[0], "user", "")
    fingerprint = _env_fingerprint(python)  # None: something unreadable, no marker either way
    repo = task.repo if key_repo is None else key_repo
    key = [str(Path(repo).resolve()), task.base_commit, files, task.import_paths,
           task.p2p_budget, python, account, fingerprint]  # fmt: skip
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    marker = Path(run_root) / "base-env-ok" / digest if fingerprint is not None else None
    if marker is not None and _is_pass_marker(marker):
        return
    outcomes, why = _probe(task, files, run_root, result, verifying, task.p2p_budget, tag="e")
    if outcomes is None:
        # #262: name the cause when no sandbox backend exists
        if why == "base-probe-failed" and cairnwork_sandbox.select() is None:
            why += " (no-sandbox: no usable sandbox backend, so nothing ran)"
        raise _env_failed(f"{why}: {len(files)} existing test file(s) near the change")
    if marker is None:
        return
    try:  # an unwritten marker only means the next dispatch probes again
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
    except OSError:
        pass


def _is_pass_marker(marker: Path) -> bool:
    """Only a regular file of ours counts: not a symlink (dangling or not), directory or another
    uid's file. Anything else, or an error, means probe again."""
    try:
        info = marker.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()


class _Unreadable(Exception):
    """A part of the interpreter's fingerprint could not be read: no marker is read or written."""


def _stat(path: str) -> list:
    try:
        info = os.stat(path)
    except OSError as exc:  # e.g. under the verifier account's home
        raise _Unreadable(path) from exc
    return [info.st_mtime_ns, info.st_size]


def _small_file(path: str) -> list:
    """A regular file's first 4 KiB and stat, or "absent"; anything else (a FIFO, a symlink, even
    a dangling one, a directory, no permission) is unreadable. Opened non-blocking, never
    following a link."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return ["absent"]
    except OSError as exc:
        raise _Unreadable(path) from exc
    if not stat.S_ISREG(info.st_mode):
        raise _Unreadable(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:  # swapped for something else after the lstat: still not a regular file
            opened = os.fstat(fd)  # the metadata of the file actually read
            if not stat.S_ISREG(opened.st_mode):
                raise _Unreadable(path)
            data = os.read(fd, 4096)
        finally:
            os.close(fd)
    except OSError as exc:
        raise _Unreadable(path) from exc
    return [data.decode(errors="replace"), opened.st_mtime_ns, opened.st_size]


def _sites(venv: str) -> list:
    """Each lib/python*/site-packages and its stat (its mtime changes when a package is added or
    removed); a lib directory or site-packages that exists but cannot be read is unreadable."""
    lib = os.path.join(venv, "lib")
    try:
        names = sorted(n for n in os.listdir(lib) if n.startswith("python"))
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise _Unreadable(lib) from exc
    out = []
    for name in names:
        site = os.path.join(lib, name, "site-packages")
        if os.path.lexists(site) or not os.access(os.path.join(lib, name), os.X_OK):
            out.append([site, _stat(site)])
    return out


def _env_fingerprint(python: str) -> list | None:
    """What the verifier interpreter is, by stat and small reads only (it is never run here): the
    real binary, the venv's pyvenv.cfg (its text holds the version) and each site-packages
    directory; for Cairnwork's own interpreter also sys.version and the real sys.prefix. None when
    any part cannot be read: then no marker is read or written, and every dispatch probes."""
    venv = os.path.dirname(os.path.dirname(os.path.abspath(python)))
    real = os.path.realpath(python)
    try:
        parts = [real, _stat(real), _small_file(os.path.join(venv, "pyvenv.cfg")), _sites(venv)]
    except _Unreadable:
        return None
    own = (
        [sys.version, os.path.realpath(sys.prefix)]
        if real == os.path.realpath(sys.executable)
        else []
    )
    return [*parts, own]


def _env_failed(detail: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", ENV_FAILED, f"unchanged base: {detail}"))


def p2p_for(task, specs, test_files, run_root, result, verifying) -> P2PPlan | None:
    """The P2P plan of a task with deferred tests (p2p.py), computed once the worker is done."""
    if not task.test_commit:
        return None
    return plan_p2p(
        task, specs, test_files, task.p2p_mode, task.p2p_budget, run_root, result, verifying
    )
