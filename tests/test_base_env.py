"""#234 (provenance audit item 3): before the first dispatch to a base, the base's own existing
tests near the change must be able to run there. If they cannot, the attempt ends before the
worker starts, as UNCOMPARABLE ``BASE_ENV_FAILED``: environment, not the worker. The stubbed
rows replace only the ``-e`` probe; the sandbox test runs it for real."""

import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_pipeline import EditWorker
from test_uncomparable_paths import PROBE_ROWS, _account_error, _run
from verify_helpers import FIX

from cairnwork import p2p, recovery
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.journal import Journal
from cairnwork.notary import Notary
from cairnwork.ownership import OwnershipLedger
from cairnwork.p2p import P2PRejected
from cairnwork.pipeline import TaskResult, TaskSpec, run_task

OK = {"rc": 1, "outcomes": {"tests/test_calc.py::test_add": "failed"}}  # the bug: still fine


@pytest.fixture
def probes(monkeypatch):
    """probes(fields): fake the ``-e`` probe with fields (a callable raises); others run for real.
    Returns the list of ``-e`` calls (their commit) and the list of Core calls."""
    core: list = []
    monkeypatch.setattr(Notary, "notarize", lambda self, *a, **k: core.append(a))
    real = p2p.run_verifiers
    seen: list = []

    def install(fields):
        def fake(repo, commit, specs, run_root, run_id, **kw):
            if not run_id.endswith("-e"):
                return real(repo, commit, specs, run_root, run_id, **kw)
            seen.append((commit, tuple(spec.targets for spec in specs)))
            if callable(fields):
                return fields()
            return tuple(_run(spec, run_root, run_id, **fields) for spec in specs)

        monkeypatch.setattr(p2p, "run_verifiers", fake)
        return seen, core

    return install


class Recording(EditWorker):
    def __init__(self, files):
        super().__init__(files)
        self.ran = 0

    def run(self, workdir: Path, task) -> None:
        self.ran += 1
        super().run(workdir, task)


def dispatch(project, root, *, key="k1", base=None, mode="whole-suite", tests=True, worker=None):
    tests_file = {"tests/test_regression.py": REGRESSION + f"# {key}\n"}  # a new commit per key
    head, test_commit = make_test_commit(project, tests_file)
    base = base or head
    root.mkdir(exist_ok=True)
    task = TaskSpec(key, f"card-{key}", "fix add", "add() is off by one", project, base,
                    ("src/calc.py",), CHECK, test_commit=test_commit if tests else "",
                    p2p_mode=mode)  # fmt: skip
    worker = worker or Recording({"src/calc.py": FIX})
    result = run_task(
        task, ledger=OwnershipLedger(root / "own.sqlite"), notary=Notary(root / "notary.sqlite"),
        journal=Journal(root / "journal.sqlite"), worker=worker, run_root=root / "runs",
    )  # fmt: skip
    phases = [e.phase for e in Journal(root / "journal.sqlite").entries(result.task_id)]
    return result, worker, phases


def assert_env_failed(result, worker, phases, core, code="BASE_ENV_FAILED"):
    assert result.status == "FAILED" and not result.verified and core == []
    assert result.reason.startswith(f"UNCOMPARABLE: {code}: "), result.reason
    assert worker.ran == 0 and "WORKER_STARTED" not in phases and phases[-1] == "FINISHED"


@pytest.mark.parametrize("probe", PROBE_ROWS)
def test_a_base_that_cannot_run_its_tests_ends_before_the_worker(project, tmp_path, probes, probe):
    seen, core = probes(probe)
    result, worker, phases = dispatch(project, tmp_path / "s")
    assert_env_failed(result, worker, phases, core)
    assert "unchanged base: " in result.reason and len(seen) == 1
    assert seen[0][0] == git(project, "rev-parse", "HEAD")  # the base, not the test commit
    assert not (tmp_path / "s" / "runs" / "base-env-ok").exists()  # a failure is not remembered


