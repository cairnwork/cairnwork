from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import cairnwork.verifier_spec as verifier_spec
from cairnwork.verifier_spec import VerifierRejected, VerifierSpec, parse_verifier


def test_parse_verifier_only_accepts_string_argv_and_test_targets() -> None:
    spec = parse_verifier(
        "v",
        ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_x.py::test_y"],
    )

    assert spec.verifier_id == "v"
    assert spec.targets == ("tests/test_x.py::test_y",)
    for bad in (
        "python3 -m pytest tests/test_x.py",
        ["python", "-m", "pytest", "tests/test_x.py"],
        ["python3", "-m", "pytest", "tests/test_x.py", "--", "touch x"],
        ["python3", "-m", "pytest", object()],
        ["python3", "-m", "pytest", "test_x.py"],
        ["python3", "-m", "pytest", "-p"],
        ["python3", "-m", "pytest"],
    ):
        with pytest.raises(VerifierRejected):
            parse_verifier("v", bad)  # type: ignore[arg-type]


def test_parse_verifier_uses_the_configured_test_roots() -> None:
    assert parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/test_x.py"], ("pkg/tests",)
    ).targets == ("pkg/tests/test_x.py",)
    with pytest.raises(VerifierRejected):
        parse_verifier("v", ["python3", "-m", "pytest", "tests/test_x.py"], ("pkg/tests",))


def test_verifier_flags_include_only_configured_collect_controls() -> None:
    spec = VerifierSpec(
        "v", ("tests/test_x.py",), continue_on_collect_errors=True, ignore=("tests/skip",)
    )

    assert spec.flags() == (
        "--continue-on-collection-errors",
        "--ignore=tests/skip",
    )
    assert VerifierSpec("v", (), collect_only=True, announce=False).flags() == ()
    assert "--collect-only" in VerifierSpec("v", (), collect_only=True).flags()


def test_dirs_above_roots_lists_each_parent_without_the_root() -> None:
    assert verifier_spec._dirs_above_roots(("a/b/tests", "tests", "a/b/tests")) == (
        "a",
        "a/b",
    )


def test_protected_inputs_forward_verifier_context_and_default_deletion_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, tuple[Any, ...]] = {}

    def sealed(*args: Any) -> frozenset[str]:
        calls["sealed"] = args
        return frozenset({"pkg"})

    monkeypatch.setattr(
        verifier_spec,
        "_first_level_unit",
        lambda *_args: ("pkg/calc.py", "", "calc", False, ""),
    )

    def project(*args: Any) -> tuple[dict[str, set[str]], set[str]]:
        calls["project"] = args
        return {}, set()

    def safe(*args: Any, **kwargs: Any) -> set[str]:
        calls["safe"] = (args, kwargs)
        return set()

    monkeypatch.setattr(verifier_spec, "sealed_packages", sealed)
    monkeypatch.setattr(verifier_spec, "_project_entries", project)
    monkeypatch.setattr(verifier_spec, "_safe_root_modules", safe)

    def risky(*args: Any) -> set[str]:
        calls["risky"] = args
        return set()

    def shadows(*args: Any, **kwargs: Any) -> set[str]:
        calls["shadows"] = (args, kwargs)
        return set()

    monkeypatch.setattr(verifier_spec, "_risky_above_roots", risky)
    monkeypatch.setattr(verifier_spec, "namespace_shadows", shadows)
    repo = Path("repo")

    result = verifier_spec.protected_verifier_inputs(
        ("pkg/calc.py",),
        ("pkg/tests",),
        repo=repo,
        base_commit="base",
        python="python-custom",
        import_paths=(),
        test_commit="test",
    )

    assert result == ("pkg/calc.py",)
    assert calls["sealed"] == (repo, "base", ("pkg/tests",), ("pkg",), ("pkg/calc.py",), "test")
    assert calls["project"] == (
        repo,
        "base",
        ("pkg/tests",),
        ("",),
        frozenset({"pkg"}),
    )
    assert calls["risky"][3:6] == ("python-custom", ({}, set()), True)
    assert calls["risky"][6] == frozenset({"pkg"})
    assert calls["safe"][0][1:4] == (repo, "base", "python-custom")
    assert calls["shadows"][1] == {
        "repo": repo,
        "base_commit": "base",
        "python": "python-custom",
        "allow_deletions": True,
    }


def test_project_entries_reads_root_import_and_test_tree_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = iter(
        (
            "040000 tree abc\trootpkg\0",
            "040000 tree 987\tdemo.py\0"
            "040000 tree 654\t__init__\0"
            "100644 blob def\tsrc/calc.py\0"
            "100755 blob 456\tsrc/tool.py\0"
            "120000 blob 789\tsrc/linked.py\0",
            "tests/helpers/tool.py\0",
        )
    )
    calls: list[tuple[Any, ...]] = []

    def fake_run_git(*args: Any) -> str:
        calls.append(args)
        return next(rows)

    monkeypatch.setattr(verifier_spec, "run_git", fake_run_git)
    result = verifier_spec._project_entries(Path("repo"), "base", ("tests",), ("", "src"))

    assert result == (
        {
            "rootpkg": {"rootpkg"},
            "demo.py": {"demo.py"},
            "calc": {"src/calc.py"},
            "tool": {"src/tool.py", "tests/helpers/tool.py"},
            "linked": {"src/linked.py"},
            "tests": {"tests"},
            "helpers": {"tests/helpers"},
        },
        {"src/calc.py", "src/tool.py"},
    )
    assert len(calls) == 3
    assert calls[1][2:] == ("-z", "base", "--", "src/")
    assert calls[2][2:8] == ("-r", "-z", "--name-only", "base", "--", "tests/")


