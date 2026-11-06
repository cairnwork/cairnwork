"""#796 S3 step 2: a test file whose module-level import of a NEW submodule of an existing
in-repo package fails on the base (``import pkg.newmod``, ``from pkg.newmod import x``) gives its
new or changed tests teeth, like S2's ``from pkg import new_name``; test-suite helpers, build
output and anything outside the source tree stay UNCOMPARABLE."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import commit_change
from test_collect_import_teeth import FILE, OTHER, OTHERS, _qualifying
from test_deferred_tests import make_test_commit
from test_uncomparable_paths import CHECK, _run
from verify_helpers import candidate, verify

from cairnwork.collect_import import as_failed
from cairnwork.deferred_tests import base_comparison
from cairnwork.verifier_spec import VerifierSpec

PARENT = "src/calcpkg/__init__.py"
NEW = "src/calcpkg/newmod.py"
AT_MODULE = "from calcpkg.newmod import double\ndef test_double():\n    assert double(2) == 4\n"
NEW_BODY = "def double(a):\n    return 2 * a\n"


def is_test(path):
    return Path(path).name.startswith("test_")


def _s3(module="calcpkg.newmod", origin=PARENT, rc: Any = 1):
    run = _run(VerifierSpec("v", (FILE,)), "/nonexistent", "r", rc=rc, collect_errors=(FILE,))
    return replace(run, submodule_errors=((FILE, module, origin),))


def test_a_qualifying_s3_run_has_its_targets_failed_and_teeth():  # AC1 (unit)
    got = as_failed(_s3(), frozenset({FILE, f"{FILE}::test_x"}), is_test)
    assert got.collect_errors == () and got.fail_kind == {}
    assert got.outcomes == {FILE: "failed", f"{FILE}::test_x": "failed"}
    assert base_comparison([got], frozenset({FILE})).kind == "TEETH"


QUALIFYING = {
    "a deeper package": ("a.b.newmod", "src/a/b/__init__.py"),
    "a neutral helper-like name": ("calcpkg.helpers", PARENT),
    "a private module": ("calcpkg._impl", PARENT),
    "a flat layout": ("calcpkg.newmod", "calcpkg/__init__.py"),
}


@pytest.mark.parametrize("name", QUALIFYING)
def test_fix_code_shapes_qualify(name):
    module, origin = QUALIFYING[name]
    got = as_failed(_s3(module, origin), frozenset({FILE}), is_test)
    assert got.collect_errors == () and got.outcomes == {FILE: "failed"}


HELD_BACK = {
    "the parent is a test package": ("tests.newmod", "tests/__init__.py"),
    "a parent under a test directory": ("helpers.newmod", "tests/helpers/__init__.py"),
    "a parent under a test directory (singular)": ("h.newmod", "src/test/h/__init__.py"),
    "the would-be file is a test file": ("calcpkg.test_new", PARENT),
    "a *_test leaf": ("calcpkg.new_test", PARENT),
    "a *tests leaf": ("calcpkg.unittests", PARENT),
    "a conftest leaf": ("calcpkg.conftest", PARENT),
    "a fixtures leaf": ("calcpkg.fixtures", PARENT),
    "a mocks leaf": ("calcpkg.mocks", PARENT),
    "a _version leaf": ("calcpkg._version", PARENT),
    "a version leaf": ("calcpkg.version", PARENT),
    "a protobuf leaf": ("calcpkg.api_pb2", PARENT),
    "a protobuf grpc leaf": ("calcpkg.api_pb2_grpc", PARENT),
    "an extension leaf": ("calcpkg.fast_ext", PARENT),
    "a cython leaf": ("calcpkg.fast_cy", PARENT),
    "a venv parent": ("m.newmod", ".venv/lib/m/__init__.py"),
    "a site-packages parent": ("m.newmod", "lib/site-packages/m/__init__.py"),
    "a dist-packages parent": ("m.newmod", "lib/dist-packages/m/__init__.py"),
    "a .tox parent": ("m.newmod", ".tox/py/m/__init__.py"),
    "a .nox parent": ("m.newmod", ".nox/py/m/__init__.py"),
    "a vendor parent": ("m.newmod", "src/vendor/m/__init__.py"),
    "a _vendor parent": ("m.newmod", "src/_vendor/m/__init__.py"),
    "a third_party parent": ("m.newmod", "third_party/m/__init__.py"),
    "a node_modules parent": ("m.newmod", "node_modules/m/__init__.py"),
}  # AC8: each stays UNCOMPARABLE


@pytest.mark.parametrize("name", HELD_BACK)
def test_test_suite_build_and_vendored_shapes_stay_uncomparable(name):
    module, origin = HELD_BACK[name]
    run = _s3(module, origin)
    assert as_failed(run, frozenset({FILE}), is_test) is run
    assert base_comparison([run], frozenset({FILE})).code == "BASE_COLLECTION_ERROR"


def test_a_would_be_package_that_is_a_test_file_by_the_rule_stays_uncomparable():
    def rule(path):  # a base whose python_files also names any __init__.py under a "new" dir
        return path.endswith("newmod/__init__.py") or is_test(path)

    run = _s3()
    assert as_failed(run, frozenset({FILE}), rule) is run


def test_a_node_in_both_records_must_pass_both():  # review item 10
    s2_bad = replace(_s3(), import_errors=((FILE, "m", "tests/test_h.py"),))
    assert as_failed(s2_bad, frozenset({FILE}), is_test) is s2_bad
    s3_bad = replace(_qualifying(), submodule_errors=((FILE, "calcpkg.mocks", PARENT),))
    assert as_failed(s3_bad, frozenset({FILE}), is_test) is s3_bad
    both = replace(_qualifying(), submodule_errors=((FILE, "calcpkg.newmod", PARENT),))
    assert as_failed(both, frozenset({FILE}), is_test).collect_errors == ()


def test_s2_keeps_its_own_vendored_list():  # the wider S3 list is not applied to S2 (#933)
    run = replace(_qualifying(), import_errors=((FILE, "m", "src/vendor/m.py"),))
    assert as_failed(run, frozenset({FILE}), is_test).collect_errors == ()


def test_two_errors_one_s3_qualifying_and_one_not_is_uncomparable():  # AC10
    run = replace(_s3(), collect_errors=(FILE, OTHER))
    assert as_failed(run, frozenset({FILE, OTHER}), is_test) is run
    good, bad = _s3(), replace(_s3(), submodule_errors=())
    runs = [as_failed(r, frozenset({FILE}), is_test) for r in (good, bad)]
    assert base_comparison(runs, frozenset({FILE})).code == "BASE_COLLECTION_ERROR"


@pytest.mark.parametrize("code", OTHERS)
def test_an_s3_entry_does_not_hide_what_the_base_check_judges_first(code):  # AC10
    run = replace(_s3(rc=None if code == "BASE_TIMEOUT" else 1), **OTHERS[code])
    got = as_failed(run, frozenset({FILE}), is_test)
    for field in OTHERS[code]:
        assert getattr(got, field) == getattr(run, field)
    assert base_comparison([got], frozenset({FILE})).code == code


def test_a_parametrized_target_is_keyed_by_its_bare_id():  # AC10
    target = f"{FILE}::test_p"
    got = as_failed(_s3(), frozenset({target}), is_test)
    assert base_comparison([got], frozenset({target})).kind == "TEETH"


# real runs (Seatbelt), through verify_candidate


def run_cli(project, state, tests, cand_files):
    """test_uncomparable_paths.run_cli, with the candidate's files allowed (a new file)."""
    base, test_commit = make_test_commit(project, {FILE: tests})
    cand = candidate(project, base, cand_files)
    argv = " ".join(CHECK[0][1])
    return verify(state, project, base, cand, "--test-commit", test_commit, tests=(argv,),
                  allowed=tuple(dict.fromkeys((*cand_files, "src/calc.py"))))  # fmt: skip