def test_a_same_uid_worker_is_not_blamed_either(project, tmp_path, probes):
    # nothing has run yet, so the isolation rule for later base runs does not apply
    _, core = probes(PROBE_ROWS[2].values[0])
    result, worker, phases = dispatch(project, tmp_path / "s")
    assert_env_failed(result, worker, phases, core)


def test_an_account_failure_in_the_env_probe_is_the_account(project, tmp_path, probes):
    _, core = probes(_account_error)
    result, worker, phases = dispatch(project, tmp_path / "s")
    assert_env_failed(result, worker, phases, core, "VERIFIER_ACCOUNT_UNUSABLE")


def test_a_pass_is_remembered_per_base_and_a_failure_is_not(project, tmp_path, probes):
    seen, _ = probes(OK)  # failing tests on the base are the bug, not the environment
    first, worker, phases = dispatch(project, tmp_path / "s")
    assert worker.ran == 1 and "WORKER_STARTED" in phases and "BASE_ENV" not in first.reason
    base = seen[0][0]
    _, worker, _ = dispatch(project, tmp_path / "s", key="k2", base=base)
    assert len(seen) == 1 and worker.ran == 1  # the same base: not probed again
    commit_change(project, {"src/other.py": "z = 1\n"})
    git(project, "push", "-q", "origin", "HEAD:refs/heads/next")  # the authors' clones see it
    dispatch(project, tmp_path / "s", key="k3")
    assert len(seen) == 2 and seen[1][0] != base  # a new base is probed


def test_after_a_failure_the_next_dispatch_probes_again(project, tmp_path, probes):
    seen, _ = probes(PROBE_ROWS[0].values[0])
    dispatch(project, tmp_path / "s")
    base = seen[0][0]
    again, _, _ = dispatch(project, tmp_path / "s", key="k2", base=base)
    assert len(seen) == 2 and "BASE_ENV_FAILED" in again.reason


@pytest.mark.parametrize(("mode", "tests"), [("off", True), ("whole-suite", False)])
def test_no_env_probe_without_a_p2p_probe_after_the_worker(project, tmp_path, probes, mode, tests):
    seen, _ = probes(PROBE_ROWS[0].values[0])
    _, worker, _ = dispatch(project, tmp_path / "s", mode=mode, tests=tests)
    assert seen == [] and worker.ran == 1


def test_no_existing_test_near_the_change_probes_nothing(repo, tmp_path, probes):
    commit_change(repo, {"src/calc.py": "def add(a, b):\n    return a + b + 1\n"})
    seen, _ = probes(PROBE_ROWS[0].values[0])
    _, worker, _ = dispatch(repo, tmp_path / "s")
    assert seen == [] and worker.ran == 1


def test_the_probe_runs_the_module_dir_files_of_the_base(project, tmp_path, probes):
    seen, _ = probes(OK)
    dispatch(project, tmp_path / "s")
    assert seen[0][1] == (("tests/test_calc.py", "tests/test_skip.py"),)


def test_recovery_sweeps_a_crashed_env_probe(tmp_path):
    attempt = "a" + "0" * 16
    (tmp_path / f"{attempt}-e-v0").mkdir()
    (tmp_path / f"{attempt}-e-v0.priv").mkdir()
    _, privates, clones = recovery._run_dirs(tmp_path, attempt)
    assert [p.name for p in clones] == [f"{attempt}-e-v0"]
    assert [p.name for p in privates] == [f"{attempt}-e-v0.priv"]  # all of it goes


SLEEP = "import time\ndef test_slow():\n    time.sleep(60)\n"


