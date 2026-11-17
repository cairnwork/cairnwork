"""Regression coverage for static deferred targets pytest never collected (#1430)."""

from types import SimpleNamespace

from cairnwork.deferred_tests import not_run


def _run(*, collected=(), outcomes=None, finished=True, errors=(), skipped=()):
    return SimpleNamespace(
        collected=tuple(collected),
        outcomes=outcomes or {},
        collection_finished=finished,
        collect_errors=tuple(errors),
        collect_skipped=tuple(skipped),
    )


def test_uncollected_static_target_is_dropped_after_clean_collection():
    ghost = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(
        collected=["tests/test_unit.py::Unit::test_x"],
        outcomes={"tests/test_unit.py::Unit::test_x": "passed"},
    )
    assert not_run([run], {ghost}) == []


def test_collected_target_without_outcome_stays_not_run():
    target = "tests/test_unit.py::Unit::test_x"
    run = _run(collected=[target])
    assert not_run([run], {target}) == [target]


def test_unfinished_collection_keeps_ghost_target():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(collected=["tests/test_unit.py::Unit::test_x"], finished=False)
    assert not_run([run], {target}) == [target]


def test_collection_error_keeps_ghost_target():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(collected=["tests/test_unit.py::Unit::test_x"], errors=["tests/test_unit.py"])
    assert not_run([run], {target}) == [target]


def test_collected_class_prefix_satisfies_class_target():
    target = "tests/test_unit.py::Unit"
    run = _run(collected=["tests/test_unit.py::Unit::test_x[one]"], outcomes={})
    assert not_run([run], {target}) == [target]


_BASE = "class Base:\n    def test_inherited(self):\n        assert True\n"
_FILE = (
    "from base_mod import Base\n\n\n"
    "class TestUnit:\n    def test_new(self):\n        assert True\n\n\n"
    "class TestEndToEnd(Base):\n    pass\n"
)


def _real_pytest_collection(tmp_path):
    import ast
    import subprocess
    import sys

    from cairnwork.deferred_nodes import test_functions

    (tmp_path / "base_mod.py").write_text(_BASE)
    (tmp_path / "test_x.py").write_text(_FILE)
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "test_x.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    ).stdout
    assert out.strip(), "pytest collected nothing"
    collected = [line for line in out.splitlines() if "::" in line]
    assert "test_x.py::TestUnit::test_new" in collected
    targets = {f"test_x.py::{n}" for n in test_functions(ast.parse(_FILE))[0]}
    return collected, targets


def test_imported_base_ghost_target_is_not_a_false_not_run(tmp_path):
    collected, targets = _real_pytest_collection(tmp_path)
    assert "test_x.py::TestEndToEnd::test_new" in targets  # the ghost the static rule emits
    assert "test_x.py::TestEndToEnd::test_new" not in collected  # pytest never collects it
    run = _run(collected=collected, outcomes=dict.fromkeys(collected, "passed"))
    assert not_run([run], targets) == []


def test_the_same_fixture_still_refuses_a_real_test_that_did_not_run(tmp_path):
    collected, targets = _real_pytest_collection(tmp_path)
    ran = [n for n in collected if not n.endswith("TestUnit::test_new")]
    run = _run(collected=collected, outcomes=dict.fromkeys(ran, "passed"))
    assert not_run([run], targets) == ["test_x.py::TestUnit::test_new"]


def test_empty_collection_keeps_every_target():
    target = "tests/test_unit.py::EndToEnd::test_x"
    assert not_run([_run(collected=[])], {target}) == [target]


def test_outcomes_outside_the_collected_record_keep_ghost_targets():
    ghost = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(collected=["a.py::t"], outcomes={"tests/test_unit.py::Unit::test_x": "passed"})
    assert not_run([run], {ghost}) == [ghost]
