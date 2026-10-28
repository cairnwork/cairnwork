"""#382: a sealed package is no import root of the shadow gate.

pytest's prepend import mode only ever puts on sys.path a directory without an ``__init__.py``
file, or one whose name is not an identifier (``_pytest.pathlib.resolve_package_path``). A
directory above or inside a test root whose base ``__init__.py`` nothing can remove (the
candidate may not list an ``__init__`` there, the test commit may only add or modify files) is
therefore never inserted, and its names never shadow a module (``sealed_packages``). In-package
test layouts (``src/mylib/tests``) and test folders that mirror the package (``pkg/tests/core``
next to ``pkg/core``) stay editable; every #360 bypass still opens the directory first and stays
refused.
"""

import os
import sys

import pytest
from conftest import commit_change, git
from test_prepend_shadow_names import ALLOWED, REFUSED, _both

from cairnwork.gitcmd import GitCommandError
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

INPKG = {  # in-package tests, import path src: pytest inserts src, never src/mylib
    "src/mylib/__init__.py": "",
    "src/mylib/tests/__init__.py": "",
    "src/mylib/tests/test_calc.py": "x = 1\n",
    "src/mylib/types.py": "x = 1\n",
    "src/mylib/json/__init__.py": "",
    "src/mylib/json/decoder.py": "x = 1\n",
}
INPKG_ROOT = ("src/mylib/tests",)
INPKG_VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "src/mylib/tests/test_calc.py")),)
MIRROR = {  # the test folders mirror the package
    "pkg/__init__.py": "",
    "pkg/api.py": "x = 1\n",
    "pkg/core/__init__.py": "",
    "pkg/core/x.py": "x = 1\n",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_calc.py": "x = 1\n",
    "pkg/tests/api/__init__.py": "",
    "pkg/tests/api/test_api.py": "x = 1\n",
    "pkg/tests/core/__init__.py": "",
    "pkg/tests/core/test_x.py": "x = 1\n",
}
PKG_VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "pkg/tests/test_calc.py")),)
HONEST = {  # base files changed from the layout (None: absent), the candidate's paths
    "in-package: a new module": ("inpkg", {}, ["src/mylib/newmod.py"]),
    "in-package: a new stdlib-named module": ("inpkg", {}, ["src/mylib/http.py"]),
    "in-package: a tracked stdlib-named file": ("inpkg", {}, ["src/mylib/types.py"]),
    "in-package: a stdlib-named subpackage": ("inpkg", {}, ["src/mylib/json/decoder.py"]),
    "in-package: the test commit adds the root's __init__.py": (
        "inpkg", {"src/mylib/tests/__init__.py": None}, ["src/mylib/types.py"]),
    "mirror: a subpackage": ("mirror", {}, ["pkg/core/x.py"]),
    "mirror: a module": ("mirror", {}, ["pkg/api.py"]),
    "mirror, pkg not a package: a subpackage": (
        "mirror", {"pkg/__init__.py": None}, ["pkg/core/x.py"]),
    "mirror, pkg not a package: a module": ("mirror", {"pkg/__init__.py": None}, ["pkg/api.py"]),
}  # fmt: skip
OPENED = {  # the same layouts, opened: the directory can be in front, so the names count again
    "an __init__ in the allowed files": (
        "inpkg", {}, ["src/mylib/__init__.py", "src/mylib/types.py"]),
    "a case variant is the only __init__": (
        "inpkg", {"src/mylib/__init__.py": None, "src/mylib/__INIT__.py": ""},
        ["src/mylib/types.py"]),
    "a test folder without __init__.py": (
        "mirror", {"pkg/__init__.py": None, "pkg/tests/__init__.py": None}, ["pkg/core/x.py"]),
    "a path inside an __init__ directory": (
        "inpkg", {}, ["src/mylib/__init__.py/x.py", "src/mylib/types.py"]),
    "the same in another case": (
        "inpkg", {}, ["src/MyLib/__init__.py/x.py", "src/mylib/types.py"]),
    "a case variant is the test folder's only __init__": (
        "mirror", {"pkg/__init__.py": None, "pkg/tests/__init__.py": None,
                   "pkg/tests/__INIT__.py": ""}, ["pkg/core/x.py"]),
}  # fmt: skip


def _layout(repo, layout, changes):
    files = {**(INPKG if layout == "inpkg" else MIRROR), **changes}
    head = commit_change(repo, {path: text for path, text in files.items() if text is not None})
    if layout == "inpkg":
        return head, ["src"], list(INPKG_ROOT), INPKG_VERIFY
    return head, [], ["pkg/tests"], PKG_VERIFY


@pytest.mark.parametrize("case", sorted(HONEST))
def test_route_check_and_submit_allow_an_edit_in_a_sealed_package(
    repo, tmp_path, monkeypatch, capsys, case
):
    layout, changes, paths = HONEST[case]
    head, import_paths, roots, verify = _layout(repo, layout, changes)
    args = (head, paths, import_paths, roots, verify)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == ALLOWED


@pytest.mark.parametrize("case", sorted(OPENED))
def test_route_check_and_submit_refuse_once_the_package_can_open(
    repo, tmp_path, monkeypatch, capsys, case
):
    layout, changes, paths = OPENED[case]
    head, import_paths, roots, verify = _layout(repo, layout, changes)
    args = (head, paths, import_paths, roots, verify)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == REFUSED


