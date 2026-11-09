"""The 3.10+ module inventory also runs the probe's hook checks and namespace-directory scan.

#811: a directory without ``__init__.py`` on the verifier's sys.path is importable as a
namespace package, yet none of the three 3.10+ listings name it; an unknown path hook or finder
serves names no listing has. #841: a standard FileFinder hook or finder given other loaders or
suffixes serves files no listing reads. Every such case is now listed or refused with the
environment reason, on 3.10+ and on the old-Python probe alike. Uses the stand-in Pythons of
test_old_python_inventory (this interpreter behind a script).
"""

import subprocess
import sys

import pytest
from conftest import commit_change
from test_old_python_inventory import CASES, FILES, HOOKS, refused, stub, unavailable

from cairnwork import module_inventory

MODERN = (3, 12)
# (setup, a module the stand-in can then import that no listing names)
SUFFIX = {
    "suffix added to the shared list": "import importlib.machinery as m\n"
    "m.SOURCE_SUFFIXES.append('.txt')",
    "hook given another suffix": "import importlib.machinery as m\n"
    "sys.path_hooks[-1] = m.FileFinder.path_hook((m.SourceFileLoader, ['.py', '.txt']))\n"
    "sys.path_importer_cache.clear()",
}
FINDERS = {
    "finder with other loaders": "import importlib.machinery as m\n"
    "f = m.FileFinder('/nowhere', (m.SourceFileLoader, ['.txt']))\n"
    "sys.path_importer_cache['/nowhere'] = f",
    "FileFinder subclass": "import importlib.machinery as m, importlib._bootstrap_external as b\n"
    "F = type('F', (m.FileFinder,), {})\n"
    "sys.path_importer_cache['/nowhere'] = F('/nowhere', *b._get_supported_file_loaders())",
}


def test_a_namespace_directory_on_the_verifier_path_is_protected_on_3_10(repo, tmp_path):
    """#811 gap 1: ``point/geom/x.py`` (no ``__init__.py``, no metadata) makes ``point``
    importable; main's 3.10+ listing missed it and let point.py through."""
    commit_change(repo, {**FILES, "point.py": "X = 1\n"})
    site = tmp_path / "site"
    (site / "point" / "geom").mkdir(parents=True)
    (site / "point" / "geom" / "x.py").write_text("")
    CASES["point.py"] = "pkgA/geometry/tests"
    try:
        assert refused(repo, "point.py", stub(tmp_path, extra=[site], version=MODERN))
        assert not refused(repo, "point.py", stub(tmp_path, version=MODERN))  # the control
    finally:
        del CASES["point.py"]


@pytest.mark.parametrize("setup", sorted(HOOKS))
def test_an_unknown_import_hook_fails_closed_on_3_10(repo, tmp_path, setup):
    """#811 gap 2: the old-Python hook allowlist now guards the 3.10+ listing too."""
    commit_change(repo, FILES)
    assert unavailable(repo, "pkgB/base.py", stub(tmp_path, setup=HOOKS[setup], version=MODERN))


@pytest.mark.parametrize("version", ((3, 9), MODERN))
@pytest.mark.parametrize("setup", sorted(SUFFIX))
def test_a_standard_hook_with_another_suffix_fails_closed(repo, tmp_path, setup, version):
    """#841: the stand-in imports ``txtmod`` from txtmod.txt, which no listing reads."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "txtmod.txt").write_text("X = 41\n")
    python = stub(tmp_path, extra=[site], setup=SUFFIX[setup], version=version)
    served = subprocess.run(  # noqa: S603 - the stand-in Python, fixed code
        [python, "-I", "-c", "import txtmod; print(txtmod.X + 1)"],
        capture_output=True, text=True, check=True, cwd="/",
    )  # fmt: skip
    assert served.stdout == "42\n"
    commit_change(repo, FILES)
    assert unavailable(repo, "pkgB/base.py", python)


@pytest.mark.parametrize("version", ((3, 9), MODERN))
@pytest.mark.parametrize("setup", sorted(FINDERS))
def test_a_standard_finder_with_other_loaders_fails_closed(repo, tmp_path, setup, version):
    commit_change(repo, FILES)
    assert unavailable(repo, "pkgB/base.py", stub(tmp_path, setup=FINDERS[setup], version=version))


@pytest.mark.parametrize("version", ((3, 9), MODERN))
def test_default_machinery_still_lists_and_allows_an_in_package_fix(repo, tmp_path, version):
    """The control: the startup hooks and finders pass, so the fix of #790's F4 stays allowed."""
    commit_change(repo, FILES)
    assert not refused(repo, "pkgB/base.py", stub(tmp_path, version=version))


def test_this_interpreter_and_its_venv_pass_the_checks():
    """The measured case (#811 prerequisite): a real venv Python, with virtualenv's finder."""
    names = module_inventory.importable_names(sys.executable)
    assert {"json", "pytest", "cairnwork"} <= names
