"""#796 step 2: a test file whose module-level import of a name the fix adds fails on the base
(``from <existing in-repo module> import <name>``) gives its new or changed tests teeth, as the
same import inside the test function does; every other collect error stays UNCOMPARABLE."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_deferred_tests import make_test_commit
from test_uncomparable_paths import _run, run_cli
from verify_helpers import FIX, candidate, verify

from cairnwork import pipeline_judge, verifiers
from cairnwork.base_teeth import base_teeth
from cairnwork.collect_import import as_failed
from cairnwork.deferred_tests import base_comparison
from cairnwork.verifier_spec import VerifierSpec

FILE = "tests/test_regression.py"
OTHER = "tests/test_other.py"
FIX_DOUBLE = FIX + "def double(a):\n    return 2 * a\n"
FIX_DOUBLE_FILES = {"src/calc.py": FIX_DOUBLE}
AT_MODULE = "from calc import double\ndef test_double():\n    assert double(2) == 4\n"


def is_test(path):
    return Path(path).name.startswith("test_")


def _qualifying(rc: Any = 1, **flags):
    """A base run as the continue flag leaves it: exit 1, the collector failed, the guard's record
    names an in-repo module's file, no test ran in that file."""
    spec = VerifierSpec("v", (FILE,))
    run = _run(spec, "/nonexistent", "r", rc=rc, collect_errors=(FILE,), **flags)
    return replace(run, import_errors=((FILE, "calc", "src/calc.py"),))


# unit: as_failed


def test_a_qualifying_run_is_returned_with_its_targets_failed_and_no_fail_kind():
    got = as_failed(_qualifying(), frozenset({FILE, f"{FILE}::test_x"}), is_test)
    assert got.collect_errors == () and got.outcomes == {
        FILE: "failed",
        f"{FILE}::test_x": "failed",
    }
    assert got.fail_kind == {} and base_teeth([got], {FILE, f"{FILE}::test_x"})[0] == "TEETH"


def test_only_the_new_or_changed_targets_of_the_file_are_marked():
    run = replace(_qualifying(), outcomes={OTHER + "::test_a": "passed"})
    got = as_failed(run, frozenset({f"{FILE}::test_new"}), is_test)
    assert got.outcomes == {OTHER + "::test_a": "passed", f"{FILE}::test_new": "failed"}


UNCHANGED = {
    "another collect error beside it": lambda r: replace(r, collect_errors=(FILE, OTHER)),
    "no record": lambda r: replace(r, import_errors=()),
    "a record for another node": lambda r: replace(r, import_errors=((OTHER, "m", "src/m.py"),)),
    "the origin is a test file": lambda r: replace(
        r, import_errors=((FILE, "m", "tests/test_h.py"),)
    ),
    "the origin is in a venv": lambda r: replace(r, import_errors=((FILE, "m", ".venv/lib/m.py"),)),
    "the origin is in site-packages": lambda r: replace(
        r, import_errors=((FILE, "m", "lib/site-packages/m/__init__.py"),)
    ),
    "the origin is in node_modules": lambda r: replace(
        r, import_errors=((FILE, "m", "node_modules/m/index.py"),)
    ),
    "the collector is not a target file": lambda r: replace(
        r, collect_errors=(OTHER,), import_errors=((OTHER, "m", "src/m.py"),)
    ),
}  # AC5, AC7, AC8, AC12: the run stays as it was


@pytest.mark.parametrize("name", UNCHANGED)
def test_anything_else_leaves_the_run_unchanged_and_uncomparable(name):
    run = UNCHANGED[name](_qualifying())
    assert as_failed(run, frozenset({FILE}), is_test) is run
    assert base_comparison([run], frozenset({FILE})).code == "BASE_COLLECTION_ERROR"


def test_a_run_with_no_collect_error_is_returned_as_it_is():
    run = replace(_qualifying(), collect_errors=())
    assert as_failed(run, frozenset({FILE}), is_test) is run


# AC10: what _base_problem judged before is still judged first
OTHERS = {
    "BASE_FORGED_BYTECODE": {"forged": ("x.pyc",)},
    "BASE_SURVIVORS": {"survivors": 1},
    "BASE_MUTATED": {"mutated": ("src/x.py",)},
    "BASE_TIMEOUT": {"timed_out": True},
    "BASE_LOG_OVER_CAP": {"log_too_big": True},
    "BASE_REPORT_OVER_CAP": {"report_too_big": True},
    "BASE_PYTEST_ERROR": {"teardown_errors": (FILE + "::test_a",)},
}


