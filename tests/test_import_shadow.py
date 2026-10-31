"""#290 (static half): a candidate must not redirect import resolution so a protected test,
conftest or helper loads the candidate's module instead of the checkout file.

tamper_scan refuses the import-internals a candidate would set to do this (``sys.meta_path``,
``sys.path_hooks``, ``sys.path_importer_cache``) in the patch, before any candidate code runs. It
is a higher bar, not a boundary: a ``sys.modules`` entry under a non-sensitive key, a name built
from strings, or a non-``.py`` file is not caught, and the candidate shares the interpreter. A
runtime observer of import resolution is tracked separately (Tier-3, out-of-process).

A bare ``sys.path`` prepend is deliberately not a rule: an honest src-layout conftest prepends
``src``, and in the default prepend import mode pytest restores ``tests/`` to ``sys.path[0]`` before
importing each test module, so a planted same-named file is never found first (shown end to end
below).
"""

import sys

import pytest
from conftest import commit_change

from cairnwork.tamper_scan import scan_added_lines
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox

CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
HELPER = "EXPECTED = 'real'\n"
# The protected test: fails as written, because the library returns the wrong value.
TEST_Z = (
    "from helper import EXPECTED\n"
    "from lib import answer\n\n"
    "def test_z():\n"
    "    assert answer() == EXPECTED, 'the real deferred test'\n"
)
TEST_A = "import lib\n\ndef test_a():\n    assert lib\n"

# Candidate library code: answer() is wrong; on import it plants a fake helper that agrees and puts
# its directory in front of sys.path. This is NOT reachable in the default prepend import mode.
PATH_SHADOW = (
    "import os, sys\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "_d = os.path.join(os.environ['TMPDIR'], 'shadow')\n"
    "os.makedirs(_d, exist_ok=True)\n"
    "with open(os.path.join(_d, 'helper.py'), 'w') as f:\n"
    "    f.write(\"EXPECTED = 'wrong'\\n\")\n"
    "sys.path.insert(0, _d)\n"
)

# The import-redirection patches tamper_scan must refuse (library code; module name = the file).
META_PATH = (
    "import importlib.abc, importlib.util, sys\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "class _F(importlib.abc.MetaPathFinder, importlib.abc.Loader):\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        return importlib.util.spec_from_loader(name, self) if name == 'helper' else None\n"
    "    def create_module(self, spec):\n"
    "        return None\n"
    "    def exec_module(self, module):\n"
    "        module.EXPECTED = 'wrong'\n"
    "sys.meta_path.insert(0, _F())\n"
)
IMPORTER_CACHE = (
    "import importlib.machinery as m, os, sys\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "_d = os.environ['TMPDIR']\n"
    "sys.path_importer_cache[_d] = m.FileFinder(_d, (m.SourceFileLoader, m.SOURCE_SUFFIXES))\n"
)
PATH_HOOKS = (
    "import sys\ndef answer():\n    return 'wrong'\nsys.path_hooks.insert(0, _my_finder_hook)\n"
)
REFUSED = {"meta_path": META_PATH, "path_hooks": PATH_HOOKS, "importer_cache": IMPORTER_CACHE}


def run(repo, tmp_path, lib, conftest=CONFTEST):
    files = {
        "tests/conftest.py": conftest,
        "tests/helper.py": HELPER,
        "tests/test_a.py": TEST_A,
        "tests/test_z.py": TEST_Z,
        "src/lib.py": lib,
    }
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (result,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    return result


def test_control_the_protected_test_fails_without_shadowing(repo, tmp_path):
    lib = "def answer():\n    return 'wrong'\n"
    result = run(repo, tmp_path, lib)
    assert not result.observation.passed and result.plain_failure, result.log_path.read_text()


def test_a_bare_sys_path_prepend_loses_to_pytest(repo, tmp_path):
    """Why there is no sys.path rule: pytest puts tests/ back at sys.path[0] before importing each
    test module, so the planted helper.py is never found first and the run fails naturally."""
    result = run(repo, tmp_path, PATH_SHADOW)
    assert not result.observation.passed and result.plain_failure, result.log_path.read_text()


@pytest.mark.parametrize("name", REFUSED.keys())
def test_the_tamper_scan_refuses_an_import_redirection_patch(name):
    lines: list[tuple[int, str]] = list(enumerate(REFUSED[name].splitlines(), start=1))
    assert scan_added_lines("src/lib.py", lines), name


def test_the_tamper_scan_does_not_flag_an_honest_src_layout_conftest():
    lines: list[tuple[int, str]] = list(enumerate(CONFTEST.splitlines(), start=1))
    assert not scan_added_lines("tests/conftest.py", lines)
