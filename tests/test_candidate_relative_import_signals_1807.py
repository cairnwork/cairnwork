"""Issue #1807 (design C'): end-to-end shapes where a relative import meets a path source, a
script run, a third-party-named package or a repointed ``__package__``. Each row is judged by the
real gate; ``EXPECT`` is the verdict of the new rule (``None`` = pass)."""

from __future__ import annotations

import pytest
from test_candidate_relative_import_1807 import REFUSED, TYPES, _verdict

X = "\nEXTRA = 1\n"
IMP = "from .types import type_ok\n"
CONF = "import sys\nsys.path.insert(0, 'pkg')\n"
RESULT = "def get():\n    pass\n"
REPOINT = '__package__ = "unittest"\n'
PKG = {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/validator.py": IMP}
EDIT = {"pkg/types.py": TYPES + X, "pkg/validator.py": IMP + X}


RUN = "import subprocess, sys\nsubprocess.run([sys.executable, 'pkg/run.py'])\n"


def _row(base_extra=None, new=None, base=None):
    return {**(base or PKG), **(base_extra or {})}, new or EDIT


ROWS = {
    "base-conftest-sys-path-insert": _row({"conftest.py": CONF}),
    "base-pytest-ini-pythonpath": _row({"pytest.ini": "[pytest]\npythonpath = pkg\n"}),
    "base-pyproject-pythonpath": _row(
        {"pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["pkg"]\n'}
    ),
    "subprocess-runs-package-script": _row(
        new={
            "pkg/types.py": TYPES + X,
            "pkg/validator.py": IMP + RUN,
        }
    ),
    "third-party-named-module": (
        {"pkg/__init__.py": "", "pkg/requests.py": RESULT, "pkg/v.py": "X = 1\n"},
        {
            "pkg/requests.py": "def get():\n    pass\n" + X,
            "pkg/v.py": "from .requests import get\n",
        },
    ),
    "third-party-named-subpackage": (
        {"pkg/__init__.py": "", "pkg/numpy/__init__.py": "", "pkg/v.py": "X = 1\n"},
        {"pkg/numpy/__init__.py": X, "pkg/v.py": "from .numpy import thing\n"},
    ),
    "repoint-validator-only": _row(new={"pkg/validator.py": REPOINT + IMP + X}),
    "repoint-types-also-changed": _row(
        new={"pkg/types.py": TYPES + X, "pkg/validator.py": REPOINT + IMP + X}
    ),
    "repoint-unittest-mock-changed": (
        {**PKG, "pkg/mock.py": "def patch(x):\n    return x\n"},
        {
            "pkg/mock.py": "def patch(x):\n    return x\n" + X,
            "pkg/validator.py": REPOINT + "from .mock import patch\n" + X,
        },
    ),
}
# Controls: the base path source with a changed validator that has NO relative import. Main passes
# these too, so the new rule adds no release there: base-only redirects are a residual of main.
for _name, _extra in {
    "control-conftest-no-relative-import": {
        "conftest.py": "import sys\nsys.path.insert(0, 'pkg')\n"
    },
    "control-pytest-ini-no-relative-import": {"pytest.ini": "[pytest]\npythonpath = pkg\n"},
    "control-pyproject-no-relative-import": {
        "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["pkg"]\n'
    },
}.items():
    ROWS[_name] = _row(_extra, {"pkg/types.py": TYPES + X, "pkg/validator.py": "VALUE = 1\n"})
# Only the subprocess row is refused by the new rule (as on main); every other shape passes.
EXPECT = {name: None for name in ROWS} | {"subprocess-runs-package-script": REFUSED}


@pytest.mark.parametrize("name", list(ROWS))
def test_shape(tmp_path, name):
    got = _verdict(tmp_path, *ROWS[name])
    print(f"VERDICT {name} {got}")  # noqa: T201  (read by the main-vs-new comparison)
    assert got == EXPECT[name]