def _with_parent(project):
    commit_change(project, {PARENT: "def old():\n    return 1\n"})


@pytest.mark.sandbox
@pytest.mark.parametrize("line", ["from calcpkg.newmod import double", "import calcpkg.newmod"])
def test_a_new_submodule_the_fix_adds_has_teeth_and_verifies(project, state, line):  # AC1
    _with_parent(project)
    body = AT_MODULE.replace("from calcpkg.newmod import double", line)
    body = body.replace("double(2)", "double(2)" if "from" in line else "calcpkg.newmod.double(2)")
    code, out = run_cli(project, state, body, {NEW: NEW_BODY})
    assert code == 0 and out["status"] == "VERIFIED", out


@pytest.mark.sandbox
def test_a_candidate_that_does_not_add_the_module_is_not_verified(project, state):  # AC1, AC8b
    _with_parent(project)
    code, out = run_cli(project, state, AT_MODULE, {"src/calc.py": "def add(a, b):\n"
                                                    "    return a + b\n"})  # fmt: skip
    assert code == 2 and out["status"] == "FAILED" and out["uncomparable"] is None, out
    assert out["core_status"] not in ("", "VERIFIED")
    assert "collection could not import 1: calcpkg.newmod" in out["reason"]  # AC8b


@pytest.mark.sandbox
def test_a_wrong_new_module_is_not_verified(project, state):
    _with_parent(project)
    code, out = run_cli(project, state, AT_MODULE, {NEW: "def double(a):\n    return a\n"})
    assert out["status"] == "FAILED" and out["verified"] is False, out


@pytest.mark.sandbox
def test_a_missing_test_helper_module_stays_uncomparable(project, state):  # AC8
    _with_parent(project)
    body = "from calcpkg.fixtures import f\ndef test_double():\n    assert f\n"
    code, out = run_cli(project, state, body, {"src/calcpkg/fixtures.py": "f = 1\n"})
    assert code == 2 and out["status"] == "UNCOMPARABLE", out
    assert out["uncomparable"]["code"] == "BASE_COLLECTION_ERROR"


@pytest.mark.sandbox
def test_a_relative_import_in_a_test_package_stays_uncomparable(project, state):  # AC8
    commit_change(project, {"tests/__init__.py": ""})
    body = "from .newrel_s3 import x\ndef test_double():\n    assert x\n"
    code, out = run_cli(project, state, body, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    assert code == 2 and out["status"] == "UNCOMPARABLE", out


@pytest.mark.sandbox
def test_a_new_top_level_module_still_stays_uncomparable(project, state):  # AC3
    body = "import newtop_s3\ndef test_double():\n    assert newtop_s3.x\n"
    code, out = run_cli(project, state, body, {"src/newtop_s3.py": "x = 1\n"})
    assert code == 2 and out["uncomparable"]["code"] == "BASE_COLLECTION_ERROR", out


FORGED = (
    "import calcpkg\nraise ModuleNotFoundError(\"No module named 'calcpkg.newmod'\","
    " name='calcpkg.newmod')\ndef test_double(): pass\n"
)


@pytest.mark.sandbox
def test_a_forged_missing_submodule_never_makes_a_wrong_candidate_verified(project, state):
    _with_parent(project)  # the record qualifies on the base; the candidate run raises it too
    code, out = run_cli(project, state, FORGED, {NEW: NEW_BODY})
    assert out["status"] != "VERIFIED" and out["verified"] is False, out
