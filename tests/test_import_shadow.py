"""#290: candidate code may shadow a protected helper by changing import resolution at runtime.

Candidate library code runs inside the pytest process that produces the verdict. It can put a
directory it may write (the run's TMPDIR) in front of ``sys.path``, or install a ``meta_path``
finder, so that a protected test importing a helper afterwards loads a module the candidate wrote
outside the checkout. The checkout comparison does not see that file.

Case A: the protected helper is first imported after candidate code runs.
Case B: the protected helper is imported by the protected conftest, before candidate code runs.

All of these run the real sandbox through run_verifiers (owner mode, no verifier account).
"""

import sys

import pytest
from conftest import commit_change

from cairnwork.tamper_scan import scan_added_lines
from cairnwork.verifiers import parse_verifier, run_verifiers

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
# Candidate library code: answer() is wrong; on import it plants a fake helper that agrees.
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
# The same, but the planted file removes itself once imported: nothing is left to scan.
SELF_REMOVING = PATH_SHADOW.replace(
    "EXPECTED = 'wrong'\\n", "import os\\nos.remove(__file__)\\nEXPECTED = 'wrong'\\n"
)
# A finder cached for the protected tests/ directory itself: pytest puts tests/ back in front of
# sys.path before importing each test module, but the import system asks the cached finder.
IMPORTER_CACHE = PATH_SHADOW.replace(
    "sys.path.insert(0, _d)\n",
    "import importlib.machinery as m\n"
    "_f = m.FileFinder(_d, (m.SourceFileLoader, m.SOURCE_SUFFIXES))\n"
    "class _W:\n"
    "    def __init__(self, orig):\n"
    "        self.orig = orig\n"
    "    def find_spec(self, name, target=None):\n"
    "        return (_f if name == 'helper' else self.orig).find_spec(name, target)\n"
    "    def invalidate_caches(self):\n"
    "        self.orig.invalidate_caches()\n"
    "for _p in list(sys.path):\n"
    "    if os.path.basename(_p) == 'tests':\n"
    "        _o = sys.path_importer_cache.get(_p)\n"
    "        _o = _o or m.FileFinder(_p, (m.SourceFileLoader, m.SOURCE_SUFFIXES))\n"
    "        sys.path_importer_cache[_p] = _W(_o)\n",
)
# No import machinery: an entry in sys.modules under the helper's name (library code, so the
# tamper scan's test-code-only sys.modules rule does not apply).
MODULES_ENTRY = (
    "import sys, types\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "sys.modules['helper'] = types.SimpleNamespace(EXPECTED='wrong')\n"
)
# No file at all: a meta_path finder that answers for "helper" from memory.
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
    (result,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    return result


def test_control_the_protected_test_fails_without_shadowing(repo, tmp_path):
    lib = "def answer():\n    return 'wrong'\n"
    result = run(repo, tmp_path, lib)
    assert not result.observation.passed and result.plain_failure, result.log_path.read_text()


SHADOWS = {
    "importer_cache": IMPORTER_CACHE,
    "meta_path": META_PATH,
    "modules_entry": MODULES_ENTRY,
}


@pytest.mark.parametrize("lib", SHADOWS.values(), ids=SHADOWS.keys())
def test_case_a_a_shadowed_helper_must_not_pass_the_run(repo, tmp_path, lib):
    result = run(repo, tmp_path, lib)
    assert not result.observation.passed, (result.why, result.log_path.read_text())


@pytest.mark.parametrize("lib", [PATH_SHADOW, SELF_REMOVING], ids=["path", "self_removing"])
def test_case_a_a_directory_put_in_front_of_sys_path_loses_to_pytest(repo, tmp_path, lib):
    """Not reachable in the default prepend import mode: pytest puts tests/ back at sys.path[0]
    before importing each test module, so the planted helper.py is never found first."""
    result = run(repo, tmp_path, lib)
    assert not result.observation.passed and result.plain_failure, result.log_path.read_text()


# tamper_scan (the primary, static half) refuses the import-redirection APIs before the candidate
# runs. A bare sys.modules entry under a non-sensitive key in library code is not statically caught
# (the library sys.modules rule only guards sensitive keys); the runtime origin check in
# report_guard catches that one, asserted by the run-level tests above.
STATICALLY_REFUSED = {"importer_cache": IMPORTER_CACHE, "meta_path": META_PATH}


@pytest.mark.parametrize("name", STATICALLY_REFUSED.keys())
def test_the_tamper_scan_refuses_a_shadowing_library_patch(name):
    lines = list(enumerate(STATICALLY_REFUSED[name].splitlines(), start=1))
    assert scan_added_lines("src/lib.py", lines), name


def test_the_tamper_scan_does_not_flag_an_honest_src_layout_conftest():
    conftest_lines = list(enumerate(CONFTEST.splitlines(), start=1))
    assert not scan_added_lines("tests/conftest.py", conftest_lines)


# Serve the fake while test_z binds EXPECTED, then restore the real helper into sys.modules on the
# import of a later protected test (test_zz), so a check that only runs at runtest_call and
# sessionfinish sees the genuine module and misses the forgery. 3e1d #290 review, point 1.
BIND_THEN_RESTORE = (
    "import importlib.util, os, sys, types\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "_real = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tests', 'helper.py')\n"
    "_fake = types.ModuleType('helper')\n"
    "_fake.EXPECTED = 'wrong'\n"
    "sys.modules['helper'] = _fake\n"
    "def _restore(event, args):\n"
    "    if event == 'import' and args and args[0] == 'test_zz':\n"
    "        spec = importlib.util.spec_from_file_location('helper', _real)\n"
    "        mod = importlib.util.module_from_spec(spec)\n"
    "        spec.loader.exec_module(mod)\n"
    "        sys.modules['helper'] = mod\n"
    "sys.addaudithook(_restore)\n"
)


def test_case_a_bind_then_restore_defeats_an_end_of_run_check(repo, tmp_path):
    """The fake is live only while test_z imports EXPECTED; by runtest and sessionfinish the real
    helper is back in sys.modules, so checking import resolution only at those points is not
    enough. A guard must also check at the moment each test module and conftest finishes importing.
    """
    files = {
        "tests/conftest.py": CONFTEST,
        "tests/helper.py": HELPER,
        "tests/test_a.py": "import lib\n\ndef test_a():\n    assert lib\n",
        "tests/test_z.py": TEST_Z,
        "tests/test_zz.py": "def test_zz():\n    assert True\n",
        "src/lib.py": BIND_THEN_RESTORE,
    }
    head = commit_change(repo, files)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py", "tests/test_zz.py"]
    )
    (result,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert not result.observation.passed, (result.why, result.log_path.read_text())


def test_case_b_a_finder_does_not_shadow_a_helper_the_conftest_imported_first(repo, tmp_path):
    """The protected conftest imports the helper before any candidate code runs, so the module is
    already cached; a finder installed afterwards is never asked."""
    conftest = CONFTEST + "import helper  # noqa: F401\n"
    result = run(repo, tmp_path, META_PATH, conftest=conftest)
    assert not result.observation.passed and result.plain_failure, result.log_path.read_text()


def test_case_b_a_sys_modules_entry_still_replaces_a_cached_helper(repo, tmp_path):
    conftest = CONFTEST + "import helper  # noqa: F401\n"
    result = run(repo, tmp_path, MODULES_ENTRY, conftest=conftest)
    assert not result.observation.passed, (result.why, result.log_path.read_text())


# A fake 'helper' whose __spec__.origin is a REAL file inside the checkout, but a different file
# than tests/helper.py. A check that only required the origin to be under the checkout root would
# pass it (the candidate's own files are in the checkout); the origin must equal the exact expected
# path. The sys.modules write is in library code under a non-sensitive key, so tamper_scan does not
# catch it: the runtime origin check is the only line of defence, which keeps this test honest.
IN_CHECKOUT_REDIRECT = (
    "import importlib.util, os, sys\n"
    "def answer():\n"
    "    return 'wrong'\n"
    "_p = os.path.join(os.path.dirname(__file__), 'evil.py')\n"
    "_s = importlib.util.spec_from_file_location('helper', _p)\n"
    "_m = importlib.util.module_from_spec(_s)\n"
    "_s.loader.exec_module(_m)\n"
    "sys.modules['helper'] = _m\n"
)


def test_a_helper_redirected_to_another_checkout_file_must_not_pass(repo, tmp_path):
    files = {
        "tests/conftest.py": CONFTEST,
        "tests/helper.py": HELPER,
        "tests/test_a.py": TEST_A,
        "tests/test_z.py": TEST_Z,
        "src/lib.py": IN_CHECKOUT_REDIRECT,
        "src/evil.py": "EXPECTED = 'wrong'\n",  # a committed in-checkout file, not tests/helper.py
    }
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_a.py", "tests/test_z.py"])
    (result,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert not result.observation.passed, (result.why, result.log_path.read_text())
