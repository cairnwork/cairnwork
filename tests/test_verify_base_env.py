"""#259: a verify call without deferred tests runs the verifiers on the candidate alone, so a base
whose own tests cannot run used to show up as a candidate failure. It now runs the same base check
as a dispatch (p2p.base_env_check) before the candidate's verifiers: when the candidate then fails,
a failed check is UNCOMPARABLE ``BASE_ENV_FAILED`` with Core never asked; a candidate that passes
keeps its VERIFIED (it may be the fix for a base that crashed at import). A pass is remembered per
base. The ``probes`` tests stub only the ``-e`` probe; the import-crash tests stub nothing."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import commit_change
from test_base_env import KEY_TASK, OK, VARIANTS, _venv
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_uncomparable_paths import PROBE_ROWS, _run, assert_uncomparable
from verify_helpers import FIX, base_of, candidate, in_process

from cairnwork import p2p
from cairnwork.intake import Intake
from cairnwork.notary import Notary
from cairnwork.pipeline import TaskResult
from cairnwork.verifier_account import VerifierAccountError
from cairnwork.verify_candidate import verify_candidate

sandbox = pytest.mark.sandbox  # the candidate's own verifiers run for real
WRONG = "def add(a, b):\n    return a + b + 2\n"  # still fails tests/test_calc.py
NEAR = ("tests/test_calc.py", "tests/test_skip.py")  # the module-dir files of src/calc.py


@pytest.fixture
def probes(monkeypatch):
    """probes(fields): the ``-e`` probe answers fields (or raises it, if an exception); every
    other run, and Core, is real. Returns the ``-e`` calls (commit, targets) and the Core calls."""
    real_run, real_notarize, seen, core = p2p.run_verifiers, Notary.notarize, [], []

    def notarize(self, *args, **kwargs):
        core.append(args)
        return real_notarize(self, *args, **kwargs)

    def install(fields):
        def fake(repo, commit, specs, run_root, run_id, **kw):
            if not run_id.endswith("-e"):
                return real_run(repo, commit, specs, run_root, run_id, **kw)
            seen.append((commit, tuple(spec.targets for spec in specs)))
            if isinstance(fields, Exception):
                raise fields
            return tuple(_run(spec, run_root, run_id, **fields) for spec in specs)

        monkeypatch.setattr(p2p, "run_verifiers", fake)
        monkeypatch.setattr(Notary, "notarize", notarize)
        return seen, core

    return install


def _fixed(project, base=None):
    return candidate(project, base or base_of(project), {"src/calc.py": FIX})


def _wrong(project, base=None):
    return candidate(project, base or base_of(project), {"src/calc.py": WRONG})


@sandbox
@pytest.mark.parametrize("probe", PROBE_ROWS)
def test_a_failing_candidate_on_a_base_that_cannot_run_its_tests_is_uncomparable(
    project, state, probes, probe
):
    seen, core = probes(probe)
    out = in_process(project, state, base_of(project), _wrong(project))
    assert_uncomparable(out, state, core, "BASE_ENV_FAILED")
    assert seen == [(base_of(project), (NEAR,))]
    assert out["logs"]  # the candidate's failing runs are still there to read


@sandbox
@pytest.mark.parametrize("probe", PROBE_ROWS)
def test_a_passing_candidate_keeps_its_verified_whatever_the_base_check_said(
    project, state, probes, probe
):
    seen, core = probes(probe)
    assert in_process(project, state, base_of(project), _fixed(project))["status"] == "VERIFIED"
    assert seen == [(base_of(project), (NEAR,))] and len(core) == 1


@sandbox
def test_a_base_that_can_run_them_leaves_a_failure_failed_and_is_probed_once(
    project, state, probes
):
    seen, core = probes(OK)
    cand = _wrong(project)
    for _ in range(2):
        out = in_process(project, state, base_of(project), cand)
        assert out["status"] == "FAILED" and out["core_status"]  # Core was asked
    assert len(seen) == 1 and seen[0][0] == base_of(project) and len(core) == 2


@sandbox
def test_with_p2p_off_nothing_is_probed(project, state, probes):
    config = state / "config.json"
    config.write_text(json.dumps({**json.loads(config.read_text()), "p2p_selection": "off"}))
    seen, _ = probes({"rc": 2})
    assert in_process(project, state, base_of(project), _wrong(project))["status"] == "FAILED"
    assert seen == []


@sandbox
def test_a_new_base_is_probed_again(project, state, probes):
    seen, _ = probes(OK)
    first = base_of(project)
    assert in_process(project, state, first, _wrong(project))["status"] == "FAILED"
    second = commit_change(project, {"README.md": "a later main\n"})
    assert in_process(project, state, second, _wrong(project, second))["status"] == "FAILED"
    assert [commit for commit, _ in seen] == [first, second]


@sandbox
def test_an_account_failure_in_the_base_check_is_no_evidence(project, state, probes):
    """The check runs before any candidate code, inside base_stage: an unusable account there is
    UNCOMPARABLE, not an internal error."""
    seen, core = probes(VerifierAccountError("account lock not available"))
    out = in_process(project, state, base_of(project), _wrong(project))
    assert_uncomparable(out, state, core, "VERIFIER_ACCOUNT_UNUSABLE")
    assert len(seen) == 1


@sandbox
def test_a_scope_refusal_wins_over_a_broken_base_and_probes_nothing(project, state, probes):
    seen, core = probes({"rc": 2})
    files = {"src/calc.py": FIX, "README.x": "not allowed\n"}
    out = in_process(project, state, base_of(project), candidate(project, base_of(project), files))
    assert out["status"] == "REFUSED" and "OUT_OF_SCOPE" in out["reason"]
    assert seen == [] and core == []


@sandbox
def test_with_deferred_tests_the_base_runs_decide_and_nothing_extra_is_probed(
    project, state, probes
):
    """Their base runs come before any candidate code already, so this check stays off there."""
    seen, _ = probes({"rc": 2})
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    verify_candidate(
        Intake(state), repo=project, base=base, candidate=_fixed(project, base),
        allowed_files=("src/calc.py",), verifiers=CHECK, test_commit=test_commit,
    )  # fmt: skip
    assert seen == []


CRASH = "import nonexistent_xyz\ndef add(a, b):\n    return a + b + 1\n"  # the base cannot import


@sandbox
def test_a_candidate_that_fixes_a_base_crashing_at_import_is_verified(project, state):
    base = commit_change(project, {"src/calc.py": CRASH})
    assert in_process(project, state, base, _fixed(project, base))["status"] == "VERIFIED"


@sandbox
def test_a_candidate_that_leaves_the_import_crash_is_uncomparable(project, state):
    """Nothing stubbed. A base whose nearby tests cannot even be collected gives no way to tell a
    broken environment from broken code, so a candidate that still fails there is UNCOMPARABLE
    (docs/ISSUE_INTAKE.md), not FAILED; Core is not asked."""
    base = commit_change(project, {"src/calc.py": CRASH})
    bad = candidate(project, base, {"src/calc.py": CRASH.replace("+ 1", "+ 2")})
    out = in_process(project, state, base, bad)
    assert out["status"] == "UNCOMPARABLE" and out["uncomparable"]["code"] == "BASE_ENV_FAILED"
    assert out["core_status"] == ""


VERIFY_TASK = replace(KEY_TASK, test_commit="")  # a verify call without deferred tests
SOURCE = Path("/nonexistent/mirror")


@pytest.fixture
def unit(monkeypatch, tmp_path):
    """check(task, verifying, key_repo): one check as the verify path calls it; probes so far."""
    probed: list = []
    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: ("tests/test_calc.py",))
    monkeypatch.setattr(
        p2p, "_probe", lambda *a, **k: (probed.append(a[0]), ({"t": "passed"}, ""))[1]
    )

    def check(task=VERIFY_TASK, verifying=(None, None), key_repo=SOURCE, without_tests=True):
        result = TaskResult("t", "a" + "0" * 16, "FAILED")
        p2p.base_env_check(
            task,
            tmp_path / "runs",
            result,
            verifying,
            without_tests=without_tests,
            key_repo=key_repo,
        )
        return len(probed)

    return check


KEYED = [pytest.param(*p.values, SOURCE, id=p.id) for p in VARIANTS if p.id != "repo"] + [
    pytest.param(lambda t, v, tmp: (t, v), Path("/nonexistent/other-mirror"), id="source-repo"),
]


@pytest.mark.parametrize(("vary", "source"), KEYED)
def test_each_key_field_still_gets_a_fresh_probe(unit, tmp_path, vary, source):
    """The same key as a dispatch, with the source repository in place of the scratch clone."""
    verifying = (SimpleNamespace(user="cairnwork-verifier", python=None), _venv(tmp_path / "venv"))
    assert unit(VERIFY_TASK, verifying) == 1 and unit(VERIFY_TASK, verifying) == 1
    task, changed = vary(VERIFY_TASK, verifying, tmp_path)
    assert unit(task, changed, source) == 2


def test_another_scratch_clone_of_the_same_source_is_remembered(unit):
    assert unit() == 1
    assert unit(replace(VERIFY_TASK, repo=Path("/nonexistent/scratch-2"))) == 1


def test_a_changed_venv_gets_a_fresh_probe(unit, tmp_path):
    """Same interpreter path, new package installed: the environment fingerprint is in the key."""
    verifying = (None, _venv(tmp_path / "venv"))
    assert unit(VERIFY_TASK, verifying) == 1 and unit(VERIFY_TASK, verifying) == 1
    (tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "newpkg").mkdir()
    assert unit(VERIFY_TASK, verifying) == 2


def test_a_key_repo_alone_does_not_turn_the_check_on(unit):
    """Only without_tests switches it on; key_repo only keys the marker."""
    assert unit(without_tests=False) == 0
    assert unit(key_repo=None) == 1  # and without key_repo it keys on task.repo
