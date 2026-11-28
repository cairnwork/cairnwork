"""C1 mutation gaps: nested AST contexts, global Test* bindings, and raw Git paths."""

import ast

import pytest

import cairnwork.deferred_nodes as deferred_nodes
from cairnwork.deferred_overlay import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError


def test_deep_nested_test_fingerprint_tracks_each_enclosing_class() -> None:
    before = ast.parse(
        "class TestOuter:\n"
        "    class Container:\n"
        "        class TestDeep:\n"
        "            def test_value(self): pass\n"
    )
    changed = ast.parse(
        "class TestOuter:\n"
        "    @marker\n"
        "    class Container:\n"
        "        class TestDeep:\n"
        "            def test_value(self): pass\n"
    )

    assert deferred_nodes.hidden_tests(before) != deferred_nodes.hidden_tests(changed)


def test_nested_test_class_keeps_full_node_id() -> None:
    tree = ast.parse(
        "class TestBase:\n"
        "    def test_base(self): pass\n"
        "class TestOuter:\n"
        "    marker = 1\n"
        "    class Container:\n"
        "        class TestDeep(TestBase):\n"
        "            def test_value(self): pass\n"
    )

    functions, _ = deferred_nodes.test_functions(tree)

    assert "TestOuter::Container::TestDeep::test_base" in functions
    assert "TestOuter::Container::TestDeep::test_value" in functions


def test_global_test_class_binding_is_included_in_hidden_fingerprint() -> None:
    before = ast.parse("def setup():\n    global TestGenerated\n    TestGenerated = None\n")
    changed = ast.parse(
        "def setup():\n"
        "    global TestGenerated\n"
        "    TestGenerated = type('TestGenerated', (), {'test_x': lambda self: None})\n"
    )

    assert deferred_nodes.hidden_tests(before) != deferred_nodes.hidden_tests(changed)


def test_annotation_only_test_name_is_not_a_hidden_test_binding() -> None:
    tree = ast.parse("test_data: object\n")

    assert deferred_nodes.hidden_tests(tree) == frozenset()


def test_imported_module_pytestmark_is_part_of_test_fingerprint() -> None:
    before = deferred_nodes.test_functions(
        ast.parse("from marks_a import pytestmark\ndef test_x(): pass\n")
    )[0]
    changed = deferred_nodes.test_functions(
        ast.parse("from marks_b import pytestmark\ndef test_x(): pass\n")
    )[0]

    assert before["test_x"] != changed["test_x"]


def test_match_wildcard_does_not_create_a_binding() -> None:
    tree = ast.parse("match value:\n    case _:\n        pass\n")

    assert deferred_nodes.hidden_tests(tree) == frozenset()


def test_match_mapping_rest_capture_named_test_is_hidden() -> None:
    tree = ast.parse("match value:\n    case {'key': _, **test_extra}:\n        pass\n")

    assert deferred_nodes.hidden_tests(tree)


def test_match_mapping_without_rest_capture_is_not_hidden() -> None:
    tree = ast.parse("match value:\n    case {'key': _}:\n        pass\n")

    assert deferred_nodes.hidden_tests(tree) == frozenset()


def test_local_names_preserves_non_utf8_git_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        deferred_nodes,
        "run_git_bytes",
        lambda *args: b"pkg/\xff_module.py\x00",
    )

    assert deferred_nodes.local_names(tmp_path, "deadbeef") == frozenset({"pkg", "\udcff_module"})


def test_local_names_keeps_the_unreadable_commit_error_code(monkeypatch, tmp_path) -> None:
    def unreadable(*args):
        raise GitCommandError("synthetic failure")

    monkeypatch.setattr(deferred_nodes, "run_git_bytes", unreadable)

    with pytest.raises(Rejected) as caught:
        deferred_nodes.local_names(tmp_path, "deadbeef")

    assert caught.value.code == "TEST_COMMIT_UNREADABLE"


def test_hidden_testcase_from_deep_local_module_is_detected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        deferred_nodes,
        "run_git_bytes",
        lambda *args: b"pkg/sub/framework.py\x00",
    )
    local = deferred_nodes.local_names(tmp_path, "deadbeef")
    tree = ast.parse("from pkg.sub.framework import TestCase\n")

    assert deferred_nodes.hidden_tests(tree, local)
