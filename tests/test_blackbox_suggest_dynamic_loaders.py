"""tools/blackbox_suggest (#370): project modules that load or run other code by name.

A module can run another module, or source text, without an import statement the reader follows:
the standard library has functions that import or run a module given its name or path, and
modules that run source given as a string. The code they run is not in the import closure, so a
module that uses any of them refuses, as ``exec`` and ``import_module`` already do.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
SIDE = "import pkg.core\npkg.core.SCALE = 10\nx = 1\n"
TEST = "from pkg.core import f\ndef test_a():\n    assert f(2) == 2\n"
OTHER = "__file__.replace('core', '_side')"


def run(tmp_path, extra):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE + extra,
        "src/pkg/_side.py": SIDE,
        "tests/test_x.py": TEST,
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "run_module": "import runpy\nrunpy.run_module('pkg._side')\n",
    "run_module_imported_by_name": "from runpy import run_module as _r\n_r('pkg._side')\n",
    "run_path": f"import runpy\nrunpy.run_path({OTHER})\n",
    "resolve_name": "import pkgutil\npkgutil.resolve_name('pkg._side:x')\n",
    "exec_module": "import importlib.util as u\n"
    f"_s = u.spec_from_file_location('pkg._side', {OTHER})\n"
    "_s.loader.exec_module(u.module_from_spec(_s))\n",
    "load_module": "import importlib.machinery as m\n"
    f"m.SourceFileLoader('pkg._side', {OTHER}).load_module()\n",
    "bootstrap_module": "import importlib._bootstrap as b, importlib.util as u\n"
    "b._load(u.find_spec('pkg._side'))\n",
    "bootstrap_from_import": "from importlib._bootstrap import _load\n",
    "bootstrap_from_package": "from importlib import _bootstrap_external\n",
    "timeit": "import timeit\ntimeit.timeit('import pkg._side', number=1)\n",
    "cprofile": "import cProfile\ncProfile.run('import pkg._side')\n",
    "profile": "import profile\n",
    "trace": "import trace\ntrace.Trace(count=0, trace=0).runctx('import pkg._side', {}, {})\n",
    "code": "import code\ncode.InteractiveInterpreter().runsource('import pkg._side')\n",
    "codeop": "from codeop import compile_command\n",
    "doctest": "import doctest\n",
    "pdb": "import pdb\n",
    "bdb": "from bdb import Bdb\n",
    "zipimport": "import zipimport\n",
    "entry_point": "from importlib.metadata import EntryPoint\n"
    "EntryPoint('x', 'pkg._side', 'g').load()\n",
    "entry_points_module": "import importlib.metadata as md\n",
    "metadata_from_package": "from importlib import metadata\n",
    "importlib_util": "import importlib.util\n_s = importlib.util.find_spec('json')\n",
    "importlib_bare": "import importlib\n",
    "metadata_backport": "import importlib_metadata\n",
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_a_module_that_loads_or_runs_code_by_name_refuses(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "plain_stdlib": "import json\n",
    "own_function_named_run": "def run():\n    return 1\n",
    "module_named_like_a_loader": "import json.decoder\n",
}


@pytest.mark.parametrize("extra", KEPT.values(), ids=KEPT.keys())
def test_a_module_that_only_looks_modules_up_keeps_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
