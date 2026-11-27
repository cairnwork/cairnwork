"""Focused probes for the final deferred-node AST reader survivors."""

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


def _git_repo(path: Path, files: dict[str, str]) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for name, content in files.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
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


def test_alias_map_is_passed_and_used_for_class_bases():
    source = (
        "class TestBase:\n    def test_run(self): pass\n"
        "class TestOther:\n    def test_other(self): pass\n"
        "Alias = TestBase\nclass TestChild(Alias):\n    pass\n"
    )
    nodes = _functions(source)
    assert set(nodes) == {
        "TestBase::test_run",
        "TestOther::test_other",
        "TestChild::test_run",
    }
    assert nodes["TestBase::test_run"] != nodes["TestChild::test_run"]


def test_unresolved_base_carries_uncollected_state_from_prior_classes():
    source = (
        "class Fixture(unittest.TestCase):\n"
        "    def __init__(self): pass\n"
        "class TestRunner(UnknownBase):\n"
        "    def test_run(self): pass\n"
    )
    nodes, blocked = read_functions(ast.parse(source))
    assert set(nodes) == {"TestRunner::test_run"}
    assert blocked == frozenset({"Fixture", "TestRunner"})


def test_unresolved_base_fingerprint_accumulates_each_prior_test_method():
    old = (
        "class TestOne:\n    def test_x(self): assert 1\n"
        "class TestTwo:\n    def test_x(self): assert 2\n"
        "class TestChild(UnknownBase):\n    pass\n"
    )
    new = old.replace("assert 1", "assert 3")
    before, after = _functions(old), _functions(new)
    assert "TestChild::test_x" in before
    assert before["TestChild::test_x"] != after["TestChild::test_x"]


def test_blocked_nested_test_class_uses_own_shell_without_outer_reach():
    old = (
        "class Outer:\n    value = 1\n"
        "    class TestInner:\n        def __init__(self): pass\n"
        "        def test_x(self): pass\n"
    )
    new = old.replace("value = 1", "value = 2")
    before, after = _functions(old), _functions(new)
    assert before.keys() == after.keys() == {"Outer::TestInner::test_x"}
    assert before["Outer::TestInner::test_x"] == after["Outer::TestInner::test_x"]


def test_nested_member_traversal_retains_path_and_function_context():
    source = (
        "class TestOuter:\n"
        "    class TestMiddle:\n"
        "        class TestInner:\n            def test_x(self): pass\n"
    )
    assert set(_functions(source)) == {"TestOuter::TestMiddle::TestInner::test_x"}


def test_reach_reads_dependencies_from_each_reached_statement():
    old = (
        "import package_left as LEFT\n"
        "import package_right as RIGHT\n"
        "@LEFT(RIGHT)\ndef test_x(): pass\n"
    )
    before = _functions(old)["test_x"]
    for imported_module in ("package_left", "package_right"):
        new = old.replace(imported_module, "changed_module")
        assert before != _functions(new)["test_x"]


@pytest.mark.parametrize(
    "import_line",
    ["import package.sub"],
)
def test_dotted_import_binds_the_top_level_name(import_line: str):
    with_import = f"{import_line}\n@package.decorator\ndef test_x(): pass\n"
    without_import = "@package.decorator\ndef test_x(): pass\n"
    assert _functions(with_import)["test_x"] != _functions(without_import)["test_x"]


def test_nested_hidden_test_includes_its_enclosing_class_shell():
    source = "class Outer:\n    class Inner:\n        class TestDeep: pass\n"
    got = _hidden(source)
    assert ast.dump(ast.parse(source).body[0]) in got


def test_non_statement_child_keeps_the_enclosing_statement_for_hidden_binding():
    source = "if ready and (test_value := make()):\n    pass\n"
    got = _hidden(source)
    assert ast.dump(ast.parse(source).body[0]) in got


def test_local_imported_testcase_is_filtered_during_recursive_hidden_scan():
    source = "if enabled:\n    from unittest import TestCase\n"
    assert _hidden(source, frozenset({"another_local_module"})) == frozenset()


def test_match_mapping_without_rest_does_not_create_a_binding():
    source = "match value:\n    case {'x': item}:\n        pass\n"
    assert _hidden(source) == frozenset()


def test_annotation_without_value_does_not_bind_a_hidden_test():
    assert _hidden("test_value: int\n") == frozenset()


def test_local_names_handles_a_dash_prefixed_tracked_path(tmp_path: Path):
    repo, commit = _git_repo(tmp_path / "dashrepo", {"-l": "VALUE = 1\n"})
    assert local_names(repo, commit) == frozenset()


def test_unreadable_commit_preserves_git_failure_detail(tmp_path: Path):
    repo, _ = _git_repo(tmp_path / "badcommit", {"helper.py": "VALUE = 1\n"})
    with pytest.raises(TestCommitRejected) as error:
        local_names(repo, "not-a-real-object")
    assert error.value.code == "TEST_COMMIT_UNREADABLE"
    assert error.value.has_detail
    assert "not-a-real-object" in str(error.value)


def test_call_expression_base_class_does_not_crash_the_reader():
    source = "class TestA(make()):\n    def test_x(self): pass\n"
    assert "TestA::test_x" in _functions(source)


# equivalent: line 84 aliases.get(id(node), ) omits the default, which is exactly {} here.
# equivalent: line 84 aliases.get(id(node), None) passes None, normalized to {} by _members.
# equivalent: line 354 split(".", ) uses the default maxsplit; split(".", 2) has the same
# first component as split(".", 1) for every string.
# equivalent: line 382's trailing separator does not change the paths returned by git's
# ls-tree for a dash-prefixed tracked path through run_git_bytes.


def test_local_names_reads_a_non_utf8_path_without_failing(tmp_path: Path) -> None:
    repo = tmp_path / "raw"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "T"],
        ["config", "user.email", "t@x.invalid"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"x\n",
        check=True,
        capture_output=True,
    ).stdout.strip()
    entries = b"100644 blob " + blob + b"\tbad\xff.py\x00" + b"100644 blob " + blob + b"\tok.py\x00"
    tree = subprocess.run(
        ["git", "-C", str(repo), "mktree", "-z"], input=entries, check=True, capture_output=True
    ).stdout.strip()
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit-tree", tree.decode(), "-m", "raw"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    names = local_names(repo, commit)
    assert "ok" in names
    assert any(name.startswith("bad") for name in names)


# equivalent (no input changes any output): lines 134/143/166/167/202/206 and 90 the "XXXX"
# separators and defaults only change digests uniformly on both sides of a comparison; lines
# 200/221/222/228 the cache keys (None / id(None)) only cost a cache miss; line 260 the base-name
# default (a falsy value is skipped by the `target and` guard); line 298 type_ignores is unread;
# line 354 split maxsplit 2 or default and the "XXXX" module default give the same first part or
# a name no tree has; line 382 the `--` operand follows a full 40-hex commit; line 386 "UTF-8" is
# the same codec; line 84 the default of aliases.get is always replaced when the node is absent.
