"""#1769 step 2 (unit): what the base run's plugin and sys.path record may refuse.

The record is built by hand, with a real checkout directory so realpath and symlinks are real.
Every refusal is TEST_COMMIT_CONFTEST_STATIC; every doubt leaves the existing verdict alone.
"""

import shutil
from pathlib import Path

import pytest

from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import check_runtime_reach, resolve_record


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


# review round 2: resolve while the checkout exists, and compare every prefix, folded
def _gone(record, root):
    """The record as the verifier hands it on: resolved once, then the checkout is removed."""
    record = resolve_record(record, root)
    shutil.rmtree(root)
    return record


def _links(root):
    (root / "data" / "real").mkdir(parents=True)
    (root / "data" / "real.zip").write_bytes(b"")
    (root / "data" / "link.zip").symlink_to(root / "data" / "real.zip")
    (root / "data" / "dirlink").symlink_to(root / "data" / "real")
    (root / "plug" / "real.py").write_text("")
    (root / "plug" / "alias.py").symlink_to(root / "plug" / "real.py")
    (root / "plug" / "pkg").mkdir()
    (root / "plug" / "pkg" / "__init__.py").write_text("")
    (root / "plug" / "pkglink").symlink_to(root / "plug" / "pkg")


def test_a_symlinked_entry_is_matched_after_the_checkout_is_removed(root):
    _links(root)
    kinds = {
        "link": (rec(root, sys_path=[str(root / "data" / "link.zip")]), "data/real.zip"),
        "plugin": (rec(root, plugins=[("p", str(root / "plug" / "alias.py"))]), "plug/real.py"),
        "package": (
            rec(root, plugins=[("p", str(root / "plug" / "pkglink" / "__init__.py"))]),
            "plug/pkg/__init__.py",
        ),
        "dir": (rec(root, finish=[str(root / "data" / "dirlink" / ".." / "real.zip")]),
                "data/real.zip"),
    }  # fmt: skip
    for record, changed in kinds.values():
        refused(resolve_record(record, root), [changed])
    record, changed = kinds["link"]
    gone = _gone(record, root)
    refused(gone, [changed])


@pytest.mark.parametrize(
    ("entry", "changed"),
    [
        ("data/LIB.zip", "data/lib.zip"),
        ("data/lib.zip/pkg/sub", "data/lib.zip"),
        ("data/Café.zip", "data/Café.zip"),
        ("data/lib.zip", "data/Lib.ZIP"),
    ],
)
def test_a_case_unicode_or_inside_variant_of_a_changed_entry_is_refused(root, entry, changed):
    refused(rec(root, sys_path=[entry]), [changed])


def test_a_prefix_that_is_not_a_changed_file_is_not_refused(root):
    check_runtime_reach(rec(root, sys_path=["data/other.zip/pkg"]), ["data/lib.zip"])
    check_runtime_reach(rec(root, sys_path=["data/lib.zip2"]), ["data/lib.zip"])


def test_a_file_less_plugin_named_after_a_changed_conftest_is_left_to_the_conftest_rules(root):
    check_runtime_reach(rec(root, plugins=[("sub.conftest", None)]), ["sub/conftest.py"])


# review round 4: case folds only where the base has no exact file; plugin packages; messages
LIB, UP = "data/lib.zip", "data/Lib.zip"
BOTH = frozenset({LIB, UP, "plug/helper.py"})  # a case-sensitive base holding both spellings


def test_a_base_with_both_case_spellings_passes_the_one_that_is_not_on_sys_path(root):
    check_runtime_reach(rec(root, sys_path=[LIB]), [UP], base_paths=BOTH)


def test_a_base_with_both_case_spellings_refuses_the_one_that_is_on_sys_path(root):
    assert LIB in refused_with(rec(root, sys_path=[LIB]), [LIB, UP], BOTH)
    refused_with(rec(root, sys_path=[LIB]), [LIB], BOTH)


def test_a_case_variant_written_on_a_case_blind_disk_is_refused_when_only_one_file_exists(root):
    refused_with(rec(root, sys_path=["data/LIB.zip"]), [LIB], frozenset({LIB}))
    refused_with(rec(root, sys_path=["data/LIB.zip"]), [LIB], None)  # unknown base: fold


