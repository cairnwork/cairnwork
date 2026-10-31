"""#478: a fixture that raises after ``yield`` is an error at the teardown of the test that ran
last with it, not of the test that owns it (pytest runs a shared fixture's finalizer at its last
user's teardown). The guard records it on that node and as a teardown error; a base run with one
gives no evidence (UNCOMPARABLE), so the base's error is never charged to a candidate."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import commit_change
from test_p2p import CALC, CONFTEST, FIX_ADD, SIBLING, run
from test_uncomparable_paths import run_cli

from cairnwork import p2p, report_guard
from cairnwork.core_request import VerifierObservation
from cairnwork.deferred_tests import base_comparison
from cairnwork.verifiers import VerifierRun, VerifierSpec

NONCE = "n0nce"
SHARED = (
    "import pytest\n@pytest.fixture(scope='module')\ndef shared():\n    yield 1\n"
    "    raise RuntimeError('teardown')\n"
    "def test_1(shared):\n    assert shared\ndef test_2(shared):\n    assert shared\n"
)
OWN = (
    "import pytest\n@pytest.fixture\ndef own():\n    yield 1\n    raise RuntimeError('teardown')\n"
    "def test_a(own):\n    assert own\ndef test_b():\n    assert True\n"
)


def guard_run(tmp_path, files, *targets):
    """The guard's record of a real pytest run of ``files`` (no sandbox)."""
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    verdict, guard = tmp_path / "verdict.json", tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    env = {
        "PATH": "/usr/bin:/bin", report_guard.PATH_ENV: str(verdict),
        report_guard.NONCE_ENV: NONCE, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
    }  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
         "-p", report_guard.MODULE, *targets],
        cwd=tmp_path, env=env, capture_output=True, check=False,
    )  # fmt: skip
    return done.returncode, report_guard.read_verdict(verdict, NONCE)