@pytest.mark.parametrize("code", OTHERS)
def test_a_qualifying_error_does_not_hide_what_the_base_check_judges_first(code):
    run = replace(_qualifying(rc=None if code == "BASE_TIMEOUT" else 1), **OTHERS[code])
    got = as_failed(run, frozenset({FILE}), is_test)
    for field in OTHERS[code]:  # the fields _base_problem reads are the run's own
        assert getattr(got, field) == getattr(run, field)
    assert (got.returncode, got.recorded) == (run.returncode, run.recorded)
    assert base_comparison([got], frozenset({FILE})).code == code


def test_the_exit_code_one_is_judged_after_the_failed_outcomes_are_added():  # AC17
    run = _qualifying(rc=1)
    assert base_comparison([run], frozenset({FILE})).code == "BASE_COLLECTION_ERROR"
    assert (
        base_comparison([as_failed(run, frozenset({FILE}), is_test)], frozenset({FILE})).kind
        == "TEETH"
    )
    no_failed = replace(run, collect_errors=(), import_errors=())  # rc 1, nothing failed, no error
    assert base_comparison([no_failed], frozenset({FILE})).code == "BASE_PYTEST_ERROR"


def test_two_verifiers_one_qualifying_and_one_not_is_uncomparable():  # AC18
    good, bad = _qualifying(), replace(_qualifying(), import_errors=())
    runs = [as_failed(r, frozenset({FILE}), is_test) for r in (good, bad)]
    assert base_comparison(runs, frozenset({FILE})).code == "BASE_COLLECTION_ERROR"


def test_a_parametrized_target_is_keyed_by_its_bare_id():  # AC18
    target = f"{FILE}::test_p"
    got = as_failed(_qualifying(), frozenset({target}), is_test)
    assert got.outcomes == {target: "failed"}
    assert base_comparison([got], frozenset({target})).kind == "TEETH"  # not NOT_RUN


def test_a_run_for_another_file_still_counts_when_the_flag_let_it_run():  # AC11
    other = replace(_run(VerifierSpec("w", (OTHER,)), "/x", "o", rc=0),
                    outcomes={OTHER + "::test_n": "passed"})  # fmt: skip
    targets = frozenset({FILE, OTHER + "::test_n"})
    runs = [as_failed(_qualifying(), targets, is_test), other]
    assert base_comparison(runs, targets).kind == "TEETH"  # the other file's test is not NOT_RUN


def test_base_teeth_still_grants_teeth_when_any_one_target_fails():
    """The dependency as_failed rests on (PRESERVED_BEHAVIORS, #796): every target of the file is
    marked failed, which is safe only while one failed target (no fail_kind) is enough. If
    base_teeth is changed to count teeth per test, this fails: revisit as_failed first."""
    run = replace(_run(VerifierSpec("v", (FILE,)), "/x", "r", rc=1),
                  outcomes={f"{FILE}::test_a": "failed", f"{FILE}::test_b": "passed"})  # fmt: skip
    assert base_teeth([run], {f"{FILE}::test_a", f"{FILE}::test_b"})[0] == "TEETH"


# tests_with_teeth: the flag goes on the base specs only


def test_the_continue_flag_is_set_on_base_specs_and_never_on_collect_only(project, monkeypatch):
    seen = []

    def fake(ctx, commit, specs, run_id, **kw):
        seen.extend(specs)
        return tuple(
            _run(s, ctx.run_root, run_id, rc=1, outcomes={FILE + "::test_double": "failed"})
            for s in specs
        )

    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    base, commit = make_test_commit(project, {FILE: AT_MODULE})
    task = type("T", (), dict(repo=project, base_commit=base, test_commit=commit,
                              test_roots=("tests",), test_store=None))()  # fmt: skip
    specs = (VerifierSpec("a", (FILE,)), VerifierSpec("c", (FILE,), collect_only=True))
    ctx = type("C", (), dict(run_root=project, attempt_id="x"))()
    result = type("R", (), dict(attempt_id="x", logs=[]))()
    pipeline_judge.tests_with_teeth(task, specs, result, ctx)  # type: ignore[arg-type]
    assert [(s.verifier_id, s.continue_on_collect_errors) for s in seen] == [
        ("a", True),
        ("c", False),
    ]
    assert not any(s.continue_on_collect_errors for s in specs)  # the caller's specs are untouched


def test_the_argv_of_a_base_run_carries_the_flag_only_when_set():
    spec = VerifierSpec("v", (FILE,))
    assert not spec.continue_on_collect_errors and spec.collect_only is False
    assert replace(spec, continue_on_collect_errors=True).continue_on_collect_errors
    assert "continue-on-collection-errors" in Path(verifiers.__file__).read_text()