@pytest.mark.sandbox
def test_a_real_base_that_hangs_ends_before_the_worker(project, tmp_path):
    commit_change(project, {"tests/test_slow.py": SLEEP})
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    root = tmp_path / "s"
    root.mkdir()
    worker = Recording({"src/calc.py": FIX})
    task = TaskSpec("k1", "card", "fix add", "add() is off by one", project, base,
                    ("src/calc.py",), CHECK, test_commit=test_commit,
                    p2p_mode="module-dir", p2p_budget=5)  # fmt: skip
    result = run_task(
        task, ledger=OwnershipLedger(root / "own.sqlite"), notary=Notary(root / "notary.sqlite"),
        journal=Journal(root / "journal.sqlite"), worker=worker, run_root=root / "runs",
    )  # fmt: skip
    phases = [e.phase for e in Journal(root / "journal.sqlite").entries(result.task_id)]
    assert_env_failed(result, worker, phases, [])
    assert "base-probe-timeout" in result.reason
    assert OwnershipLedger(root / "own.sqlite").get(task_id=result.task_id).status == "FAILED"
    assert [p.name for p in (root / "runs").iterdir() if "-e-" in p.name] == []  # nothing left


# Unit tests of the pass marker and its key (review of #258): only ``_probe`` is faked.

KEY_TASK = TaskSpec("k", "c", "t", "i", Path("/nonexistent/repo"), "1" * 40, ("src/calc.py",),
                    CHECK, test_commit="2" * 40, p2p_mode="module-dir")  # fmt: skip


@pytest.fixture
def unit(monkeypatch, tmp_path):
    """check(task, verifying): one base_env_check; returns how many probes ran in total."""
    probed: list = []
    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: ("tests/test_calc.py",))
    monkeypatch.setattr(
        p2p, "_probe", lambda *a, **k: (probed.append(a[0]), ({"t": "passed"}, ""))[1]
    )

    def check(task=KEY_TASK, verifying=(None, None)):
        p2p.base_env_check(
            task, tmp_path / "runs", TaskResult("t", "a" + "0" * 16, "FAILED"), verifying
        )
        return len(probed)

    return check


def _venv(root: Path, version="3.12.1") -> str:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "python3.12" / "site-packages").mkdir(parents=True, exist_ok=True)
    (root / "pyvenv.cfg").write_text(f"version = {version}\n")
    python = root / "bin" / "python"
    python.write_text("")
    return str(python)


def _alias(python: str) -> str:
    """Another name for the same interpreter in the same venv: only the python field differs."""
    alias = Path(python).with_name("python3")
    alias.symlink_to(python)
    return str(alias)


VARIANTS = [
    pytest.param(lambda t, v, tmp: (replace(t, repo=Path("/nonexistent/other")), v), id="repo"),
    pytest.param(lambda t, v, tmp: (replace(t, base_commit="3" * 40), v), id="base"),
    pytest.param(lambda t, v, tmp: (replace(t, import_paths=("src",)), v), id="import_paths"),
    pytest.param(lambda t, v, tmp: (replace(t, p2p_budget=7), v), id="budget"),
    pytest.param(lambda t, v, tmp: (t, (v[0], _venv(tmp / "venv2"))), id="python-other-venv"),
    pytest.param(lambda t, v, tmp: (t, (v[0], _alias(v[1]))), id="python-same-file"),
    pytest.param(
        lambda t, v, tmp: (t, (SimpleNamespace(user="other", python=v[1]), v[1])), id="account"
    ),
]


@pytest.mark.parametrize("vary", VARIANTS)
def test_each_key_field_gets_a_fresh_probe(unit, tmp_path, vary):
    verifying = (SimpleNamespace(user="cairnwork-verifier", python=None), _venv(tmp_path / "venv"))
    assert unit(KEY_TASK, verifying) == 1 and unit(KEY_TASK, verifying) == 1  # remembered
    task, changed = vary(KEY_TASK, verifying, tmp_path)
    assert unit(task, changed) == 2


def test_other_files_near_the_change_get_a_fresh_probe(unit, monkeypatch):
    assert unit() == 1 and unit() == 1
    monkeypatch.setattr(
        p2p, "_base_tests", lambda task, exclude: ("tests/test_calc.py", "tests/test_x.py")
    )
    assert unit() == 2