def refused_with(record, changed, base_paths, added=()):
    with pytest.raises(TestCommitRejected) as caught:
        check_runtime_reach(record, changed, added, base_paths=base_paths)
    assert caught.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    return str(caught.value)


@pytest.mark.parametrize(
    "changed", ["vendor/PKG/HELPER.py", "vendor/pkg/Helper.py", "PKG/helper.py"]
)
def test_a_file_less_plugin_matches_a_case_variant_of_its_file(root, changed):
    refused_with(rec(root, plugins=[("pkg.helper", None)]), [changed], frozenset({changed}))


def test_a_file_less_plugin_passes_a_case_variant_when_the_exact_file_exists_too(root):
    both = frozenset({"vendor/pkg/helper.py", "vendor/PKG/HELPER.py"})
    check_runtime_reach(
        rec(root, plugins=[("pkg.helper", None)]), ["vendor/PKG/HELPER.py"], base_paths=both
    )
    refused_with(rec(root, plugins=[("pkg.helper", None)]), ["vendor/pkg/helper.py"], both)


PKG = ("custom_plugin", "custom_plugin/__init__.py")


@pytest.mark.parametrize(
    ("changed", "added", "refuses"),
    [
        (["custom_plugin/hooks.py"], [], True),
        (["custom_plugin/sub/deep.py"], [], True),
        ([], ["custom_plugin/new_hooks.py"], True),
        (["custom_plugin/__init__.py"], [], True),
        (["custom_plugin_extra/x.py"], [], False),
        (["custom_plugin.py"], [], False),
        ([], ["custom_plugin_extra/x.py"], False),
        (["custom_plugin/data.txt"], [], False),
    ],
)
def test_a_changed_or_added_py_file_under_a_loaded_plugin_package_is_refused(
    root, changed, added, refuses
):
    (root / "custom_plugin").mkdir()
    record = rec(root, plugins=[(PKG[0], str(root / PKG[1]))])
    if not refuses:
        check_runtime_reach(record, changed, added)
        return
    refused_with(record, changed, None, added)


def test_a_plugin_module_that_is_not_a_package_does_not_widen_to_its_directory(root):
    record = rec(root, plugins=[("plug.helper", str(root / "plug" / "helper.py"))])
    check_runtime_reach(record, ["plug/other.py"], ["plug/new.py"])


def test_the_message_says_adds_when_the_hit_is_an_added_file(root):
    record = rec(root, sys_path=["data/bundle.bin"])
    assert "adds" in refused_with(record, [], None, added=["data/bundle.bin"])
    assert "changes" in refused(rec(root, sys_path=["data/bundle.bin"]), ["data/bundle.bin"])


def test_a_pycache_bytecode_plugin_maps_back_to_its_source(root):
    path = str(root / "plug" / "__pycache__" / "helper.cpython-311.pyc")
    refused(rec(root, plugins=[("plug.helper", path)]), ["plug/helper.py"])


# review round 4b: a file-less plugin name is matched against changed AND added files alike
@pytest.mark.parametrize(
    ("added", "refuses"),
    [
        ("pkg/mod.py", True),
        ("pkg/mod/__init__.py", True),
        ("PKG/MOD.py", True),
        ("vendor/pkg/mod.py", True),
        ("pkg/other.py", False),
        ("pkg/mod.txt", False),
        ("pkgx/mod.py", False),
        ("pkg/mod/conftest.py", False),
    ],
)
def test_a_file_less_plugin_name_is_matched_by_a_file_the_commit_adds(root, added, refuses):
    record = rec(root, plugins=[("pkg.mod", None)])
    if not refuses:
        check_runtime_reach(record, [], [added], base_paths=frozenset())
        return
    assert "adds" in refused_with(record, [], frozenset(), added=[added])


def test_a_file_less_plugin_name_still_matches_a_changed_file_and_says_changes(root):
    record = rec(root, plugins=[("pkg.mod", None)])
    assert "changes" in refused_with(record, ["pkg/mod.py"], frozenset({"pkg/mod.py"}))
    check_runtime_reach(record, ["pkg/other.py"], [], base_paths=frozenset({"pkg/other.py"}))
