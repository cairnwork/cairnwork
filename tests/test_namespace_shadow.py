"""#384, #390: a candidate file below the first level of an import root must not take over a
dotted name the candidate does not own.

pytest puts the import paths, the checkout root and test-root directories in front of
site-packages. A directory without ``__init__.py`` is a namespace portion: PathFinder keeps
looking and, when no module or regular package of that name turns up, merges every portion in
sys.path order, so the candidate's module is found first (#384: an installed namespace such as
``nsco.somelib.impl``; "B+": the base's own namespace portion under another root). PathFinder
also returns that namespace before an appended ``sys.meta_path`` finder is asked, so a package
found only by such a finder (editable installs) is replaced as a whole (#390). The first-level
rule never looks this deep. The interpreter here is a throwaway venv (namespace_site.py).
"""

import json
import subprocess
from pathlib import Path

import pytest
from conftest import commit_change, git
from namespace_site import make_site

from cairnwork import namespace_shadow
from cairnwork.gitcmd import GitCommandError
from cairnwork.verifier_spec import protected_verifier_inputs


@pytest.fixture(scope="module")
def python(tmp_path_factory):
    return make_site(tmp_path_factory.mktemp("site"))


def gate(repo, paths, python, import_paths=("src",), roots=("tests",), allow_deletions=False):
    base = git(repo, "rev-parse", "HEAD")
    return protected_verifier_inputs(
        tuple(paths),
        roots,
        repo=repo,
        base_commit=base,
        python=python,
        import_paths=import_paths,
        allow_deletions=allow_deletions,
    )


REFUSED = {
    "S1 module in an installed namespace": "src/nsco/somelib/impl.py",
    "S1b sourceless bytecode": "src/nsco/somelib/impl.pyc",
    "extension module": "src/nsco/somelib/impl.cpython-314-darwin.so",
    "S2 module over a regular subpackage": "src/nsco/reglib.py",
    "S3 __init__ over a regular subpackage": "src/nsco/reglib/__init__.py",
    "S5 __init__ turns a namespace level regular": "src/nsco/somelib/__init__.py",
    "S4 under the checkout root": "nsco/somelib/impl.py",
    "L1 legacy -nspkg.pth namespace": "src/legacyco/somelib/impl.py",
    "#390 editable package under src": "src/mylib/oracle.py",
    "#390 editable package at the root": "mylib/oracle.py",
    "#390 any directory of that name": "src/mylib/data.json",
    "a namespace only a meta finder supplies": "src/metans/part.py",
}
ALLOWED = {
    "R1 deep file of an installed regular package": "src/regpkg/calc.py",
    "R2 inside a regular subpackage of a namespace": "src/nsco/reglib/core.py",
    "P1 pkgutil-style package, existing subpackage": "src/pkuco/somelib/impl.py",
    "OK1 own portion in an installed namespace": "src/nsco/myproj/core.py",
    "OK2 new module in an installed namespace": "src/nsco/newmod.py",
    "OK3 own regular package": "src/myproj/core.py",
    "OK4 data file in an own portion": "src/nsco/myproj/data.json",
    "not an importable directory name": "src/my-data/nsco/somelib/impl.py",
    "documentation": "docs/guide/index.md",
}


@pytest.mark.parametrize("path", REFUSED.values(), ids=REFUSED.keys())
def test_a_path_that_takes_over_an_interpreter_name_is_refused(repo, python, path):
    assert gate(repo, [path], python) == (path,)


@pytest.mark.parametrize("path", ALLOWED.values(), ids=ALLOWED.keys())
def test_a_path_that_adds_a_new_name_is_allowed(repo, python, path):
    assert gate(repo, [path], python) == ()


def test_a_meta_only_package_above_a_nested_test_root_is_refused(repo, python):
    assert gate(repo, ["pkg/mylib/oracle.py"], python, (), ("pkg/tests",)) == (
        "pkg/mylib/oracle.py",
    )


def test_the_bases_own_namespace_under_another_root_counts(repo, python):
    """B+: nothing installed; tests/__init__.py puts the root ahead of src, so a root copy of the
    base's own namespace module replaces it, while the base's own file stays editable."""
    commit_change(repo, {"src/acme/core/calc.py": "x = 1\n", "tests/__init__.py": ""})
    assert gate(repo, ["acme/core/calc.py"], python) == ("acme/core/calc.py",)
    assert gate(repo, ["src/acme/core/calc.py"], python, allow_deletions=True) == ()
    assert gate(repo, ["acme/core/data.json", "acme/other.py"], python) == ()


def test_a_namespace_under_a_test_root_counts(repo, python):
    commit_change(repo, {"tests/helpers/oracle.py": "x = 1\n"})
    assert gate(repo, ["src/helpers/oracle.py"], python) == ("src/helpers/oracle.py",)


def test_an_own_regular_package_found_by_an_editable_finder_stays_editable(repo, python):
    assert gate(repo, ["src/ownproj/core.py"], python) == ("src/ownproj/core.py",)
    commit_change(repo, {"src/ownproj/__init__.py": ""})
    assert gate(repo, ["src/ownproj/core.py", "src/ownproj/sub/x.py"], python) == ()


@pytest.mark.parametrize("name", ["mylib", "nsco", "metans"])
def test_a_new_init_in_a_tracked_directory_cannot_take_over_an_installed_name(repo, python, name):
    """rev on PR #405: the base tracks only ``src/<name>/README.md``; the first-level rule counts
    the new ``__init__.py`` as tracked, and its module inventory misses names that only a finder
    or a namespace without metadata provides."""
    commit_change(repo, {f"src/{name}/README.md": "x\n"})
    path = f"src/{name}/__init__.py"
    assert gate(repo, [path], python) == (path,)


