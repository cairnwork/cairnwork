"""#290 (4b, 08:32): a first-level module under an import path may shadow a dependency.

The checkout root is on the verifier's import path, and so is every configured ``import_paths``
directory (#82, prepended to pytest's ``pythonpath``). A root module is writable only with proof
from the base commit that its name shadows nothing the verifier's interpreter can import
(verifier_spec._safe_root_modules). The same check is missing for the first level of an import
path (``src/json.py`` in a src-layout project) and for packages (``json/__init__.py``).
"""

import sys

import pytest
from conftest import commit_change, git

from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)


def task(repo, path, import_paths=("src",)):
    head = git(repo, "rev-parse", "HEAD")
    return TaskSpec(
        "key", "card", "title", "fix", repo, head, (path,), VERIFY, import_paths=import_paths
    )


@pytest.mark.parametrize(
    "path",
    ["src/json.py", "src/colorsys.py", "src/pytest.py", "src/pluggy.py", "src/json/__init__.py"],
)
def test_a_tracked_first_level_module_under_an_import_path_that_shadows_is_refused(repo, path):
    commit_change(repo, {path: "x = 1\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


@pytest.mark.parametrize("path", ["src/brand_new.py", "src/brand_new/__init__.py"])
def test_a_new_first_level_module_under_an_import_path_is_refused(repo, path):
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


@pytest.mark.parametrize("path", ["json/__init__.py", "colorsys/__init__.py"])
def test_a_root_package_that_shadows_is_refused(repo, path):
    commit_change(repo, {path: "x = 1\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path, import_paths=()))


@pytest.mark.parametrize("path", ["src/calc.py", "src/calcpkg/core.py", "src/calcpkg/__init__.py"])
def test_honest_src_layout_files_stay_allowed(repo, path):
    commit_change(repo, {path: "x = 1\n"})
    assert check_task(task(repo, path))


@pytest.mark.sandbox
def test_the_shadow_really_reaches_a_protected_test(repo, tmp_path):
    """Reachability, not the fix: with import_paths=("src",), src/colorsys.py replaces the
    standard library module a protected test compares against, and the wrong answer passes."""
    files = {
        "src/calc.py": "def hsv():\n    return (0.0, 0.0, 0.0)\n",
        "src/colorsys.py": "def rgb_to_hsv(r, g, b):\n    return (0.0, 0.0, 0.0)\n",
        "tests/test_calc.py": (
            "import colorsys\nfrom calc import hsv\n\n"
            "def test_hsv():\n    assert hsv() == colorsys.rgb_to_hsv(1.0, 0.0, 0.0)\n"
        ),
    }
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_calc.py"])
    (run,) = run_verifiers(
        repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable, import_paths=("src",)
    )
    assert run.observation.passed, (run.why, run.log_path.read_text())


def test_adding_init_to_an_existing_tracked_package_dir_is_allowed(repo):
    # base has src/mypkg123/mod.py (so the dir is a tracked tree) but no __init__.py; adding one
    # to an existing package dir is not shadowing, so it stays writable when the name is safe.
    commit_change(repo, {"src/mypkg123/mod.py": "x = 1\n"})
    assert check_task(task(repo, "src/mypkg123/__init__.py"))


def test_package_tree_query_failure_fails_closed(repo, monkeypatch):
    # When the __init__.py is a new file, safety depends on the dir being a tracked tree, proved by
    # `git ls-tree -d`. If that query fails we have no proof, so the input must stay protected.
    import cairnwork.verifier_spec as vs
    from cairnwork.gitcmd import GitCommandError

    commit_change(repo, {"src/mypkg123/mod.py": "x = 1\n"})
    real = vs.run_git

    def flaky(repo_arg, *args):
        if "-d" in args:
            raise GitCommandError("ls-tree -d failed")
        return real(repo_arg, *args)

    monkeypatch.setattr(vs, "run_git", flaky)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, "src/mypkg123/__init__.py"))


@pytest.mark.parametrize("path", ["SRC/colorsys.py", "Src/json.py", "SRC/evil.pth"])
def test_a_case_variant_of_the_import_root_prefix_is_still_protected(repo, path):
    # APFS is case-insensitive: SRC/ and src/ are the same directory on disk, so a candidate that
    # spells the import-root component in a different case must not escape the shadow gate.
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


@pytest.mark.parametrize(
    "path", ["src/colorsys/__INIT__.py", "json/__Init__.py", "src/json/__INIT__.PY"]
)
def test_a_case_variant_of_init_py_is_still_a_protected_package(repo, path):
    # On APFS Python resolves pkg/__init__.py from a committed pkg/__INIT__.py, so a case-variant
    # __init__ filename (or .PY extension) must still be recognised as a shadowing package.
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


@pytest.mark.parametrize(
    "path",
    [
        "src/colorsys.pyc",
        "src/colorsys/__init__.pyc",
        "colorsys.pyc",
        "json/__init__.pyo",
        "src/colorsys.cpython-314-darwin.so",
        "src/colorsys/__INIT__.pyc",
    ],
)
def test_committed_bytecode_or_extension_shadow_is_refused(repo, path):
    # Python imports a first-level .pyc/.pyo/.so just as a .py; tamper_scan (.py only) and
    # pycache_guard (the TMPDIR prefix) do not cover bytecode committed into the checkout.
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


def test_a_submodule_bytecode_in_an_honest_package_is_not_a_first_level_unit(repo):
    # src/calcpkg/core.pyc is inside a package, not a first-level name, and calcpkg does not
    # shadow: it must not be falsely protected (same as core.py).
    commit_change(repo, {"src/calcpkg/__init__.py": "x = 1\n", "src/calcpkg/core.pyc": "\x00"})
    assert check_task(task(repo, "src/calcpkg/core.pyc"))