def _task(repo, paths, roots=("pkg/tests",), import_paths=()):
    head = git(repo, "rev-parse", "HEAD")
    verify = (("pytest", ("python3", "-m", "pytest", "-q", f"{roots[0]}/test_calc.py")),)
    return TaskSpec("key", "card", "t", "fix", repo, head, paths, verify, test_roots=roots,
                    import_paths=import_paths)  # fmt: skip


@pytest.mark.parametrize("path", ["pkg/fractions.py", "pkg/json/__init__.py", "pkg/brand_new.py"])
def test_a_package_root_in_a_sealed_package_adds_no_import_root(repo, path):
    # #360's table: with pkg/__init__.py and pkg/tests/__init__.py pytest inserts the checkout root.
    files = {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "pkg/tests/test_calc.py": ""}
    commit_change(repo, files | ({} if path == "pkg/brand_new.py" else {path: "x = 1\n"}))
    assert check_task(_task(repo, (path,)))


def test_a_directory_whose_name_is_no_identifier_is_not_sealed(repo):
    # pytest stops the package chain at my-pkg and inserts it, __init__.py or not.
    files = {"my-pkg/__init__.py": "", "my-pkg/tests/__init__.py": "", "my-pkg/fractions.py": ""}
    commit_change(repo, files | {"my-pkg/tests/test_calc.py": "x = 1\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: my-pkg/fractions.py$"):
        check_task(_task(repo, ("my-pkg/fractions.py",), roots=("my-pkg/tests",)))


def test_a_symlinked_init_does_not_seal(repo):
    files = {path: text for path, text in MIRROR.items() if path != "pkg/__init__.py"}
    commit_change(repo, files | {"pkg/real.py": "", "pkg/fractions.py": "x = 1\n"})
    os.symlink("real.py", repo / "pkg" / "__init__.py")  # pytest follows it; git: mode 120000
    commit_change(repo, message="symlinked init")
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
        check_task(_task(repo, ("pkg/fractions.py",)))


def test_without_the_base_listing_nothing_is_sealed(repo, monkeypatch):
    def unreadable(*_args, **_kwargs):
        raise GitCommandError("ls-tree failed")

    commit_change(repo, INPKG)
    monkeypatch.setattr("cairnwork.sealed_packages.run_git", unreadable)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: src/mylib/types.py$"):
        check_task(_task(repo, ("src/mylib/types.py",), INPKG_ROOT, ("src",)))


SHADOW_TEST = "import fractions\n\n\ndef test_half():\n    assert fractions.Fraction(1, 2) == 7\n"
FAKE = "class Fraction:\n    def __init__(self, *args):\n        pass\n\n"
FAKE += "    def __eq__(self, other):\n        return True\n"
REACH = {  # files, test root, import paths, whether the fake fractions takes over
    "pkg and its root are packages": (
        {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "pkg/fractions.py": FAKE},
        "pkg/tests", (), False),
    "a conftest above a plain root": (
        {"pkg/__init__.py": "", "pkg/conftest.py": "", "pkg/fractions.py": FAKE},
        "pkg/tests", (), False),
    "in-package tests": (
        {"src/mylib/__init__.py": "", "src/mylib/tests/__init__.py": "",
         "src/mylib/fractions.py": FAKE}, "src/mylib/tests", ("src",), False),
    "a name that is no identifier": (
        {"my-pkg/__init__.py": "", "my-pkg/tests/__init__.py": "", "my-pkg/fractions.py": FAKE},
        "my-pkg/tests", (), True),
}  # fmt: skip


@pytest.mark.sandbox
@pytest.mark.parametrize("case", sorted(REACH))
def test_the_verifiers_pytest_never_inserts_a_sealed_package(repo, tmp_path, case):
    """Reachability on the verifier's own pytest: in a sealed package a stdlib-named file is not
    what the tests import, so the wrong answer still fails; a directory whose name is no
    identifier is inserted even with its __init__.py, so there it passes (not sealed)."""
    files, root, import_paths, passed = REACH[case]
    head = commit_change(repo, files | {f"{root}/test_calc.py": SHADOW_TEST})
    spec = parse_verifier("v", ["python3", "-m", "pytest", f"{root}/test_calc.py"], (root,))
    (run,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable,
                           import_paths=import_paths)  # fmt: skip
    assert run.observation.passed is passed, (run.why, run.log_path.read_text())


@pytest.mark.sandbox
def test_a_mirrored_test_folder_is_not_the_top_level_module(repo, tmp_path):
    """Reachability: with pkg not a package (pytest inserts pkg) and pkg/tests sealed, the tests'
    ``import core`` is pkg/core, never pkg/tests/core."""
    test = "import core\n\n\ndef test_who():\n    assert core.WHO == 'pkg'\n"
    files = {
        "pkg/core/__init__.py": "WHO = 'pkg'\n",
        "pkg/tests/__init__.py": "",
        "pkg/tests/core/__init__.py": "WHO = 'tests'\n",
        "pkg/tests/core/test_x.py": test,
    }
    head = commit_change(repo, files)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/core/test_x.py"], ("pkg/tests",)
    )
    (run,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed, (run.why, run.log_path.read_text())
