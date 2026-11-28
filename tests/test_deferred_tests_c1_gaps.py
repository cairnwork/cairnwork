"""Pin deferred_tests behaviour that mutation testing left unchecked (C1): the root check on the
base, the test commit's path rules, the exposure and fetch refusals, and the overlay commit.
Every refusal is pinned by its code and its full text, since the author reads both."""

import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import git

import cairnwork.deferred_tests as dt
from cairnwork import gitprobe
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError

BAD = "TEST_COMMIT_SCOPE"


def mkcommit(repo: Path, files: dict[str, Any], parent: str | None = None) -> str:
    """A commit built with plumbing only (no checkout), so any path or mode can be made.
    ``files``: path -> text, (mode, text), (mode, oid) for a gitlink, or None to remove."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo.parent / f"idx-{uuid.uuid4().hex}")}

    def run(*args: str, stdin: str | None = None) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                              capture_output=True, text=True, input=stdin)  # fmt: skip
        return done.stdout.strip()

    if parent:
        run("read-tree", parent)
    for path, spec in files.items():
        if spec is None:
            run("update-index", "--force-remove", path)
            continue
        mode, content = spec if isinstance(spec, tuple) else ("100644", spec)
        oid = content if mode == "160000" else run("hash-object", "-w", "--stdin", stdin=content)
        run("update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
    parents = ["-p", parent] if parent else []
    return run("commit-tree", run("write-tree"), "-m", "c", *parents)


def base_of(repo: Path, files: dict[str, Any]) -> str:
    return mkcommit(repo, files, git(repo, "rev-parse", "HEAD"))


def spy(monkeypatch, fail=None, reply=None) -> list[tuple[Any, ...]]:
    """Record every run_git call of deferred_tests; ``fail(args)`` makes one raise (with its text,
    if a str), ``reply`` (args -> str | None) answers it, otherwise the real git runs."""
    calls: list[tuple[Any, ...]] = []
    real = dt.run_git

    def run_git(repo: Path, *args: Any, **kwargs: Any) -> str:
        calls.append(args)
        if fail and (why := fail(args)):
            raise GitCommandError(why if isinstance(why, str) else "boom")
        answer = reply(args) if reply else None
        return real(repo, *args, **kwargs) if answer is None else answer

    monkeypatch.setattr(dt, "run_git", run_git)
    return calls


def refusal(call, *args: Any, **kwargs: Any) -> Rejected:
    with pytest.raises(Rejected) as caught:
        call(*args, **kwargs)
    return caught.value


# ---- check_roots_on_base ----------------------------------------------------------------


def test_roots_check_lists_each_level_of_a_nested_root_with_the_found_names(repo, monkeypatch):
    base = base_of(repo, {"Tests/unit/deep/test_a.py": "x = 1\n"})
    calls = spy(monkeypatch)
    dt.check_roots_on_base(repo, base, ("tests/unit/deep",))
    assert calls == [
        ("ls-tree", "-z", base),
        ("ls-tree", "-z", base, "--", "Tests/"),
        ("ls-tree", "-z", base, "--", "Tests/unit/"),
    ]


def test_roots_check_accepts_real_directories_and_absent_roots(repo):
    base = base_of(repo, {"my tests/a.py": "x = 1\n"})
    assert dt.check_roots_on_base(repo, base, ("my tests", "absent/deeper", "tests")) is None


def test_roots_check_reads_a_missing_part_then_moves_on_to_the_next_root(repo):
    base = base_of(repo, {"tests": "a file, not a directory\n"})
    err = refusal(dt.check_roots_on_base, repo, base, ("absent", "tests"))
    assert err.code == "TEST_ROOT_NOT_A_DIRECTORY"


def test_roots_check_refuses_a_file_where_the_root_should_be(repo):
    base = base_of(repo, {"tests": "a file\n"})
    err = refusal(dt.check_roots_on_base, repo, base, ("tests",))
    assert err.code == "TEST_ROOT_NOT_A_DIRECTORY"
    assert str(err) == "TEST_ROOT_NOT_A_DIRECTORY: ['tests'] on the base"


def test_roots_check_names_the_checked_prefix_of_a_deep_root(repo):
    base = base_of(repo, {"a/b/c/file": "x\n", "a/b/c/d": "a file\n"})
    err = refusal(dt.check_roots_on_base, repo, base, ("a/b/c/d/e",))
    assert str(err) == "TEST_ROOT_NOT_A_DIRECTORY: ['a', 'b', 'c', 'd'] on the base"


def test_roots_check_follows_a_directory_name_with_a_space_down_the_path(repo):
    base = base_of(repo, {"my tests/unit": "a file\n"})
    err = refusal(dt.check_roots_on_base, repo, base, ("my tests/unit",))
    assert str(err) == "TEST_ROOT_NOT_A_DIRECTORY: ['my tests', 'unit'] on the base"


def test_roots_check_refuses_two_case_variants_of_a_directory(repo):
    base = base_of(repo, {"Tests/a.py": "x\n", "tests/a.py": "x\n"})
    err = refusal(dt.check_roots_on_base, repo, base, ("tests",))
    assert err.code == "TEST_ROOT_NOT_A_DIRECTORY"


def test_roots_check_refuses_a_symlink_where_the_root_should_be(repo):
    base = base_of(repo, {"tests": ("120000", "elsewhere")})
    err = refusal(dt.check_roots_on_base, repo, base, ("tests",))
    assert err.code == "TEST_ROOT_NOT_A_DIRECTORY"


def test_an_unreadable_base_is_a_root_refusal_with_gits_reason(repo, monkeypatch):
    spy(monkeypatch, fail=lambda args: True)
    err = refusal(dt.check_roots_on_base, repo, "base", ("tests",))
    assert err.code == "TEST_ROOT_UNREADABLE"
    assert str(err) == "TEST_ROOT_UNREADABLE: GIT_COMMAND_FAILED: boom"
    assert isinstance(err.__cause__, GitCommandError)


# ---- test_paths -------------------------------------------------------------------------


def paths_of(repo: Path, files: dict[str, Any]):
    base = git(repo, "rev-parse", "HEAD")
    return base, mkcommit(repo, files, base)


def test_a_test_commit_reads_the_diff_between_base_and_commit_without_renames(repo, monkeypatch):
    base, tc = paths_of(repo, {"tests/test_a.py": "def test_a(): pass\n"})
    calls = spy(monkeypatch)
    assert dt.test_paths(repo, base, tc) == (("tests/test_a.py",), ())
    assert calls[1] == (
        "diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", "--raw",
        "--ignore-submodules=none", base, tc,
    )  # fmt: skip


def test_a_move_is_one_deleted_and_one_added_path(repo):
    base = base_of(repo, {"tests/test_old.py": "def test_a(): pass\n"})
    tc = mkcommit(repo, {"tests/test_old.py": None, "tests/test_new.py": "def test_a(): pass\n"},
                  base)  # fmt: skip
    assert dt.test_paths(repo, base, tc) == (("tests/test_new.py",), ("tests/test_old.py",))


def test_a_gitlink_under_tests_is_refused_whatever_gitmodules_says(repo):
    base = base_of(repo, {".gitmodules": '[submodule "tests/sub"]\n\tpath = tests/sub\n'
                          "\tignore = all\n"})  # fmt: skip
    head = git(repo, "rev-parse", "HEAD")
    tc = mkcommit(repo, {"tests/test_a.py": "def test_a(): pass\n",
                         "tests/sub": ("160000", head)}, base)  # fmt: skip
    err = refusal(dt.test_paths, repo, base, tc)
    assert str(err) == f"{BAD}: A 160000 tests/sub"


def test_an_unknown_base_is_an_unreadable_test_commit(repo):
    tc = mkcommit(repo, {"tests/test_a.py": "def test_a(): pass\n"}, git(repo, "rev-parse", "HEAD"))
    err = refusal(dt.test_paths, repo, "1" * 40, tc)
    assert err.code == "TEST_COMMIT_UNREADABLE"
    assert str(err).startswith("TEST_COMMIT_UNREADABLE: GIT_COMMAND_FAILED: ")


def test_git_failures_name_the_unreadable_commit_with_gits_reason(repo, monkeypatch):
    base, tc = paths_of(repo, {"tests/test_a.py": "def test_a(): pass\n"})
    spy(monkeypatch, fail=lambda args: args[0] == "diff-tree")
    err = refusal(dt.test_paths, repo, base, tc)
    assert str(err) == "TEST_COMMIT_UNREADABLE: GIT_COMMAND_FAILED: boom"
    assert isinstance(err.__cause__, GitCommandError)


def test_a_commit_not_on_the_base_says_so(repo):
    base = git(repo, "rev-parse", "HEAD")
    other = base_of(repo, {"src/c.py": "z = 1\n"})
    tc = mkcommit(repo, {"tests/test_a.py": "def test_a(): pass\n"}, other)
    err = refusal(dt.test_paths, repo, base, tc)
    assert str(err) == "TEST_COMMIT_NOT_ON_BASE: its only parent must be the base"


def test_a_test_commit_reads_its_roots_for_the_verifier_target_rule(repo):
    base, tc = paths_of(repo, {"t.est/test_a.py": "def test_a(): pass\n"})
    assert dt.test_paths(repo, base, tc, ("t.est",)) == (("t.est/test_a.py",), ())


def test_a_symlink_or_other_mode_is_refused_by_its_new_mode(repo):
    base, tc = paths_of(repo, {"tests/link": ("120000", "../src/a.py")})
    assert str(refusal(dt.test_paths, repo, base, tc)) == f"{BAD}: A 120000 tests/link"


def test_deleting_a_symlink_is_refused_by_its_old_mode(repo):
    base = base_of(repo, {"tests/link": ("120000", "../src/a.py")})
    tc = mkcommit(repo, {"tests/link": None, "tests/test_a.py": "def test_a(): pass\n"}, base)
    assert str(refusal(dt.test_paths, repo, base, tc)) == f"{BAD}: D 000000 tests/link"


def test_a_path_outside_the_roots_is_refused_with_its_status_and_mode(repo):
    base, tc = paths_of(repo, {"src/c.py": "z = 1\n"})
    assert str(refusal(dt.test_paths, repo, base, tc)) == f"{BAD}: A 100644 src/c.py"


def test_a_conftest_may_be_added_but_not_deleted(repo):
    base, tc = paths_of(repo, {"tests/conftest.py": "x = 1\n", "tests/test_a.py": "x = 1\n"})
    assert dt.test_paths(repo, base, tc) == (("tests/conftest.py", "tests/test_a.py"), ())
    base = base_of(repo, {"tests/conftest.py": "x = 1\n"})
    tc = mkcommit(repo, {"tests/conftest.py": None, "tests/test_a.py": "x = 1\n"}, base)
    assert str(refusal(dt.test_paths, repo, base, tc)).startswith(f"{BAD}: D 000000 tests/conf")


def test_a_path_over_the_id_limit_is_cut_in_the_refusal(repo):
    path = "tests/" + "ab/" * 200 + "test_x.py"
    base, tc = paths_of(repo, {path: "x = 1\n"})
    err = refusal(dt.test_paths, repo, base, tc)
    assert str(err) == f"{BAD}: path over 512 UTF-8 bytes: {path[:80]}..."


def test_a_python_file_no_target_can_name_is_refused_unless_it_is_deleted(repo):
    base, tc = paths_of(repo, {"tests/bad name.py": "x = 1\n", "tests/test_a.py": "x = 1\n"})
    err = refusal(dt.test_paths, repo, base, tc)
    assert str(err) == (
        f"{BAD}: no verifier target can name 'tests/bad name.py' (names: [A-Za-z0-9_-])"
    )
    base = base_of(repo, {"tests/bad name.py": "x = 1\n"})
    ok = mkcommit(repo, {"tests/bad name.py": None, "tests/test_a.py": "x = 1\n"}, base)
    assert dt.test_paths(repo, base, ok) == (("tests/test_a.py",), ("tests/bad name.py",))


def test_a_commit_that_only_deletes_has_no_tests_and_says_only_the_code(repo):
    base = base_of(repo, {"tests/test_a.py": "x = 1\n"})
    tc = mkcommit(repo, {"tests/test_a.py": None}, base)
    err = refusal(dt.test_paths, repo, base, tc)
    assert err.code == "TEST_COMMIT_EMPTY" and str(err) == "TEST_COMMIT_EMPTY"
    assert not err.has_detail


# ---- never_seen / ensure_test_commit ----------------------------------------------------


def test_a_present_commit_is_exposed_and_an_absent_one_is_not(repo):
    head = git(repo, "rev-parse", "HEAD")
    err = refusal(dt.never_seen, repo, head)
    assert str(err) == "TEST_COMMIT_ALREADY_EXPOSED: push the tests as a new commit"
    assert dt.never_seen(repo, "2" * 40) is None


def test_git_that_cannot_answer_leaves_the_exposure_unknown(repo, monkeypatch):
    spy(monkeypatch, fail=lambda args: True)
    err = refusal(dt.never_seen, repo, "2" * 40)
    assert str(err) == "TEST_COMMIT_EXPOSURE_UNKNOWN: git could not answer"
    assert isinstance(err.__cause__, GitCommandError)


def test_an_invalid_commit_id_and_a_missing_import_are_refused_in_full(repo):
    err = refusal(dt.ensure_test_commit, repo, "nonsense")
    assert str(err) == "TEST_COMMIT_INVALID: not a 40-hex commit id"
    store = SimpleNamespace(required=True, has=lambda oid: False)
    err = refusal(dt.ensure_test_commit, repo, "3" * 40, store)
    assert str(err) == f"TEST_COMMIT_NOT_IMPORTED: {'3' * 40}"


def test_the_fetch_gets_the_allowed_protocols_and_a_refusal_says_the_origin_failed(
    repo, monkeypatch
):
    seen: list[Any] = []

    def unavailable(repo_: Path, origin: Any, oid: str, *, protocols: Any) -> None:
        seen.append((origin, oid, protocols))
        raise gitprobe.TestCommitUnavailable

    monkeypatch.setattr(dt.gitprobe, "fetch_to_supplied", unavailable)
    err = refusal(dt.ensure_test_commit, repo, "4" * 40, origin_url="u")
    assert str(err) == "TEST_COMMIT_UNREADABLE: origin did not provide it"
    refusal(dt.ensure_test_commit, repo, "4" * 40, origin_url="u", allowed_protocols=("ssh",))
    assert seen == [("u", "4" * 40, ("https", "ssh")), ("u", "4" * 40, ("ssh",))]


# ---- lay_tests_over ---------------------------------------------------------------------


def lay(repo: Path, tmp_path: Path, files: dict[str, Any] | None = None):
    base = git(repo, "rev-parse", "HEAD")
    tc = mkcommit(repo, files or {"tests/test_a.py": "def test_a(): pass\n"}, base)
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    return base, tc, root


def test_the_overlay_commit_is_authored_as_cairnwork_not_as_the_host_user(repo, tmp_path):
    base, tc, root = lay(repo, tmp_path)
    head = dt.lay_tests_over(repo, base, tc, ("tests/test_a.py",), "a" * 17, root)
    assert git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>", head) == (
        "Cairnwork <cairnwork@localhost>|Cairnwork <cairnwork@localhost>"
    )
    assert git(repo, "rev-parse", f"{head}^") == base
    assert not list(root.iterdir())


def test_the_overlay_uses_its_private_index_and_removes_it(repo, tmp_path, monkeypatch):
    base, tc, root = lay(repo, tmp_path)
    seen: list[Any] = []
    real = dt.overlay_tree

    def watch(*args: Any, **kwargs: Any) -> str:
        seen.append(args[4])
        assert not args[4].exists()
        return real(*args, **kwargs)

    monkeypatch.setattr(dt, "overlay_tree", watch)
    dt.lay_tests_over(repo, base, tc, ("tests/test_a.py",), "b" * 17, root)
    assert seen == [root / ("b" * 17 + ".tests.index")]


def test_an_existing_private_index_is_refused_naming_it_even_when_dangling(repo, tmp_path):
    base, tc, root = lay(repo, tmp_path)
    index = root / ("c" * 17 + ".tests.index")
    index.symlink_to(root / "nowhere")
    err = refusal(dt.lay_tests_over, repo, base, tc, ("tests/test_a.py",), "c" * 17, root)
    assert str(err) == f"TEST_OVERLAY_FAILED: {index} already exists"
    assert index.is_symlink()


@pytest.mark.parametrize("fail", [GitCommandError("boom"), ValueError("bad tree")])
def test_an_overlay_failure_keeps_the_original_error_and_hides_a_missing_index(
    repo, tmp_path, monkeypatch, fail
):
    base, tc, root = lay(repo, tmp_path)

    def failing(*args: Any, **kwargs: Any) -> str:
        raise fail

    monkeypatch.setattr(dt, "overlay_tree", failing)
    err = refusal(dt.lay_tests_over, repo, base, tc, ("tests/test_a.py",), "d" * 17, root)
    assert str(err) == f"TEST_OVERLAY_FAILED: {fail}"
    assert err.__cause__ is fail
