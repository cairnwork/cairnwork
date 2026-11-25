"""Package-own shadow files (#1807, design C): the signal stays, the shadow file may go.

A relative import still counts as the import-system signal it would be if it named the stdlib
module (nothing is hidden from the scan). Only a changed file that a changed relative import
names as the package's own module is left out of the shadow search, and only when the trees
prove the package, nothing moves its search path, no changed file has another path signal and no
config text names the package directory. Residuals that stay open (Issue #1829, present on main
without any relative import) are pinned at the end. Every case runs the real gate on a real repo.
"""

from __future__ import annotations

import pytest
from test_candidate_relative_import_1807 import REFUSED, TYPES, _verdict

USE = "from .types import type_ok\n\n\ndef check(x):\n    return type_ok(x)\n"
USE2 = USE + "\n\ndef more(x):\n    return x\n"
TYPES2 = TYPES + "\n\ndef extra():\n    return 1\n"
SRC_CONF = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / 'src'))\n"
PIECES = "import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'p' + 'kg'))\n"
PKG = {"pkg/__init__.py": "", "pkg/a.py": "X = 1\n"}


def _src_layout(init: str):
    root = "src/mini"
    base = {f"{root}/__init__.py": init, f"{root}/types.py": TYPES, f"{root}/validator.py": USE}
    base["conftest.py"] = SRC_CONF
    return base, {f"{root}/types.py": TYPES2, f"{root}/validator.py": USE2}


def _own(root_files: dict[str, str]):
    """A base with ``root_files`` and a candidate that adds pkg/types.py plus a user of it."""
    return {**PKG, **root_files}, {"pkg/types.py": TYPES, "pkg/v.py": USE}


MUST_PASS = {
    "src layout, empty init": _src_layout(""),
    "src layout, init with literals": _src_layout('__all__ = ["check"]\n__version__ = "1"\n'),
    "src layout, init re-exports": _src_layout("from .validator import check\n"),
    "plain sibling": (
        {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/validator.py": USE},
        {"pkg/types.py": TYPES2, "pkg/validator.py": USE2},
    ),
    "new sibling named like a site module": (
        PKG,
        {"pkg/site.py": "X = 2\n", "pkg/v.py": "from .site import X\n"},
    ),
    "subpackage, parent import": (
        {"pkg/__init__.py": "", "pkg/sub/__init__.py": "", "pkg/types.py": TYPES},
        {"pkg/types.py": TYPES2, "pkg/sub/v.py": "from ..types import type_ok\n"},
    ),
    "config only puts the parent on the path": _own(
        {"conftest.py": "import sys\nsys.path.insert(0, '.')\n"}
    ),
}
# names that are refused without any shadow file (the signal alone), so a hidden use of the real
# module through a repointed package would be a new pass if the signal were dropped
REPOINT = {"pkg/pickle.py": "X = 2\n", "pkg/v.py": "from .pickle import loads\n"}
INIT_MOVES = 'import os, sys\nsys.modules[__name__].__setattr__("__pa" + "th__", [os.__file__])\n'
MUST_BLOCK = {
    "helper outside the chain moves the path": (
        {**PKG, "helper.py": "import os, pkg\npkg.__path__ = [os.path.dirname(os.__file__)]\n"},
        REPOINT,
    ),
    "importer rebinds its package": (
        PKG,
        {
            "pkg/mock.py": "X = 2\n",
            "pkg/v.py": '__package__ = "unittest"\nfrom .mock import patch\n',
        },
    ),
    "importer rebinds its package, importlib step": (
        PKG,
        {"pkg/util.py": "X = 2\n", "pkg/v.py": '__package__ = "importlib"\nfrom .util import f\n'},
    ),
    "importer rebinds its package, shadow name": (
        PKG,
        {"pkg/types.py": TYPES, "pkg/v.py": '__package__ = "unittest"\n' + USE},
    ),
    "chain init moves the path, unlisted spelling": ({"pkg/__init__.py": INIT_MOVES}, REPOINT),
    "package directory on the path: conftest": _own(
        {"conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n"}
    ),
    "package directory on the path: pytest.ini": _own(
        {"pytest.ini": "[pytest]\npythonpath = pkg\n"}
    ),
    "package directory on the path: pyproject": _own(
        {"pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["pkg"]\n'}
    ),
    "package directory on the path: CI env": _own(
        {".github/workflows/t.yml": "env:\n  PYTHONPATH: pkg\n"}
    ),
    "package directory on the path: .pth": _own({"site/x.pth": "pkg\n"}),
    "another path signal next to the relative import": (
        _own({"conftest.py": PIECES})[0],
        {"pkg/types.py": TYPES, "pkg/v.py": "import sys\nsys.path.insert(0, 'x')\n" + USE},
    ),
    "package named like a machinery module": (
        PKG,
        {"pkg/runpy/__init__.py": "X = 2\n", "pkg/v.py": "from .runpy import X\n"},
    ),
}
# Issue #1829: the same result is a pass on main once the relative import is removed, so these
# are not new capability; they are pinned so nobody changes them silently.
KNOWN_RESIDUAL = {
    "path moved by a helper, shadow-needing name": (
        {**PKG, "helper.py": "import os, pkg\npkg.__path__ = [os.path.dirname(os.__file__)]\n"},
        {"pkg/types.py": TYPES, "pkg/v.py": USE},
    ),
    "chain init moves the path, shadow-needing name": (
        {"pkg/__init__.py": INIT_MOVES, "pkg/a.py": "X = 1\n"},
        {"pkg/types.py": TYPES, "pkg/v.py": USE},
    ),
    "directory name built in pieces": _own({"conftest.py": PIECES}),
    "package script run by a base test": (
        {
            **PKG,
            "pkg/tool.py": "print(1)\n",
            "tests/test_t.py": "import subprocess, sys\n\n\ndef test_t():\n"
            "    subprocess.run([sys.executable, 'pkg/tool.py'])\n",
        },
        {"pkg/types.py": TYPES, "pkg/v.py": USE},
    ),
}


@pytest.mark.parametrize("name", list(MUST_PASS))
def test_an_honest_package_own_change_passes(tmp_path, name):
    assert _verdict(tmp_path, *MUST_PASS[name]) is None


@pytest.mark.parametrize("name", list(MUST_BLOCK))
def test_a_repointed_or_rooted_package_stays_refused(tmp_path, name):
    assert _verdict(tmp_path, *MUST_BLOCK[name]) == REFUSED


@pytest.mark.parametrize("name", list(KNOWN_RESIDUAL))
def test_known_residual_is_pinned_see_issue_1829(tmp_path, name):
    assert _verdict(tmp_path, *KNOWN_RESIDUAL[name]) is None


def test_the_same_change_without_the_relative_import_is_a_pass_on_main_too(tmp_path):
    """The reason the residuals are not this PR's: no relative import, same result (#1829)."""
    base = {**PKG, "conftest.py": PIECES}
    new = {"pkg/types.py": TYPES, "pkg/v.py": "def f():\n    return 1\n"}
    assert _verdict(tmp_path, base, new) is None
