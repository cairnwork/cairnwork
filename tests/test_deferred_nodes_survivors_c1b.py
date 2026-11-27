"""Regression probes for hidden binding and local module-name survivors."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from cairnwork.deferred_nodes import hidden_tests, local_names
from cairnwork.deferred_nodes import test_functions as read_functions


def _hidden(source: str, local: frozenset[str] | None = None) -> frozenset[str]:
    return hidden_tests(ast.parse(source), local)


def test_walrus_for_and_with_targets_bind_hidden_tests():
    source = (
        "if (test_walrus := make()): pass\n"
        "for TestLoop in values(): pass\n"
        "with manager() as test_with: pass\n"
    )
    got = _hidden(source)
    assert len(got) == 3
    assert any("test_walrus" in item for item in got)
    assert any("TestLoop" in item for item in got)
    assert any("test_with" in item for item in got)


def test_imported_testcase_counts_only_for_local_module_tree():
    source = (
        "from helpers.case import TestCase\nclass Checks(TestCase):\n    def test_x(self): pass\n"
    )
    assert set(read_functions(ast.parse(source))[0]) == {"Checks::test_x"}
    assert _hidden(source, frozenset({"helpers"}))
    assert _hidden(source, frozenset({"unrelated"})) == frozenset()


def test_local_names_reports_py_stems_and_nested_directories(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "tests" / "deep").mkdir(parents=True)
    (repo / "tests" / "deep" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "rootmod.py").write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
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
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert local_names(repo, commit) == frozenset({"tests", "deep", "helper", "rootmod"})


def test_inherited_methods_digest_accumulates_each_base_version():
    old = (
        "class TestA:\n    def test_x(self): pass\n"
        "class TestB:\n    def test_x(self): assert True\n"
        "class TestC(TestA, TestB):\n    pass\n"
    )
    new = old.replace("assert True", "assert False")
    before, after = (read_functions(ast.parse(s))[0] for s in (old, new))
    assert before.keys() == after.keys()
    assert before["TestC::test_x"] != after["TestC::test_x"]


def test_non_testcase_dotted_base_remains_conservative_for_inheritance():
    source = (
        "class TestBase:\n    def test_x(self): pass\n"
        "class Other(module.Base):\n    def test_y(self): pass\n"
    )
    nodes, _ = read_functions(ast.parse(source))
    assert "Other::test_x" not in nodes


# equivalent: line 127 else value is empty string; non-Name bases resolve through _base_name,
# while this target is used only for same-file plain-name class lookup.
# equivalent: line 128 aliases lookup on a non-Name base; target is always empty in that case.
# equivalent: line 134 default affects only absent method keys; assignment occurs after own keys
# are built and inherited names are absent by the name guard.
# equivalent: line 135 (target or not target.endswith(...)) simplifies to true for empty target,
# which only changes entry into conservative scan for non-Name bases; those target names cannot
# resolve to external imports and scan is intentionally conservative already.
