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


# PR #385 review round 1: the base's own code can put a sealed directory in front of sys.path.
INSERT_PKG = (
    "import os\nimport sys\n\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
)
SEALED_PKG = {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "pkg/tests/test_calc.py": ""}


@pytest.mark.parametrize(
    ("conftest", "expected"),
    [(INSERT_PKG, REFUSED), ("import pytest\n", ALLOWED)],  # a conftest alone opens nothing
)
def test_route_check_and_submit_refuse_when_a_base_conftest_inserts_the_package(
    repo, tmp_path, monkeypatch, capsys, conftest, expected
):
    head = commit_change(repo, SEALED_PKG | {"pkg/tests/conftest.py": conftest})
    args = (head, ["pkg/fractions.py"], [], ["pkg/tests"], PKG_VERIFY)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == expected


WORDS = ["sys.path", "syspath", "from sys import", "addsitedir", ".pth", "PYTHONPATH",
         "pytest_plugins", "runpy", "sys.executable", "subprocess", "os.system", "os.popen",
         "os.exec", "os.spawn", "meta_path", "path_hooks", "importlib", "__import__"]  # fmt: skip


@pytest.mark.parametrize("word", WORDS)
def test_any_mention_of_a_sys_path_change_in_a_test_unseals(repo, word):
    commit_change(repo, SEALED_PKG | {"pkg/tests/helpers.py": f"# {word}\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
        check_task(_task(repo, ("pkg/fractions.py",)))


@pytest.mark.parametrize(
    ("path", "refused"),
    [
        ("conftest.py", True),  # the checkout root's files
        ("setup_paths.py", True),
        ("pkg/__init__.py", True),  # the package under test itself
        ("pkg/sub/deep.py", True),
        ("docs/conf.py", True),  # importable from the checkout root (a namespace package)
        ("tools/release-notes/make.py", True),  # every Python file: runpy or exec can reach it
        ("pkg/tests/notes.txt", False),  # not Python
    ],
)
def test_where_the_base_is_scanned(repo, path, refused):
    commit_change(repo, SEALED_PKG | {path: "import sys\nsys.path.insert(0, 'pkg')\n"})
    task = _task(repo, ("pkg/fractions.py",))
    if refused:
        with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
            check_task(task)
    else:
        assert check_task(task)


@pytest.mark.parametrize("step", ["ls-tree", "cat-file"])
def test_a_failed_scan_seals_nothing(repo, monkeypatch, step):
    from cairnwork import sys_path_scan

    def fails(name, real):
        def run(repo_, *args, **kwargs):
            if args[0] == step:
                raise GitCommandError(f"{step} failed")
            return real(repo_, *args, **kwargs)

        monkeypatch.setattr(sys_path_scan, name, run)

    commit_change(repo, SEALED_PKG)
    fails("run_git", sys_path_scan.run_git)
    fails("run_git_bytes", sys_path_scan.run_git_bytes)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
        check_task(_task(repo, ("pkg/fractions.py",)))


@pytest.mark.sandbox
def test_a_base_conftest_really_puts_a_sealed_package_in_front(repo, tmp_path):
    """Reachability on the verifier's own pytest, not the fix: pytest alone never inserts the
    sealed pkg, but a base conftest that does lets a new pkg/fractions.py pass a wrong answer."""
    files = SEALED_PKG | {
        "pkg/tests/conftest.py": INSERT_PKG,
        "pkg/tests/test_calc.py": SHADOW_TEST,
    }
    head = commit_change(repo, files | {"pkg/fractions.py": FAKE})
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/test_calc.py"], ("pkg/tests",)
    )
    (run,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed, (run.why, run.log_path.read_text())


# PR #385 review round 2: where else the scan must look.
@pytest.mark.parametrize("attributes", ["*.py binary\n", "*.py -diff\n", "*.py -text\n"])
def test_route_check_and_submit_scan_files_the_attributes_call_binary(
    repo, tmp_path, monkeypatch, capsys, attributes
):
    files = SEALED_PKG | {".gitattributes": attributes, "pkg/tests/conftest.py": INSERT_PKG}
    head = commit_change(repo, files)
    args = (head, ["pkg/fractions.py"], [], ["pkg/tests"], PKG_VERIFY)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == REFUSED


@pytest.mark.parametrize(
    ("config", "import_paths"),
    [
        ({}, ("lib",)),
        ({"pytest.ini": "[pytest]\npythonpath = lib\n"}, ()),
        ({}, ("l[i]b",)),  # a name a pathspec could read as a pattern is still searched
    ],
)
def test_an_import_path_is_scanned_too(repo, config, import_paths):
    # library code the tests import, under a task import path or the base pythonpath
    folder = import_paths[0] if import_paths else "lib"
    library = {f"{folder}/paths.py": "import sys\nsys.path[:0] = ['pkg']\n"}
    commit_change(repo, SEALED_PKG | config | library)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
        check_task(_task(repo, ("pkg/fractions.py",), import_paths=import_paths))


@pytest.mark.parametrize("present", [True, False])
def test_a_test_commit_in_the_repository_is_scanned_too(repo, present):
    base = commit_change(repo, SEALED_PKG)
    test = commit_change(repo, {"pkg/tests/test_more.py": INSERT_PKG}, message="tests")
    if not present:
        git(repo, "reset", "-q", "--hard", base)
        git(repo, "reflog", "expire", "--expire=now", "--all")
        git(repo, "gc", "-q", "--prune=now")
    task = TaskSpec("key", "card", "t", "fix", repo, base, ("pkg/fractions.py",), PKG_VERIFY,
                    test_roots=("pkg/tests",), test_commit=test)  # fmt: skip
    if present:
        with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/fractions.py$"):
            check_task(task)
    else:  # not fetched yet: not read, and not fetched for this (stated residual)
        assert check_task(task)
