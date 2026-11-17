"""Regression coverage for fail-closed handling of static deferred targets (#1430)."""

import ast
import subprocess
import sys
from types import SimpleNamespace

from cairnwork.deferred_speculative import speculative_ids
from cairnwork.deferred_tests import not_run


def _run(*, collected=(), outcomes=None, finished=True, errors=(), skipped=()):
    return SimpleNamespace(
        collected=tuple(collected),
        outcomes=outcomes or {},
        collection_finished=finished,
        collect_errors=tuple(errors),
        collect_skipped=tuple(skipped),
    )


def _collected(tmp_path, files):
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return [line for line in proc.stdout.splitlines() if "::" in line]


def _assert_real_missing_target(tmp_path, files, target):
    collected = _collected(tmp_path, files)
    assert target not in collected
    run = _run(collected=collected, outcomes=dict.fromkeys(collected, "passed"))
    assert not_run([run], {target}) == [target]


def test_default_not_run_does_not_filter_unmarked_targets():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(
        collected=["tests/test_unit.py::Unit::test_x"],
        outcomes={"tests/test_unit.py::Unit::test_x": "passed"},
    )
    assert not_run([run], {target}) == [target]


def test_collected_target_without_outcome_stays_not_run():
    target = "tests/test_unit.py::Unit::test_x"
    assert not_run([_run(collected=[target])], {target}) == [target]


def test_unfinished_collection_keeps_ghost_target():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(collected=["tests/test_unit.py::Unit::test_x"], finished=False)
    assert not_run([run], {target}, {target}) == [target]


def test_collection_error_keeps_ghost_target():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(collected=["tests/test_unit.py::Unit::test_x"], errors=["tests/test_unit.py"])
    assert not_run([run], {target}, {target}) == [target]


def test_deselected_target_stays_not_run(tmp_path):
    _assert_real_missing_target(
        tmp_path,
        {
            "conftest.py": (
                "def pytest_collection_modifyitems(items):\n"
                "    deselected = [item for item in items if item.name == 'test_new']\n"
                "    items[:] = [item for item in items if item not in deselected]\n"
                "    config = items[0].session.config if items else None\n"
                "    if config is not None:\n"
                "        config.hook.pytest_deselected(items=deselected)\n"
            ),
            "test_x.py": (
                "import pytest\n\n"
                "def test_fast():\n    pass\n\n"
                "@pytest.mark.slow\n"
                "def test_new():\n    pass\n"
            ),
        },
        "test_x.py::test_new",
    )


def test_function_test_false_target_stays_not_run(tmp_path):
    _assert_real_missing_target(
        tmp_path,
        {
            "test_x.py": (
                "def test_fast():\n    pass\n\n"
                "def test_new():\n    pass\n\n"
                "test_new.__test__ = False\n"
            )
        },
        "test_x.py::test_new",
    )


def test_class_test_false_target_stays_not_run(tmp_path):
    _assert_real_missing_target(
        tmp_path,
        {
            "test_x.py": (
                "class TestA:\n    def test_fast(self):\n        pass\n\n"
                "class TestB:\n    __test__ = False\n\n"
                "    def test_new(self):\n        pass\n"
            )
        },
        "test_x.py::TestB::test_new",
    )


def test_custom_python_functions_target_stays_not_run(tmp_path):
    _assert_real_missing_target(
        tmp_path,
        {
            "pytest.ini": "[pytest]\npython_functions = check_*\n",
            "test_x.py": "def check_fast():\n    pass\n\ndef test_new():\n    pass\n",
        },
        "test_x.py::test_new",
    )


def test_target_in_file_with_no_collected_nodes_stays_not_run():
    target = "tests/test_empty.py::TestUnit::test_new"
    run = _run(
        collected=["tests/test_other.py::test_ok"],
        outcomes={"tests/test_other.py::test_ok": "passed"},
    )
    assert not_run([run], {target}, {target}) == [target]


def test_collection_skips_keep_speculative_ghost_not_run():
    target = "tests/test_unit.py::EndToEnd::test_x"
    run = _run(
        collected=["tests/test_unit.py::Unit::test_x"],
        outcomes={"tests/test_unit.py::Unit::test_x": "passed"},
        skipped=["tests/test_unit.py"],
    )
    assert not_run([run], {target}, {target}) == [target]


def test_imported_base_ghost_target_is_dropped_when_class_has_collected_test(tmp_path):
    base = "class Base:\n    def test_inherited(self):\n        assert True\n"
    source = (
        "from base_mod import Base\n\n"
        "class TestUnit:\n    def test_new(self):\n        assert True\n\n"
        "class TestEndToEnd(Base):\n    pass\n"
    )
    collected = _collected(tmp_path, {"base_mod.py": base, "test_x.py": source})
    ghost = "test_x.py::TestEndToEnd::test_new"
    assert ghost not in collected
    targets = {f"test_x.py::{node}" for node in speculative_ids(ast.parse(source))}
    assert ghost in targets
    run = _run(collected=collected, outcomes=dict.fromkeys(collected, "passed"))
    assert not_run([run], {ghost}, {ghost}) == []


def test_parametrized_imported_base_ghost_is_dropped(tmp_path):
    base = "class Base:\n    def test_inherited(self):\n        assert True\n"
    source = (
        "import pytest\nfrom base_mod import Base\n\n"
        "class TestUnit:\n"
        "    @pytest.mark.parametrize('value', [1, 2])\n"
        "    def test_new(self, value):\n        assert value\n\n"
        "class TestEndToEnd(Base):\n    pass\n"
    )
    collected = _collected(tmp_path, {"base_mod.py": base, "test_x.py": source})
    ghost = "test_x.py::TestEndToEnd::test_new"
    targets = {f"test_x.py::{node}" for node in speculative_ids(ast.parse(source))}
    assert ghost in targets and ghost not in collected
    run = _run(collected=collected, outcomes=dict.fromkeys(collected, "passed"))
    assert not_run([run], {ghost}, {ghost}) == []


def test_resolvable_same_file_inheritance_is_not_speculative():
    tree = ast.parse(
        "class Base:\n    def test_x(self): pass\n\nclass TestChild(Base):\n    pass\n"
    )
    assert speculative_ids(tree) == frozenset()


def test_simple_namespace_missing_collection_attributes_fails_closed():
    run = SimpleNamespace(outcomes={"tests/test_unit.py::Unit::test_x": "passed"})
    target = "tests/test_unit.py::EndToEnd::test_x"
    assert not_run([run], {target}, {target}) == [target]


_GHOST = "tests/test_unit.py::EndToEnd::test_new"
_SIBLING = "tests/test_unit.py::EndToEnd::test_old"


def _clean_run(**kw):
    return _run(collected=[_SIBLING], outcomes={_SIBLING: "passed"}, **kw)


def test_clean_runs_drop_speculative_ghost_with_collected_class():
    assert not_run([_clean_run()], {_GHOST}, {_GHOST}) == []


def test_each_unclean_run_keeps_speculative_ghost():
    bad_runs = [
        _clean_run(finished=False),
        _clean_run(errors=["tests/test_unit.py"]),
        _clean_run(skipped=["tests/test_unit.py"]),
        _run(collected=[_SIBLING], outcomes={"tests/test_unit.py::Other::test_z": "passed"}),
        _run(collected=[], outcomes={}),
    ]
    for bad in bad_runs:
        assert not_run([_clean_run(), bad], {_GHOST}, {_GHOST}) == [_GHOST], bad