def test_project_entries_does_not_descend_into_sealed_test_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = iter(("", "tests/helpers/tool.py\0"))
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_args: next(rows))

    result = verifier_spec._project_entries(
        Path("repo"), "base", ("tests",), ("",), frozenset({"tests/helpers"})
    )

    assert result == ({"tests": {"tests"}, "helpers": {"tests/helpers"}}, set())


def test_project_entries_extracts_the_leaf_of_nested_import_root_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = iter(("", "100644 blob abc\tsrc/pkg/demo.py\0", ""))
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_args: next(rows))

    result = verifier_spec._project_entries(Path("repo"), "base", (), ("src/pkg",))

    assert result == ({"demo": {"src/pkg/demo.py"}}, {"src/pkg/demo.py"})


def test_project_entries_splits_git_metadata_once_when_paths_contain_tabs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = iter(("100644 blob abc\todd\tname.py\0", ""))
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_args: next(rows))

    assert verifier_spec._project_entries(Path("repo"), "base", (), ()) == (
        {"odd\tname": {"odd\tname.py"}},
        {"odd\tname.py"},
    )


def test_project_entries_requires_both_git_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_git_call(*_args: Any) -> str:
        raise AssertionError("git must not be queried without both coordinates")

    monkeypatch.setattr(verifier_spec, "run_git", unexpected_git_call)
    assert verifier_spec._project_entries(None, "base", (), ()) is None
    assert verifier_spec._project_entries(Path("repo"), None, (), ()) is None


def test_first_level_units_and_pth_are_limited_to_one_import_level() -> None:
    roots = verifier_spec._import_roots(("src/",))

    assert roots == ("", "src")
    assert verifier_spec._first_level_unit("calc.py", roots) == (
        "calc.py",
        "",
        "calc",
        False,
        "",
    )
    assert verifier_spec._first_level_unit("pkg/__init__.py", roots) == (
        "pkg/__init__.py",
        "",
        "pkg",
        True,
        "pkg",
    )
    assert verifier_spec._first_level_unit("src/calc.py", roots) == (
        "src/calc.py",
        "src",
        "calc",
        False,
        "",
    )
    assert verifier_spec._first_level_unit("src/pkg/__INIT__.py", roots) == (
        "src/pkg/__INIT__.py",
        "src",
        "pkg",
        True,
        "src/pkg",
    )
    assert verifier_spec._first_level_unit("src/pkg/impl.py", roots) is None
    assert verifier_spec._is_first_level_pth("custom.pth", roots)
    assert verifier_spec._is_first_level_pth("src/custom.PTH", roots)
    assert not verifier_spec._is_first_level_pth("src/pkg/custom.pth", roots)


def test_safe_root_modules_needs_tracked_file_or_unique_package_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = iter(("100644 blob abc\tcalc.py\0", "", "040000 tree def\tpkg\0"))
    queries: list[tuple[Any, ...]] = []

    def run_git(*args: Any) -> str:
        queries.append(args)
        return next(entries)

    monkeypatch.setattr(verifier_spec, "run_git", run_git)
    monkeypatch.setattr(verifier_spec, "_importable_names", lambda _python: set())
    project = ({"pkg": {"pkg"}}, {"calc.py"})

    assert verifier_spec._safe_root_modules(
        ("calc.py",), Path("repo"), "base", project=project
    ) == {"calc.py"}
    assert verifier_spec._safe_root_modules(
        ("pkg/__init__.py",), Path("repo"), "base", project=project
    ) == {"pkg/__init__.py"}
    assert queries == [
        (Path("repo"), "ls-tree", "-z", "base", "--", "calc.py"),
        (Path("repo"), "ls-tree", "-z", "base", "--", "pkg/__init__.py"),
        (Path("repo"), "ls-tree", "-z", "-d", "base", "--", "pkg"),
    ]
    tree_entries = iter(("", "040000 tree def\tpkg\0"))
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_args: next(tree_entries))
    duplicate_project = ({"pkg": {"pkg", "src/pkg"}}, set())
    assert (
        verifier_spec._safe_root_modules(
            ("pkg/__init__.py",), Path("repo"), "base", project=duplicate_project
        )
        == set()
    )
    assert verifier_spec._safe_root_modules(("calc.py",), None, "base") == set()
    assert verifier_spec._safe_root_modules(("calc.py",), Path("repo"), None) == set()


def test_safe_root_modules_parses_git_metadata_before_unusual_path_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_args: "100644 blob abc\todd\tname.py\0")
    monkeypatch.setattr(verifier_spec, "_importable_names", lambda _python: set())

    assert verifier_spec._safe_root_modules(("odd\tname.py",), Path("repo"), "base") == set()
