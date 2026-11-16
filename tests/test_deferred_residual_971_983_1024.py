"""Fail-closed regression coverage for deferred node and attached-test fingerprints."""

import ast

import pytest
from conftest import commit_change, git
from test_deferred_tests import make_test_commit

import cairnwork.deferred_nodes as deferred_nodes
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import ensure_test_commit, new_tests

FILE = "tests/test_regression.py"
VISIBLE = "def test_visible():\n    assert 1\n"
CHANGED = "def test_visible():\n    assert 2 == 2\n"


def _new_tests(project, old, new):
    commit_change(project, {FILE: old})
    base = git(project, "rev-parse", "HEAD")
    _, new_commit = make_test_commit(project, {FILE: new})
    ensure_test_commit(project, new_commit, origin_url=git(project, "remote", "get-url", "origin"))
    return new_tests(project, base, new_commit, (FILE,))


@pytest.mark.parametrize(
    "assignment",
    ["Base = TestBase", "First = TestBase\nBase = First"],
)
def test_inherited_test_node_ids_resolve_same_file_alias_chains(assignment):
    src = (
        "class TestBase:\n    def test_x(self): pass\n"
        + assignment
        + "\nclass TestSub(Base):\n    pass\n"
    )
    nodes, _ = deferred_nodes.test_functions(ast.parse(src))
    assert "TestSub::test_x" in nodes


@pytest.mark.parametrize(
    "prefix,base,tail",
    [
        ("class TestBase:\n    def test_x(self): pass\n", "TestBase", "TestBase = TestB2\n"),
        (
            "class TestA:\n    def test_x(self): pass\nclass TestB:\n    pass\nBase = TestA\n",
            "Base",
            "Base = TestB\n",
        ),
    ],
)
def test_rebound_base_alias_keeps_subclass_copy(prefix, base, tail):
    src = prefix + f"class TestSub({base}):\n    pass\n" + tail
    nodes, _ = deferred_nodes.test_functions(ast.parse(src))
    assert "TestSub::test_x" in nodes


@pytest.mark.parametrize(
    "assignment",
    [
        "Base: type = TestA",
        "Base, Other = TestA, None",
        "Base = TestA = TestB",
        "(Base := TestA)",
        "if flag:\n    Base = TestA",
        "try:\n    Base = TestA\nexcept Exception:\n    pass",
    ],
)
def test_ambiguous_alias_is_conservative(assignment):
    src = (
        "class TestA:\n    def test_x(self): pass\n"
        "class TestB:\n    pass\n" + assignment + "\nclass TestSub(Base):\n    pass\n"
    )
    nodes, _ = deferred_nodes.test_functions(ast.parse(src))
    assert "TestSub::test_x" in nodes or "TestSub" in _


@pytest.mark.parametrize(
    "outer,changed",
    [
        (
            "class Base:\n    def test_x(self): pass\n"
            "class Outer(Base):\n    class TestInner:\n        def test_x(self): pass\n",
            "class Base:\n    def test_x(self): assert 1\n"
            "class Outer(Base):\n    class TestInner:\n        def test_x(self): pass\n",
        ),
        (
            "helper = 1\nclass Outer:\n    class TestInner:\n"
            "        def test_x(self): assert helper\n",
            "helper = 2\nclass Outer:\n    class TestInner:\n"
            "        def test_x(self): assert helper\n",
        ),
    ],
)
def test_nested_test_node_tracks_enclosing_base_and_module_reads(outer, changed):
    before, _ = deferred_nodes.test_functions(ast.parse(outer))
    after, _ = deferred_nodes.test_functions(ast.parse(changed))
    assert before["Outer::TestInner::test_x"] != after["Outer::TestInner::test_x"]


def test_nested_class_in_outer_conditional_is_hidden():
    source = (
        "class TestOuter:\n    if True:\n        class TestInner:\n"
        "            def test_x(self): pass\n"
    )
    assert deferred_nodes.hidden_tests(ast.parse(source))


def test_nested_test_class_outer_marker_changes_attached_test(project):
    nested = (
        "class TestOuter:\n    class TestInner:\n        def test_x(self):\n            assert 1\n"
    )
    old = "import pytest\n" + nested + VISIBLE
    new = "import pytest\n@pytest.mark.skip\n" + nested + CHANGED
    with pytest.raises(Rejected):
        _new_tests(project, old, new)


@pytest.mark.parametrize("block", ["if True:", "try:"])
def test_nested_test_class_in_outer_block_refuses_marker_change(project, block):
    if block == "try":
        body = (
            "try:\n        class TestInner:\n            def test_x(self): pass\n"
            "    finally:\n        pass"
        )
    else:
        body = "if True:\n        class TestInner:\n            def test_x(self): pass"
    old = "import pytest\nclass TestOuter:\n    " + body + "\n" + VISIBLE
    new = old.replace("class TestOuter:", "@pytest.mark.skip\nclass TestOuter:").replace(
        "assert 1", "assert 2 == 2"
    )
    with pytest.raises(Rejected):
        _new_tests(project, old, new)


@pytest.mark.parametrize("binding", ["TestAlias = Nested", "First = Nested\nTestAlias = First"])
def test_nested_test_class_alias_is_in_fingerprint(binding):
    outer = "class TestOuter:\n    class Nested:\n        def test_x(self): pass\n    "
    ordinary = deferred_nodes.hidden_tests(ast.parse(outer))
    aliased = deferred_nodes.hidden_tests(ast.parse(outer + binding + "\n"))
    assert aliased != ordinary


@pytest.mark.parametrize("namespace", ["globals()", "vars()", "locals()", "globals().__dict__"])
def test_string_key_builtin_namespace_reads_are_refused(project, namespace):
    old = f"def _impl():\n    assert add(2, 2) == 4\ntest_x = {namespace}['_impl']\n" + VISIBLE
    new = old.replace("add(2, 2) == 4", "add(2, 3) == 6").replace("assert 1", "assert 2 == 2")
    with pytest.raises(Rejected):
        _new_tests(project, old, new)


@pytest.mark.parametrize(
    "source",
    [
        "def _impl():\n    pass\nhelper = _impl\n",
        "def _impl():\n    pass\nvalue = 1\n",
        "class TestOuter:\n    value = 1\n",
    ],
)
def test_benign_same_file_name_and_data_bindings_remain_allowed(project, source):
    assert _new_tests(project, source + VISIBLE, source + CHANGED)


def test_alias_data_and_constant_subscript_stay_allowed(project):
    source = (
        "class TestBase:\n    def test_x(self): pass\n"
        "Alias = TestBase\nFirst = Alias\n"
        "DATA = {'x': 1}\nvalue = DATA['x']\n"
        "class TestSub(First):\n    pass\n"
    )
    assert _new_tests(project, source + VISIBLE, source + CHANGED)
