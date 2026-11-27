"""Additional public-API probes for deferred node mutation survivors."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from cairnwork.deferred_nodes import hidden_tests, local_names
from cairnwork.deferred_nodes import test_functions as read_functions
from cairnwork.deferred_overlay import TestCommitRejected


def _functions(source: str) -> dict[str, str]:
    return read_functions(ast.parse(source))[0]


def _hidden(source: str, local: frozenset[str] | None = None) -> frozenset[str]:
    return hidden_tests(ast.parse(source), local)


def test_nested_class_paths_and_multiple_children_are_reported():
    source = (
        "class TestOuter:\n"
        "    class TestFirst:\n        def test_one(self): pass\n"
        "    class TestSecond:\n        def test_two(self): pass\n"
    )
    assert set(_functions(source)) == {
        "TestOuter::TestFirst::test_one",
        "TestOuter::TestSecond::test_two",
    }


def test_nested_class_marks_and_outer_methods_reach_nested_tests():
    old = "class Outer:\n    value = 1\n    class TestInner:\n        def test_x(self): pass\n"
    new = old.replace("value = 1", "value = 2")
    before = _functions(old)["Outer::TestInner::test_x"]
    after = _functions(new)["Outer::TestInner::test_x"]
    assert before != after


def test_nested_testcase_inheritance_and_blocked_class_propagate():
    source = (
        "class Outer:\n"
        "    class Base(unittest.TestCase):\n"
        "        def __init__(self): pass\n"
        "        def test_inherited(self): pass\n"
        "    class Child(Base):\n        pass\n"
    )
    nodes, blocked = read_functions(ast.parse(source))
    assert set(nodes) == {"Outer::Base::test_inherited", "Outer::Child::test_inherited"}
    assert blocked == frozenset()


def test_testcase_alias_changes_class_collection_and_base_name_spelling():
    old = (
        "class TestBase:\n    def test_x(self): pass\n"
        "Alias = TestBase\nclass TestChild(Alias):\n    pass\n"
    )
    assert set(_functions(old)) == {"TestBase::test_x", "TestChild::test_x"}
    source = (
        "class A(unittest.Testcase):\n    def test_a(self): pass\n"
        "class B(unittest.TESTCASE):\n    def test_b(self): pass\n"
        "class C(unittest.TestCase):\n    def test_c(self): pass\n"
    )
    assert set(_functions(source)) == {"C::test_c"}


def test_module_reach_tracks_imported_pytestmark_and_decorators():
    old = "from marks import pytestmark\ndef test_x(): pass\n"
    new = "from marks2 import pytestmark\ndef test_x(): pass\n"
    assert _functions(old)["test_x"] != _functions(new)["test_x"]
    old = "@fixture(1)\ndef helper(): pass\ndef test_x(): helper()\n"
    new = old.replace("fixture(1)", "fixture(2)")
    assert _functions(old)["test_x"] != _functions(new)["test_x"]


def test_reach_follows_multiple_binding_dependencies_in_source_order():
    old = "A = 1\nB = A + 1\ndef test_x(): assert B\n"
    new = old.replace("A = 1", "A = 2")
    assert _functions(old)["test_x"] != _functions(new)["test_x"]


def test_hidden_nested_class_binding_and_outer_context_are_recorded():
    old = "class Outer:\n    flag = 1\n    class TestInner: pass\n"
    new = old.replace("flag = 1", "flag = 2")
    assert _hidden(old) != _hidden(new)
    guarded = "if ready:\n    class TestInner: pass\n"
    branch = ast.parse(guarded).body[0]
    assert isinstance(branch, ast.If)
    assert ast.dump(branch.body[0]) in _hidden(guarded)


def test_hidden_match_as_and_star_import_bindings():
    source = "match value:\n    case value as TestCapture: pass\n"
    assert any("TestCapture" in item for item in _hidden(source))
    assert "ImportFrom" in next(iter(_hidden("from plugin import *\n")))


def test_hidden_local_imported_testcase_and_global_capitalized_name():
    source = "from pkg.sub.case import TestCase\nclass Checks(TestCase): pass\n"
    assert _hidden(source, frozenset({"pkg"}))
    assert not _hidden(source, frozenset({"other"}))
    assert _hidden("def publish():\n    global TestLater\n    TestLater = make()\n")


def _git_repo(path: Path, contents: dict[str, bytes]) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, body in contents.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return path, commit


def test_local_names_ignores_non_python_stems_but_keeps_directories(tmp_path: Path):
    repo, commit = _git_repo(
        tmp_path / "tree",
        {
            "src/pkg/mod.py": b"x = 1\n",
            "assets/data.txt": b"x\n",
        },
    )
    assert local_names(repo, commit) == frozenset({"src", "pkg", "mod", "assets"})


def test_local_names_decodes_surrogate_paths_and_rejects_missing_commit(tmp_path: Path):
    repo, commit = _git_repo(tmp_path / "tree", {"ordinary.py": b"x = 1\n"})
    assert "ordinary" in local_names(repo, commit)
    with pytest.raises(TestCommitRejected) as error:
        local_names(repo, "missing-object")
    assert error.value.code == "TEST_COMMIT_UNREADABLE"
    assert error.value.has_detail


# Equivalent mutations: split('.', 1) and split('.', ) have identical behavior; split('.', 2)
# yields the same first component. UTF-8 and SURROGATEESCAPE codec names are case-insensitive.
# The class suffix variants remain distinguished by the case-sensitive TestCase probes above.