@pytest.mark.parametrize("change", ["site-packages", "pyvenv.cfg", "binary"])
def test_a_changed_interpreter_or_venv_gets_a_fresh_probe(unit, tmp_path, change):
    python = _venv(tmp_path / "venv")
    assert unit(KEY_TASK, (None, python)) == 1 and unit(KEY_TASK, (None, python)) == 1
    if change == "site-packages":
        (tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "newpkg").mkdir()
    elif change == "pyvenv.cfg":  # same size and mtime: only the version text differs
        cfg = tmp_path / "venv" / "pyvenv.cfg"
        before = cfg.stat()
        cfg.write_text("version = 3.12.2\n")
        os.utime(cfg, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        Path(python).write_text("rebuilt")
    assert unit(KEY_TASK, (None, python)) == 2


def test_cairnworks_own_interpreter_is_keyed_by_version_and_prefix(monkeypatch):
    base = p2p._env_fingerprint(sys.executable)
    monkeypatch.setattr(sys, "version", sys.version + " changed")
    assert p2p._env_fingerprint(sys.executable) != base


@pytest.mark.parametrize("kind", ["dangling-symlink", "symlink-to-file", "directory"])
def test_only_a_regular_file_of_ours_counts_as_a_pass(unit, tmp_path, kind):
    assert unit() == 1
    (marker,) = (tmp_path / "runs" / "base-env-ok").iterdir()
    marker.unlink()
    if kind == "directory":
        marker.mkdir()
    else:
        target = tmp_path / ("missing" if kind == "dangling-symlink" else "file")
        if kind == "symlink-to-file":
            target.write_text("")
        marker.symlink_to(target)
    assert unit() == 2


def test_a_marker_owned_by_another_uid_is_not_a_pass(unit, tmp_path, monkeypatch):
    assert unit() == 1
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 1)  # the file is now "someone else's"
    assert unit() == 2


def test_a_base_whose_test_files_cannot_be_listed_is_a_failure(unit, monkeypatch):
    def refuse(task, exclude):
        raise P2PRejected("P2P_BASE_UNREADABLE", "ls-tree failed")

    monkeypatch.setattr(p2p, "_base_tests", refuse)
    with pytest.raises(BaseUncomparable) as got:
        unit()
    assert got.value.comparison.code == "BASE_ENV_FAILED" and "P2P_BASE_UNREADABLE" in str(
        got.value
    )


def test_an_unreadable_base_is_a_failure_not_a_skip(project, tmp_path, monkeypatch):
    probed: list = []
    monkeypatch.setattr(p2p, "_probe", lambda *a, **k: probed.append(a) or ({"t": "passed"}, ""))
    task = replace(KEY_TASK, repo=project, base_commit="4" * 40)  # not in the repository
    with pytest.raises(BaseUncomparable) as got:
        p2p.base_env_check(
            task, tmp_path / "runs", TaskResult("t", "a" + "0" * 16, "FAILED"), (None, None)
        )
    assert got.value.comparison.code == "BASE_ENV_FAILED" and probed == []


def test_no_sandbox_names_the_cause_and_keeps_the_verdict(project, tmp_path, probes, monkeypatch):
    import cairnwork_sandbox

    seen, core = probes(PROBE_ROWS[2].values[0])  # a real probe failure row, but no backend
    monkeypatch.setattr(cairnwork_sandbox, "select", lambda: None)
    result, worker, phases = dispatch(project, tmp_path / "s")
    assert_env_failed(result, worker, phases, core)
    assert "base-probe-failed (no-sandbox" in result.reason and len(seen) == 1


def test_with_a_sandbox_a_probe_failure_keeps_its_reason(project, tmp_path, probes, monkeypatch):
    import cairnwork_sandbox

    _, core = probes(PROBE_ROWS[2].values[0])
    monkeypatch.setattr(cairnwork_sandbox, "select", lambda: object())  # a backend, on any host
    result, worker, phases = dispatch(project, tmp_path / "s")
    assert_env_failed(result, worker, phases, core)
    assert "no-sandbox" not in result.reason
