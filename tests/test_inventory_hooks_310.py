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
from typing import Any

import pytest
from conftest import commit_change
from test_old_python_inventory import (
    CASES,
    FILES,
    HOOKS,
    refused,
    rejection,
    stub,
)

from cairnwork import module_inventory

MODERN = (3, 12)
# (setup, a module the stand-in can then import that no listing names)
SUFFIX = {
    "suffix added to the shared list": "import importlib.machinery as m\n"
    "m.SOURCE_SUFFIXES.append('.txt')",
    "hook given another suffix": "import importlib.machinery as m\n"
    "sys.path_hooks[-1] = m.FileFinder.path_hook((m.SourceFileLoader, ['.py', '.txt']))\n"
    "sys.path_importer_cache.clear()",
    "hook suffix subclass with a different value": "import importlib.machinery as m\n"
    "class S(str):\n    def __eq__(self, other): return other == '.py'\n"
    "sys.path_hooks[-1] = m.FileFinder.path_hook((m.SourceFileLoader, [S('.txt')]))\n"
    "sys.path_importer_cache.clear()",
}
FINDERS = {
    "finder with other loaders": "import importlib.machinery as m\n"
    "f = m.FileFinder('/nowhere', (m.SourceFileLoader, ['.txt']))\n"
    "sys.path_importer_cache['/nowhere'] = f",
    "FileFinder subclass": "import importlib.machinery as m, importlib._bootstrap_external as b\n"
    "F = type('F', (m.FileFinder,), {})\n"
    "sys.path_importer_cache['/nowhere'] = F('/nowhere', *b._get_supported_file_loaders())",
    "finder suffix subclass with a different value": "import importlib.machinery as m, "
    "importlib._bootstrap_external as b\n"
    "class S(str):\n    def __eq__(self, other): return other == '.py'\n"
    "f = m.FileFinder('/nowhere', *b._get_supported_file_loaders())\n"
    "f._loaders = [(S('.txt'), l) if l is m.SourceFileLoader else (s, l) for s, l in f._loaders]\n"
    "sys.path_importer_cache['/nowhere'] = f",
    "loader metaclass equality spoof": "import importlib.machinery as m, "
    "importlib._bootstrap_external as b\n"
    "class M(type):\n    def __eq__(cls, other): return other is m.SourceFileLoader\n"
    "class L(m.SourceFileLoader, metaclass=M): pass\n"
    "f = m.FileFinder('/nowhere', *b._get_supported_file_loaders())\n"
    "f._loaders = [(s, L) if l is m.SourceFileLoader else (s, l) for s, l in f._loaders]\n"
    "sys.path_importer_cache['/nowhere'] = f",
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


QUIET = {  # hooks that leave every import working, so main's 3.10+ listing ran and allowed
    "declining path hook": "def hook(path):\n    raise ImportError(path)\n"
    "sys.path_hooks.insert(0, hook)\nsys.path_importer_cache.clear()",
    "finder that finds nothing": "F = type('F', (), {'find_spec': staticmethod(lambda *a: None)})\n"
    "sys.meta_path.insert(0, F())",
}


@pytest.mark.parametrize("setup", sorted(QUIET))
def test_an_unknown_import_hook_fails_closed_on_3_10(repo, tmp_path, setup):
    """#811 gap 2: the old-Python hook allowlist now guards the 3.10+ listing too."""
    commit_change(repo, FILES)
    assert rejection(repo, "pkgB/base.py", stub(tmp_path, setup=QUIET[setup], version=MODERN)) == (
        "VERIFIER_PYTHON_INVENTORY_UNAVAILABLE"
    )


@pytest.mark.parametrize("setup", sorted(HOOKS))
def test_the_old_probe_s_hook_cases_are_refused_on_3_10(repo, tmp_path, setup):
    """Some of these break imports outright, so either refusal code is a refusal."""
    commit_change(repo, FILES)
    assert rejection(repo, "pkgB/base.py", stub(tmp_path, setup=HOOKS[setup], version=MODERN))


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
    assert rejection(repo, "pkgB/base.py", python) in (
        "VERIFIER_PYTHON_INVENTORY_UNAVAILABLE",
        "PROTECTED_VERIFIER_INPUT",
    )


@pytest.mark.parametrize("version", ((3, 9), MODERN))
@pytest.mark.parametrize("setup", sorted(FINDERS))
def test_a_standard_finder_with_other_loaders_fails_closed(repo, tmp_path, setup, version):
    commit_change(repo, FILES)
    code = rejection(repo, "pkgB/base.py", stub(tmp_path, setup=FINDERS[setup], version=version))
    assert code == "VERIFIER_PYTHON_INVENTORY_UNAVAILABLE"


@pytest.mark.parametrize("version", ((3, 9), MODERN))
def test_default_machinery_still_lists_and_allows_an_in_package_fix(repo, tmp_path, version):
    """The control: the startup hooks and finders pass, so the fix of #790's F4 stays allowed."""
    commit_change(repo, FILES)
    assert not refused(repo, "pkgB/base.py", stub(tmp_path, version=version))


def test_this_interpreter_and_its_venv_pass_the_checks():
    """The measured case (#811 prerequisite): a real venv Python, with virtualenv's finder."""
    names = module_inventory.importable_names(sys.executable)
    assert {"json", "pytest", "cairnwork"} <= names


class _LooksLikePy(str):
    def __eq__(self, other):
        return other == ".py"

    __hash__ = str.__hash__


def test_a_suffix_that_only_claims_equality_is_not_a_startup_suffix():
    """The hook check compares values, not ``__eq__``: a str subclass valued ".txt" that says it
    equals ".py" is another suffix (#1063). The end-to-end hook case above is refused either way,
    since such a hook also stops ``.py`` imports; this pins the check itself."""
    from importlib import machinery

    from cairnwork import old_python_probe as probe

    spoofed: list[Any] = [
        (loader, [_LooksLikePy(".txt") if s == ".py" else s for s in suffixes])
        for loader, suffixes in probe._default_loaders()
    ]
    assert probe._default_hook(machinery.FileFinder.path_hook(*probe._default_loaders()))
    assert not probe._default_hook(machinery.FileFinder.path_hook(*spoofed))


class _ClaimsToBeAList(type):
    def __eq__(cls, other):
        return True

    __hash__ = type.__hash__


def test_a_container_that_only_claims_to_be_a_list_is_not_the_startup_table():
    """The loader table must be exactly a list or tuple: a type whose metaclass says it equals
    list, and whose iteration could change after the check, is refused (#1063)."""
    from importlib import machinery

    from cairnwork import old_python_probe as probe

    want = [(s, loader) for loader, suffixes in probe._default_loaders() for s in suffixes]

    class Table(metaclass=_ClaimsToBeAList):
        def __len__(self):
            return len(want)

        def __iter__(self):
            return iter(want)

    finder: Any = machinery.FileFinder("/nowhere", *probe._default_loaders())
    assert probe._default_finder(finder)
    finder._loaders = Table()
    assert not probe._default_finder(finder)