def test_a_new_init_in_a_tracked_directory_of_a_new_name_stays_allowed(repo, python):
    commit_change(repo, {"src/myproj/README.md": "x\n"})
    assert gate(repo, ["src/myproj/__init__.py"], python) == ()


def test_an_own_module_of_the_name_wins_over_the_candidates_directory(repo, python):
    commit_change(repo, {"src/nsco.py": "x = 1\n"})
    assert gate(repo, ["src/nsco/somelib/impl.py"], python) == ()


def test_an_own_module_the_task_may_delete_does_not_count(repo, python):
    commit_change(repo, {"src/nsco.py": "x = 1\n"})
    paths = ["src/nsco.py", "src/nsco/somelib/impl.py"]
    assert "src/nsco/somelib/impl.py" in gate(repo, paths, python, allow_deletions=True)
    assert "src/nsco/somelib/impl.py" not in gate(repo, paths, python)


def test_an_own_module_also_installed_is_editable_only_without_deletions(repo, python):
    commit_change(repo, {"src/nsco/somelib/impl.py": "x = 1\n"})
    path = "src/nsco/somelib/impl.py"
    assert gate(repo, [path], python) == ()
    assert gate(repo, [path], python, allow_deletions=True) == (path,)


@pytest.mark.parametrize(
    ("base", "paths", "refused"),
    [
        ({"src/nsco/myproj/a.py": ""}, ["src/NSCO/somelib/impl.py"], 1),
        ({}, ["src/Nsco/somelib/impl.py", "src/nsco/newmod.py"], 1),
        ({"src/nsco/myproj/a.py": ""}, ["src/NSCO/myproj/b.py"], 0),
    ],
    ids=["against the base", "between allowed paths", "harmless in both spellings"],
)
def test_a_path_is_walked_in_each_spelling_of_its_directories(repo, python, base, paths, refused):
    """On APFS both spellings land in one directory, and Python matches the on-disk case."""
    if base:
        commit_change(repo, base)
    assert gate(repo, paths, python) == tuple(paths[:refused])


def test_the_probe_never_imports_a_concrete_package(repo, python):
    marker = Path(python).parents[2] / "sidefx-imported"
    assert gate(repo, ["src/sidefx/x/y.py"], python) == ("src/sidefx/x/y.py",)
    assert not marker.exists()


def test_without_an_interpreter_every_deep_path_is_protected(repo):
    paths = ("src/nsco/myproj/core.py", "docs/guide/index.md")
    assert gate(repo, paths, "/nonexistent/python") == paths


def test_without_the_base_listing_every_deep_path_is_protected(repo, python, monkeypatch):
    real = namespace_shadow.run_git

    def no_listing(repo, *args, **kwargs):
        if args[0] == "ls-tree" and "-rz" in args:
            raise GitCommandError("ls-tree failed")
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(namespace_shadow, "run_git", no_listing)
    paths = ("src/nsco/myproj/core.py", "docs/guide/index.md")
    assert gate(repo, paths, python) == paths


def test_a_base_that_is_no_commit_is_left_to_the_later_refusals(repo, python):
    """As pytest_config.gate_import_paths: intake refuses it (TASK_PATH_UNREADABLE)."""
    paths = ("src/nsco/somelib/impl.py",)
    gone = protected_verifier_inputs(
        paths, repo=repo, base_commit="d" * 40, python=python, import_paths=("src",)
    )
    assert gone == ()


MALFORMED = {
    "not json": lambda names: "ok",
    "empty": lambda names: "[]",
    "an entry missing": lambda names: json.dumps([["none"]] * (len(names) - 1)),
    "an unknown kind": lambda names: json.dumps([["odd"]] * len(names)),
    "a kind after a final one": lambda names: json.dumps([["path", "ns"]] * len(names)),
    "longer than the chain": lambda names: json.dumps([["ns"] * (len(n) + 1) for n in names]),
    "ends early on ns": lambda names: json.dumps([["ns"]] * len(names)),
    "an empty entry": lambda names: json.dumps([[]] * len(names)),
}


@pytest.mark.parametrize("answer", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_probe_answer_protects_every_deep_path(repo, python, monkeypatch, answer):
    monkeypatch.setattr(namespace_shadow, "_run_probe", lambda python, names: answer(names))
    paths = ("src/nsco/myproj/core.py", "src/other/core.py")
    assert gate(repo, paths, python) == paths


def test_the_probe_answers_one_entry_per_chain(python):
    chains = [("nsco", "somelib", "impl"), ("regpkg",), ("mylib",), ("metans",), ("nothing_here",)]
    answer = json.loads(namespace_shadow._run_probe(python, chains))
    assert answer == [["ns", "ns", "path"], ["path"], ["meta"], ["meta"], ["none"]]


def test_the_probe_runs_where_namespaceloader_is_not_public(python):
    """Python 3.9/3.10 (a target's own venv) have only importlib._bootstrap_external's."""
    older = "import importlib.machinery as m\ndel m.NamespaceLoader\n" + namespace_shadow._PROBE
    answer = subprocess.run(
        [python, "-I", "-c", older], input='[["nsco", "somelib"]]', capture_output=True, text=True
    )
    assert json.loads(answer.stdout) == [["ns", "ns"]], answer.stderr


def test_without_a_repository_the_rule_is_not_applied():
    """pycache_guard's per-file check has no base; check_task always passes one."""
    path = ("src/nsco/somelib/impl.py",)
    assert protected_verifier_inputs(path, import_paths=("src",)) == ()
