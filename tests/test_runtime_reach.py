"""#1769 step 2 (unit): what the base run's plugin and sys.path record may refuse.

The record is built by hand, with a real checkout directory so realpath and symlinks are real.
Every refusal is TEST_COMMIT_CONFTEST_STATIC; every doubt leaves the existing verdict alone.
"""

from pathlib import Path

import pytest

from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import check_runtime_reach


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = (tmp_path / "checkout").resolve()
    (path / "tests").mkdir(parents=True)
    (path / "plug").mkdir()
    return path


def rec(root: Path, *, plugins=(), sys_path=(), finish=()) -> CollectedRecord:
    return CollectedRecord(frozenset(), tuple(plugins), tuple(sys_path), tuple(finish), str(root))


def refused(record, changed) -> str:
    with pytest.raises(TestCommitRejected) as caught:
        check_runtime_reach(record, changed)
    assert caught.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    return str(caught.value)


# AC2: a plugin file the base run loaded at runtime that the test commit changes
def test_a_runtime_plugin_file_the_commit_changes_is_refused(root):
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.py"))])
    assert "plug/helper.py" in refused(record, ["plug/helper.py"])


def test_a_pyc_plugin_maps_back_to_its_source(root):
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.pyc"))])
    refused(record, ["plug/helper.py"])


def test_a_symlinked_plugin_is_matched_by_its_target(root):
    (root / "plug" / "real.py").write_text("")
    (root / "plug" / "alias.py").symlink_to(root / "plug" / "real.py")
    record = rec(root, plugins=[("plug.alias", str(root / "plug" / "alias.py"))])
    refused(record, ["plug/real.py"])


def test_a_plugin_the_commit_does_not_change_is_not_refused(root):
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.py"))])
    check_runtime_reach(record, ["tests/test_a.py"])


def test_a_conftest_the_base_loaded_is_left_to_the_conftest_rules(root):
    record = rec(root, plugins=[("tests.conftest", str(root / "tests" / "conftest.py"))])
    check_runtime_reach(record, ["tests/conftest.py"])


def test_a_plugin_outside_the_checkout_is_not_compared(root, tmp_path):
    elsewhere = tmp_path / "site" / "plug" / "helper.py"
    record = rec(root, plugins=[("plug.helper", str(elsewhere))])
    check_runtime_reach(record, ["plug/helper.py"])


def test_a_file_the_commit_adds_has_no_base_run_entry_to_hit(root):
    # changed_on_base holds only paths the base has: a new test file is never passed in
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.py"))])
    check_runtime_reach(record, [])


# AC1: a plugin known only by its import name (no file)
def test_a_file_less_plugin_whose_name_is_a_changed_file_is_refused(root):
    record = rec(root, plugins=[("plug.dyn", None)])
    assert "plug/dyn.py" in refused(record, ["plug/dyn.py"])
    refused(rec(root, plugins=[("plug.dyn", None)]), ["sub/plug/dyn/__init__.py"])


def test_a_file_less_plugin_matching_no_changed_file_is_not_refused(root):
    check_runtime_reach(rec(root, plugins=[("plug.dyn", None)]), ["plug/other.py", "dyn.txt"])


def test_a_file_less_name_is_matched_on_whole_path_parts(root):
    check_runtime_reach(rec(root, plugins=[("dyn", None)]), ["plug/xdyn.py", "plug/dyn.pyx"])


# AC3: an archive (any extension) the base run had on sys.path
@pytest.mark.parametrize("where", ["sys_path", "finish"])
def test_a_changed_archive_on_sys_path_is_refused(root, where):
    archive = str(root / "data" / "bundle.bin")
    record = rec(
        root, **({"sys_path": [archive]} if where == "sys_path" else {"finish": [archive]})
    )
    assert "data/bundle.bin" in refused(record, ["data/bundle.bin"])


def test_a_relative_sys_path_entry_is_read_against_the_checkout(root):
    refused(rec(root, finish=["data/bundle.bin"]), ["data/bundle.bin"])


def test_a_symlink_to_the_changed_archive_on_sys_path_is_refused(root):
    (root / "data").mkdir()
    (root / "data" / "real.zip").write_bytes(b"")
    (root / "data" / "link.bin").symlink_to(root / "data" / "real.zip")
    refused(rec(root, finish=[str(root / "data" / "link.bin")]), ["data/real.zip"])


def test_a_changed_archive_that_is_only_data_is_not_refused(root):
    record = rec(root, sys_path=[str(root)], finish=[str(root), str(root / "tests")])
    check_runtime_reach(record, ["data/bundle.bin"])


def test_a_directory_on_sys_path_holding_a_changed_file_is_not_refused(root):
    record = rec(root, finish=[str(root / "tests"), str(root / "plug")])
    check_runtime_reach(record, ["tests/test_a.py", "plug/helper.py"])


def test_an_unchanged_archive_on_sys_path_is_not_refused(root):
    check_runtime_reach(rec(root, finish=[str(root / "data" / "old.zip")]), ["tests/test_a.py"])


# Failure: no record, no root, or nothing changed: the existing verdict stands
def test_no_record_or_no_root_or_nothing_changed_refuses_nothing(root):
    check_runtime_reach(None, ["plug/helper.py"])
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.py"))])
    check_runtime_reach(record, [])
    rootless = CollectedRecord(frozenset(), record.plugins, (), (), "")
    check_runtime_reach(rootless, ["plug/helper.py"])


def test_a_bad_entry_shape_does_not_raise_anything_but_a_refusal(root):
    record = rec(root, plugins=[("p", "\0bad")], sys_path=["\0bad"], finish=[""])
    check_runtime_reach(record, ["plug/helper.py"])