# real runs (Seatbelt): AC1, AC3, AC4, AC5, AC11, AC14, through verify_candidate


def _cli(project, state, files, cand_files, targets=(FILE,)):
    base, commit = make_test_commit(project, files)
    cand = candidate(project, base, cand_files)
    argv = " ".join(("python3", "-m", "pytest", "-q", *targets))
    return verify(state, project, base, cand, "--test-commit", commit, tests=(argv,))


@pytest.mark.sandbox
def test_a_module_level_import_of_a_name_the_fix_adds_has_teeth_and_verifies(project, state):
    code, out = run_cli(project, state, AT_MODULE, {"src/calc.py": FIX_DOUBLE})  # AC1
    assert code == 0 and out["status"] == "VERIFIED", out


@pytest.mark.sandbox
def test_a_candidate_that_does_not_add_the_name_is_not_verified(project, state):
    code, out = run_cli(project, state, AT_MODULE, {"src/calc.py": FIX})  # AC1 negative
    assert code == 2 and out["status"] == "FAILED" and out["uncomparable"] is None
    assert out["core_status"] not in ("", "VERIFIED")


UNCOMPARABLE_FILES = {
    "a missing third-party package": "import not_installed_pkg_796\ndef test_double(): pass\n",
    "a missing name of a stdlib module": "from json import nope\ndef test_double(): pass\n",
    "another collect error in the file": "raise RuntimeError('x')\ndef test_double(): pass\n",
}  # AC3, AC4, AC5


@pytest.mark.sandbox
@pytest.mark.parametrize("name", UNCOMPARABLE_FILES)
def test_other_collect_errors_stay_uncomparable(project, state, name):
    code, out = run_cli(project, state, UNCOMPARABLE_FILES[name], {"src/calc.py": FIX_DOUBLE})
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False
    assert out["uncomparable"]["code"] == "BASE_COLLECTION_ERROR"
    assert FILE in out["uncomparable"]["detail"] and out["core_status"] == ""


@pytest.mark.sandbox
def test_the_hint_says_what_would_qualify(project, state):
    code, out = run_cli(
        project, state, UNCOMPARABLE_FILES["another collect error in the file"], FIX_DOUBLE_FILES
    )
    assert "inside the test function" in out["uncomparable"]["detail"]
    assert "from <existing in-repo module> import <name>" in out["uncomparable"]["detail"]


@pytest.mark.sandbox
def test_another_file_still_runs_on_the_base_beside_a_qualifying_one(project, state):  # AC11
    other = "from calc import add\ndef test_n():\n    assert add(1, 1) == 2\n"
    files = {FILE: AT_MODULE, OTHER: other}
    code, out = _cli(project, state, files, {"src/calc.py": FIX_DOUBLE}, (FILE, OTHER))
    assert code == 0 and out["status"] == "VERIFIED", out


@pytest.mark.sandbox
def test_a_qualifying_file_beside_another_collect_error_is_uncomparable(project, state):  # AC7
    files = {FILE: AT_MODULE, OTHER: "raise RuntimeError('x')\ndef test_n(): pass\n"}
    code, out = _cli(project, state, files, {"src/calc.py": FIX_DOUBLE}, (FILE, OTHER))
    assert code == 2 and out["uncomparable"]["code"] == "BASE_COLLECTION_ERROR"


FORGED = {  # AC: the record can be made to qualify; the candidate run is what refuses
    "test file raises it": "import os\nraise ImportError(\"cannot import name 'x' from 'calc'\",\n"
    "    name='calc', path=os.path.abspath('src/calc.py'))\ndef test_double(): pass\n",
    "repo code builds it": "import os, mk\nraise mk.make(os.path.abspath('src/calc.py'))\n"
    "def test_double(): pass\n",
}
MK = (
    "def make(path):\n    return ImportError(\"cannot import name 'x' from 'calc'\","
    " name='calc', path=path)\n"
)


@pytest.mark.sandbox
@pytest.mark.parametrize("how", FORGED)
def test_a_forged_import_error_never_makes_a_wrong_candidate_verified(project, state, how):
    from conftest import commit_change

    commit_change(project, {"src/mk.py": MK})
    code, out = run_cli(project, state, FORGED[how], {"src/calc.py": FIX_DOUBLE})
    assert out["status"] != "VERIFIED" and out["verified"] is False, out
    assert out["core_status"] in ("",) or out["core_status"] != "VERIFIED"
