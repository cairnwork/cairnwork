"""Gaps left by the verifier_spec mutation survivors (C1): what the gate protects and refuses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import cairnwork.verifier_spec as verifier_spec
from cairnwork.verifier_spec import VerifierRejected, parse_verifier, protected_verifier_inputs


def _no_modules(_python: str) -> set[str]:
    return set()


# --- parse_verifier: what the refusal says -------------------------------------------------------


@pytest.mark.parametrize("argv", ["python3 -m pytest tests/test_x.py", ["python3", 7]])
def test_unusable_argv_is_refused_with_the_list_of_strings_reason(argv: Any) -> None:
    with pytest.raises(VerifierRejected) as caught:
        parse_verifier("v", argv)

    assert str(caught.value).endswith(": argv must be a list of strings")


def test_missing_target_names_every_configured_root() -> None:
    with pytest.raises(VerifierRejected) as caught:
        parse_verifier("v", ["python3", "-m", "pytest", "-q"], ("a", "b/c"))

    assert str(caught.value).endswith(": v: no a|b/c/*.py target")


# --- protected_verifier_inputs: roots, protected file names ---------------------------------------


def test_a_test_root_and_everything_under_it_stays_protected() -> None:
    paths = ("tests", "TESTS", "tests/test_x.py", "Tests/sub/data.txt", "src/tests_not.py")

    assert protected_verifier_inputs(paths, ("tests",)) == paths[:4]


def test_protected_file_names_count_in_any_directory_and_case() -> None:
    nested = ("a/b/conftest.py", "a/b/c/Setup.PY", "docs/x/PYTEST.INI", "x/y/pyproject.toml")
    harmless = ("a/b/notes.txt", "a/b/conftest.py.bak")

    assert protected_verifier_inputs((*nested, *harmless), ("tests",)) == nested


# --- _only_entry ----------------------------------------------------------------------------------


def test_only_entry_is_true_for_a_name_the_base_does_not_list() -> None:
    assert verifier_spec._only_entry(({}, set()), "pkg", "pkg") is True


def test_only_entry_is_false_when_another_entry_carries_the_name() -> None:
    project = ({"pkg": {"pkg", "src/pkg"}, "solo": {"solo"}}, set())

    assert verifier_spec._only_entry(project, "PKG", "pkg") is False
    assert verifier_spec._only_entry(project, "Solo", "SOLO") is True
    assert verifier_spec._only_entry(None, "pkg", "pkg") is False


# --- _safe_root_modules: what git reports ---------------------------------------------------------


def test_safe_root_modules_accepts_a_tracked_executable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_spec, "run_git", lambda *_a: "100755 blob abc\ttool.py\0")
    monkeypatch.setattr(verifier_spec, "_importable_names", _no_modules)

    assert verifier_spec._safe_root_modules(("tool.py",), Path("repo"), "base") == {"tool.py"}


def test_safe_root_modules_skips_the_tree_query_for_a_tracked_package_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[tuple[Any, ...]] = []

    def run_git(*args: Any) -> str:
        queries.append(args)
        return "100644 blob abc\tpkg/__init__.py\0"

    monkeypatch.setattr(verifier_spec, "run_git", run_git)
    monkeypatch.setattr(verifier_spec, "_importable_names", _no_modules)

    safe = verifier_spec._safe_root_modules(("pkg/__init__.py",), Path("repo"), "base")

    assert safe == {"pkg/__init__.py"}
    assert queries == [(Path("repo"), "ls-tree", "-z", "base", "--", "pkg/__init__.py")]


def test_safe_root_modules_asks_for_the_tree_of_an_untracked_package_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(("", "040000 tree def\tsrc/pkg\0"))
    queries: list[tuple[Any, ...]] = []

    def run_git(*args: Any) -> str:
        queries.append(args)
        return next(answers)

    monkeypatch.setattr(verifier_spec, "run_git", run_git)
    monkeypatch.setattr(verifier_spec, "_importable_names", _no_modules)

    safe = verifier_spec._safe_root_modules(
        ("src/pkg/__init__.py",), Path("repo"), "base", import_paths=("src",), project=({}, set())
    )

    assert safe == {"src/pkg/__init__.py"}
    assert queries[1] == (Path("repo"), "ls-tree", "-z", "-d", "base", "--", "src/pkg")


# --- _risky_above_roots: which directories above a root count -------------------------------------


def _risky(
    paths: tuple[str, ...],
    above: tuple[str, ...],
    *,
    entries: dict[str, set[str]],
    tracked: set[str] | None = None,
    sealed: frozenset[str] = frozenset(),
    deletions: bool = True,
) -> set[str]:
    project = (entries, tracked or set())
    return verifier_spec._risky_above_roots(paths, above, set(), "py", project, deletions, sealed)


def test_a_directory_named_like_an_init_file_is_not_an_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_spec, "_importable_names", _no_modules)

    assert _risky(("a/__init__.d/x.py",), ("a",), entries={}) == set()
    assert _risky(("a/__init__.py",), ("a",), entries={}) == {"a/__init__.py"}


def test_a_tracked_init_in_one_above_directory_does_not_hide_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_spec, "_importable_names", lambda _python: {"b"})
    path = "a/b/__init__.py"

    risky = _risky(
        (path,),
        ("a", "a/b", "A", "A/b"),
        entries={},
        tracked={path},
        sealed=frozenset({"a"}),
        deletions=False,
    )

    assert risky == {path}


def test_a_sealed_above_directory_does_not_hide_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verifier_spec, "_importable_names", _no_modules)
    path = "a/x/y.py"

    risky = _risky((path,), ("a", "A"), entries={"x": {"other"}}, sealed=frozenset({"a"}))

    assert risky == {path}


# --- _project_entries: which git paths are asked for ----------------------------------------------


def test_project_entries_limits_the_test_listing_to_every_configured_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def run_git(*args: Any) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(verifier_spec, "run_git", run_git)

    assert verifier_spec._project_entries(Path("repo"), "base", ("t1", "t2/x"), ("", "src")) == (
        {},
        set(),
    )
    assert calls[1][-2:] == ("--", "src/")
    tests_call = calls[2]
    assert tests_call[tests_call.index("--") :] == ("--", "t1/", "t2/x/")
