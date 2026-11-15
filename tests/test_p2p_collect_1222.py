"""Issue #1222: bracket characters in paths do not corrupt node id parsing."""

import pytest

from cairnwork.p2p_collect import _function, classify


@pytest.mark.parametrize(
    ("nodeid", "base", "test", "expected"),
    [
        (
            "tests/a[b]/t.py::test_y",
            "def test_y():\n    pass\n",
            "",
            "removed",
        ),
    ],
)
def test_bracket_in_directory_still_classifies_removed_test(nodeid, base, test, expected):
    path = nodeid.partition("::")[0]
    side = classify({nodeid}, set(), {path}, lambda _: test, lambda _: base)
    assert getattr(side, expected) == {nodeid}


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        (
            "tests/a[b/test_x.py::test_case[param]",
            "tests/a[b/test_x.py::test_case",
        ),
        (
            "tests/a[b]/test_x.py::test_case[param]",
            "tests/a[b]/test_x.py::test_case",
        ),
        ("tests/t.py::test_y[value]", "tests/t.py::test_y"),
        ("tests/a[b]/t.py::test_y[value]", "tests/a[b]/t.py::test_y"),
        ("tests/a[b]/t.py::test_y", "tests/a[b]/t.py::test_y"),
        ("tests/t.py::C::test_y[a]", "tests/t.py::C::test_y"),
        ("tests/t.py::TestResource[sqlite]::test_get", "tests/t.py::TestResource::test_get"),
        ("tests/t.py::TestResource[sqlite]::test_get[x]", "tests/t.py::TestResource::test_get"),
        ("tests/t.py::TestResource[a[b]]::test_get[x[y]]", "tests/t.py::TestResource::test_get"),
        ("tests/t.py::C[a::b]::test_y", "tests/t.py::C::test_y"),
        ("tests/t.py::test_y[a[b]]", "tests/t.py::test_y"),
        ("tests/t.py::test_y[x::y]", "tests/t.py::test_y"),
    ],
)
def test_parametrize_suffix_is_removed_from_function_id(nodeid, expected):
    assert _function(nodeid) == expected


def test_removed_method_in_parametrized_class_is_removed_not_dropped():
    nodeid = "tests/t.py::TestResource[sqlite]::test_get"
    side = classify(
        {nodeid},
        set(),
        {"tests/t.py"},
        lambda _: "class TestResource:\n    pass\n",
        lambda _: "class TestResource:\n    def test_get(self): pass\n",
    )
    assert side.removed == {nodeid}
    assert side.dropped == set()


def test_methods_of_parametrized_class_do_not_collide():
    first = "tests/t.py::TestResource[sqlite]::test_get"
    second = "tests/t.py::TestResource[sqlite]::test_delete"
    side = classify(
        {first, second},
        {second},
        {"tests/t.py"},
        lambda _: "class TestResource:\n    def test_delete(self): pass\n",
        lambda _: (
            "class TestResource:\n    def test_get(self): pass\n    def test_delete(self): pass\n"
        ),
    )
    assert side.removed == {first}
    assert side.dropped == set()


@pytest.mark.parametrize("nodeid", ["t.py"])
def test_malformed_node_id_is_dropped_without_raising(nodeid):
    side = classify({nodeid}, set(), {nodeid}, lambda _: "", lambda _: "def test_y(): pass\n")
    assert side.removed == set()
    assert side.dropped == {nodeid}


@pytest.mark.parametrize(
    ("present", "sibling"),
    [
        ("a[b]/test_case.py", "ab/test_case.py"),
        ("a*/test_case.py", "abc/test_case.py"),
        ("a?/test_case.py", "ab/test_case.py"),
        (":(glob)test_case.py", "test_case.py"),
    ],
    ids=["bracket", "star", "question", "magic"],
)
def test_collected_side_keeps_present_literal_path_and_detects_deleted_control(
    tmp_path, monkeypatch, present, sibling
):
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    from cairnwork import p2p_collect
    from cairnwork.collect_reader import Collected

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    for path in (present, sibling, "deleted_test.py"):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_case():\n    pass\n")
    git("add", "--all")
    git("commit", "-qm", "base")
    base_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "deleted_test.py").unlink()
    git("add", "--all")
    git("commit", "-qm", "delete control")
    test_commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def collect(task, files, *_args):
        return Collected(ids=tuple(f"{path}::test_case" for path in files))

    monkeypatch.setattr(p2p_collect, "_collect", collect)
    monkeypatch.setattr(p2p_collect, "changed_tests", lambda *args: set())
    task = SimpleNamespace(repo=Path(repo), base_commit=base_commit, test_commit=test_commit)
    base_ids = {f"{present}::test_case", "deleted_test.py::test_case"}
    side = p2p_collect.collected_side(
        task,
        (present, "deleted_test.py"),
        {node: "passed" for node in base_ids},
        (present, "deleted_test.py"),
        None,
        30,
        "attempt",
    )
    assert f"{present}::test_case" not in side.removed
    assert "deleted_test.py::test_case" in side.removed