def test_a_function_fixture_teardown_error_fails_its_own_test(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_own.py": OWN}, "tests")
    assert rc == 1 and v.recorded and not v.ran
    assert v.outcomes == {
        "tests/test_own.py::test_a": "failed",
        "tests/test_own.py::test_b": "passed",
    }
    assert v.teardown_errors == ("tests/test_own.py::test_a",)


def test_a_shared_fixture_teardown_error_lands_on_its_last_user(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_shared.py": SHARED}, "tests")
    assert rc == 1 and not v.ran
    assert v.outcomes == {"tests/test_shared.py::test_1": "passed",
                          "tests/test_shared.py::test_2": "failed"}  # fmt: skip
    assert v.teardown_errors == ("tests/test_shared.py::test_2",)


def test_a_clean_run_has_no_teardown_errors(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_ok.py": "def test_ok():\n    assert True\n"}, "tests")
    assert rc == 0 and v.ran and v.teardown_errors == ()


def _record(tmp_path, extra):
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps({"nonce": NONCE, "collected": ["t.py::a"], "exitstatus": 0,
                                "testsfailed": 0, "outcomes": {"t.py::a": "passed"}}
                               | extra))  # fmt: skip
    return report_guard.read_verdict(path, NONCE)


def test_a_record_naming_a_teardown_error_is_not_a_pass_even_if_every_outcome_passed(tmp_path):
    v = _record(tmp_path, {"teardown_errors": ["t.py::a"]})
    assert v.recorded and not v.ran and v.teardown_errors == ("t.py::a",)


@pytest.mark.parametrize("bad", [{"x": 1}, [1], "t.py::a"])
def test_malformed_teardown_errors_mean_no_record(tmp_path, bad):
    v = _record(tmp_path, {"teardown_errors": bad})
    assert not v.recorded and not v.ran and v.outcomes == {}


# --- the base runs: P2P probe and deferred tests (stubbed runs) ----------------------------


def probe_run(tmp_path, rc, outcomes, teardown=()):
    spec = VerifierSpec("p2p-probe", ("tests/test_a.py",))
    (tmp_path / "log").mkdir(exist_ok=True)
    seen = VerifierObservation(spec.verifier_id, "x/verifier.log", "sha256:0", rc == 0)
    return VerifierRun(spec, rc, False, (), tmp_path / "log" / "verifier.log", seen, outcomes,
                       recorded=True, teardown_errors=teardown)  # fmt: skip


def probe(monkeypatch, tmp_path, run_):
    from test_p2p_plan import RESULT, TASK, context

    monkeypatch.setattr(p2p, "run_verifiers", lambda *a, **k: (run_,))
    return p2p._probe(TASK, ("tests/test_a.py",), RESULT, context(tmp_path), 30)


A, B = "tests/test_a.py::test_1", "tests/test_a.py::test_2"


def test_a_base_probe_with_a_teardown_error_is_unusable(monkeypatch, tmp_path):
    got = probe(monkeypatch, tmp_path, probe_run(tmp_path, 1, {A: "passed", B: "failed"}, (B,)))
    assert got == (None, "base-probe-teardown-error")  # not (outcomes, ""): test_1 is no target


def test_a_base_probe_exiting_1_with_no_failed_test_is_unusable(monkeypatch, tmp_path):
    got = probe(monkeypatch, tmp_path, probe_run(tmp_path, 1, {A: "passed", B: "passed"}))
    assert got == (None, "base-probe-failed")


@pytest.mark.parametrize(
    ("rc", "outcomes"),
    [(0, {A: "passed", B: "passed"}), (1, {A: "passed", B: "failed"})],
    ids=["all-pass", "a-test-fails"],
)
def test_a_base_probe_without_teardown_errors_is_used_as_before(
    monkeypatch, tmp_path, rc, outcomes
):
    assert probe(monkeypatch, tmp_path, probe_run(tmp_path, rc, outcomes)) == (outcomes, "")


TARGET = "tests/test_regression.py::test_regression"


def base(rc=1, outcomes=None, teardown=()):
    return VerifierRun(
        VerifierSpec("v", ("tests/test_regression.py",)), rc, False, (), Path("x"),
        VerifierObservation("v", "x/verifier.log", "sha256:0", False),
        {TARGET: "failed"} if outcomes is None else outcomes, recorded=True,
        teardown_errors=teardown,
    )  # fmt: skip


@pytest.mark.parametrize(
    "run_",
    [
        pytest.param(lambda: base(teardown=(TARGET,)), id="teeth-and-teardown-error"),
        pytest.param(lambda: base(outcomes={TARGET: "passed"}), id="exit-1-nothing-failed"),
    ],
)
def test_a_deferred_base_run_with_an_unrecorded_or_teardown_failure_is_uncomparable(run_):
    got = base_comparison([run_()], frozenset({TARGET}))
    assert (got.kind, got.code) == ("UNCOMPARABLE", "BASE_PYTEST_ERROR")


def test_a_deferred_base_run_without_teardown_errors_keeps_its_teeth():
    assert base_comparison([base()], frozenset({TARGET})).kind == "TEETH"
    no_report = replace(base(teardown=(TARGET,)), recorded=False, outcomes={})
    assert base_comparison([no_report], frozenset({TARGET})).code == "BASE_NO_REPORT"


# --- real runs (sandbox) --------------------------------------------------------------------


CALC_TEST = "from calc import add\ndef test_add():\n    assert add(2, 3)\n"  # as in test_p2p


@pytest.fixture
def calc_project(repo):
    commit_change(
        repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_sibling.py": SIBLING,
               "tests/test_calc.py": CALC_TEST},
    )  # fmt: skip
    return repo


@pytest.mark.sandbox
def test_a_base_teardown_error_outside_module_dir_is_not_charged_to_a_correct_fix(
    calc_project, tmp_path
):
    # whole-suite would select only tests/deep/test_shared.py::test_1 (test_2 carries the error);
    # its run alone fires the same finalizer. The probe is unusable instead: degrade to module-dir
    result = run(
        calc_project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite",
        extra={"tests/deep/test_shared.py": SHARED},
    )  # fmt: skip
    assert result.status == "COMPLETED" and result.verified, result.reason
    assert result.p2p["degraded"] == "base-probe-teardown-error"
    assert result.p2p["applied"] == "module-dir"


@pytest.mark.sandbox
def test_a_module_dir_base_teardown_error_ends_before_the_worker(calc_project, tmp_path):
    result = run(
        calc_project, tmp_path, {"src/calc.py": FIX_ADD}, mode="module-dir",
        extra={"tests/test_shared.py": SHARED},
    )  # fmt: skip
    assert result.status == "FAILED" and not result.verified and result.candidate_commit is None
    assert result.reason.startswith("UNCOMPARABLE: BASE_ENV_FAILED: ")
    assert "base-probe-teardown-error" in result.reason


CANDIDATE_TEARDOWN = (
    "import pytest, calc\n@pytest.fixture(scope='module')\ndef res():\n    yield 1\n"
    "    if calc.SLOW:\n        raise RuntimeError('teardown')\n"
    "def test_r1(res):\n    assert res\ndef test_r2(res):\n    assert res\n"
)


@pytest.mark.sandbox
def test_a_teardown_error_only_the_candidate_causes_stays_failed(calc_project, tmp_path):
    result = run(
        calc_project, tmp_path, {"src/calc.py": FIX_ADD.replace("SLOW = False", "SLOW = True")},
        mode="whole-suite", extra={"tests/test_res.py": CANDIDATE_TEARDOWN},
    )  # fmt: skip
    assert result.status == "FAILED" and not result.verified
    assert result.p2p["degraded"] == ""  # the base was clean: the failure is the candidate's
    assert result.p2p["rerun"]["failed"] == ["tests/test_res.py::test_r2"]
    assert result.p2p["p2p_rerun_passed"] is False


TEARDOWN_ONLY = (
    "import pytest\nfrom calc import add\n@pytest.fixture\ndef fixed():\n    yield\n"
    "    assert add(1, 1) == 2\ndef test_regression(fixed):\n    pass\n"
)


@pytest.mark.sandbox
def test_a_deferred_test_that_fails_only_at_teardown_on_the_base_is_uncomparable(project, state):
    # before #478 the base run read as passed (NO_TEETH, REFUSED) although pytest exited 1
    code, out = run_cli(project, state, TEARDOWN_ONLY, {"src/calc.py": FIX_ADD})
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False
    assert out["uncomparable"]["code"] == "BASE_PYTEST_ERROR"
