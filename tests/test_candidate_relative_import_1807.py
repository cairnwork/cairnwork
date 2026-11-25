"""Issue #1807: a relative from-import is not scanned as an absolute import.

``from .types import x`` names a module inside the package, not the stdlib ``types``. Every case
commits a base package, then a candidate that changes the listed files, and calls
``refuse_new_sys_path_mutation`` on the real change set. Absolute imports of machinery modules,
held names reached through a relative import, and an added same-name file stay refused.
"""

from __future__ import annotations

import pytest
from conftest import git

from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.workspace import CandidateRejected

REFUSED = "CANDIDATE_SYS_PATH_MUTATION"
TYPES = "def type_ok(x):\n    return x\n\n\ndef type_name(x):\n    return str(x)\n"


def _verdict(tmp_path, base_files, new_files, name="repo"):
    """``base_files`` are committed first; ``new_files`` (path -> text) are then written. Every
    written path is an allowed own file. Returns the refusal code or None."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    for path, text in base_files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    for path, text in new_files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    try:
        refuse_new_sys_path_mutation(repo, base, candidate, list(new_files))
    except CandidateRejected as exc:
        return exc.code
    return None


def _pair(first_line, validator_rest="", types_rest=""):
    """The base package and the candidate that edits other lines of both files."""
    validator = first_line + "\n\n\ndef check(x):\n    return type_ok(x)\n"
    base = {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/validator.py": validator}
    new = {
        "pkg/types.py": TYPES + types_rest + "\nEXTRA = 1\n",
        "pkg/validator.py": validator + validator_rest + "\nEXTRA = 1\n",
    }
    return base, new


PASS = [
    "from .types import type_ok, type_name",
    "from .types import type_ok",
    "from .types import (type_ok, type_name)",
    "from ..types import type_ok",
    "from .types import type_ok as ok",
    "from .types.sub import thing",
]


@pytest.mark.parametrize("line", PASS)
def test_relative_import_of_a_package_module_named_like_machinery_passes(tmp_path, line):
    base, new = _pair(line)
    assert _verdict(tmp_path, base, new) is None


BLOCK = [
    "from . import sys",
    "from . import importlib",
    "from .x import importlib",
    "from .x import sys",
    "from .x import __import__",
    "from . import runpy",
    "from .util import import_module",
]


@pytest.mark.parametrize("line", BLOCK)
def test_relative_import_that_brings_in_a_held_name_stays_refused(tmp_path, line):
    base, new = _pair(line)
    assert _verdict(tmp_path, base, new) == REFUSED


# Decision (#1807): with an empty module part the imported name alone is checked, and a name such
# as ``types`` is held when it arrives through a from-import (HELD_IF_IMPORTED): the scan cannot
# tell a sibling module from an object of that name. This Issue does not touch that rule, so it
# stays refused; the fix covers the module part of ``from .types import x`` only.
@pytest.mark.parametrize("line", ["from . import types", "from . import types as t"])
def test_from_dot_import_of_a_held_if_imported_name_stays_refused(tmp_path, line):
    base, new = _pair(line)
    assert _verdict(tmp_path, base, new) == REFUSED


def test_relative_import_followed_by_a_dynamic_import_stays_refused(tmp_path):
    base, new = _pair(
        "from .types import type_ok, type_name",
        validator_rest='\nMOD = __import__("os")\n',
    )
    assert _verdict(tmp_path, base, new) == REFUSED


@pytest.mark.parametrize(
    "line",
    [
        "from types import FunctionType",
        "from importlib import util",
        "import importlib",
        "from runpy import run_path",
        "import site",
        "from importlib.util import spec_from_file_location",
    ],
)
def test_absolute_import_of_a_machinery_module_stays_refused(tmp_path, line):
    base, new = _pair(line)
    assert _verdict(tmp_path, base, new) == REFUSED


def test_sys_path_insert_stays_refused(tmp_path):
    base, new = _pair("import sys\nsys.path.insert(0, 'x')")
    assert _verdict(tmp_path, base, new) == REFUSED


def test_added_same_name_file_next_to_a_signal_file_stays_refused(tmp_path):
    base = {
        "pkg/__init__.py": "",
        "pkg/validator.py": "from importlib import util\n\n\ndef check(x):\n    return x\n",
    }
    new = {
        "pkg/types.py": TYPES,
        "pkg/validator.py": base["pkg/validator.py"] + "\nEXTRA = 1\n",
    }
    assert _verdict(tmp_path, base, new) == REFUSED


def test_only_one_of_the_two_files_changed_passes_as_before(tmp_path):
    base, new = _pair("from .types import type_ok, type_name")
    only_validator = {"pkg/validator.py": new["pkg/validator.py"]}
    only_types = {"pkg/types.py": new["pkg/types.py"]}
    assert _verdict(tmp_path, base, only_validator, "a") is None
    assert _verdict(tmp_path, base, only_types, "b") is None
