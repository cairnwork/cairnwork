"""Issue #1807 (design C'): a relative import is exempt only when the judged tree proves its
target (the chain of ``__init__.py`` files in both trees, a top-level package not named like the
stdlib). Cases and helpers live in the sibling test file; init and symlink rows are in the
chain file."""

from __future__ import annotations

import pytest
from conftest import git
from test_candidate_relative_import_1807 import GUARD, MOCK, REFUSED, TYPES, _verdict

from cairnwork.sys_path_scan import may_edit_sys_path, scan_sys_path_blob

CALL = "\n\ndef check(x):\n    return patch(x)\n"
PATCH = "from .mock import patch" + CALL


def _own(tmp_path, base, new):
    """Base files plus an edited same-named module next to the file that carries the import."""
    return _verdict(tmp_path, base, new)


@pytest.mark.parametrize(
    "layout",
    [
        {
            "pkg/__init__.py": "",
            "pkg/types.py": TYPES,
            "pkg/sub/__init__.py": "",
            "pkg/sub/validator.py": "from ..types import type_ok\n",
        },
        {
            "pkg/__init__.py": "from .types import type_ok\n",
            "pkg/types.py": TYPES,
        },
        {
            "pkg/__init__.py": "",
            "pkg/types.py": TYPES,
            "pkg/code.py": "def helper():\n    return 1\n",
            "pkg/validator.py": "from .code import helper\n",
        },
        {
            "pkg/__init__.py": "",
            "pkg/types.py": TYPES,
            "pkg/mod.py": "X = 1\n",
            "pkg/validator.py": "from . import mod\n",
        },
        {
            "pkg/__init__.py": "",
            "pkg/types/__init__.py": TYPES,
            "pkg/types/sub.py": "thing = 1\n",
            "pkg/validator.py": "from .types.sub import thing\n",
        },
        {
            "pkg/__init__.py": "",
            "pkg/types.py": TYPES,
            "pkg/validator.py": GUARD,
        },
    ],
    ids=[
        "dotdot-in-subpackage",
        "in-init",
        "stdlib-named-sibling",
        "from-dot-import-mod",
        "sub-of-package",
        "main-guard",
    ],
)
def test_a_relative_import_the_tree_resolves_passes(tmp_path, layout):
    new = {path: text + "\nEXTRA = 1\n" for path, text in layout.items() if path.endswith(".py")}
    assert _verdict(tmp_path, layout, new) is None


@pytest.mark.parametrize("top", ["unittest", "json", "logging", "email", "xml", "importlib", "os"])
@pytest.mark.parametrize("inits", [False, True])
@pytest.mark.parametrize("sub", ["", "sub/"])
def test_a_package_named_like_the_stdlib_is_not_exempt(tmp_path, top, inits, sub):
    level = ".." if sub else "."
    files = {
        f"{top}/{sub}test_loc.py": f"from {level}mock import patch" + CALL,
        f"{top}/types.py": TYPES,
    }
    files[f"{top}/mock.py"] = MOCK
    if inits:
        files |= {f"{top}/__init__.py": "", **({f"{top}/sub/__init__.py": ""} if sub else {})}
    new = {path: text + "\nEXTRA = 1\n" for path, text in files.items()}
    assert _verdict(tmp_path, files, new) == REFUSED


@pytest.mark.parametrize(
    "layout",
    [
        {"pkg/validator.py": "from .types import type_ok\n"},  # no __init__.py: a namespace dir
        {"pkg/__init__.py": "", "pkg/validator.py": "from .types.deep import ok\n"},  # no target
        {"pkg/__init__.py": "", "pkg/validator.py": "from ..types import type_ok\n"},  # above top
        {"pkg/__init__.py": "", "pkg/sub/validator.py": "from ..types import type_ok\n"},  # chain
    ],
    ids=["no-init", "no-target", "above-top-level", "broken-chain"],
)
def test_a_relative_import_the_tree_cannot_resolve_is_scanned_as_before(tmp_path, layout):
    base = {**layout, "pkg/types.py": TYPES}
    new = {path: text + "\nEXTRA = 1\n" for path, text in base.items()}
    assert _verdict(tmp_path, base, new) == REFUSED


def test_the_scan_without_a_resolver_and_the_sealed_check_keep_the_old_rule(tmp_path):
    """sealed_packages reads whole commits with no change set to prove a target, so it stays
    strict: only the candidate refusal relaxes a relative import (#1807)."""
    source = "from .types import type_ok\n"
    assert scan_sys_path_blob(source.encode()) is True
    assert scan_sys_path_blob(source.encode(), lambda level, module: True) is False
    repo = tmp_path / "sealed"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "a@b")
    git(repo, "config", "user.name", "a")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "types.py").write_text(TYPES)
    (repo / "pkg" / "validator.py").write_text(source)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    assert may_edit_sys_path(repo, git(repo, "rev-parse", "HEAD")) is True


@pytest.mark.parametrize("top", ["requests", "numpy", "yaml", "attr", "pkg"])
def test_a_third_party_named_top_package_is_exempt_like_any_other(tmp_path, top):
    """Only standard-library names are barred: a package called like a popular third-party module
    is the candidate's own code in this tree, so its sibling import is exempt (main refused it)."""
    base = {
        f"{top}/__init__.py": "",
        f"{top}/types.py": TYPES,
        f"{top}/validator.py": "from .types import type_ok\n",
    }
    new = {path: text + "\nEXTRA = 1\n" for path, text in base.items()}
    assert _verdict(tmp_path, base, new) is None


SRC_BASE = {
    "conftest.py": (
        'import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))\n'
    ),
    "src/miniscema/__init__.py": "",
    "src/miniscema/types.py": "def type_name(v):\n    return type(v).__name__\n",
    "src/miniscema/validator.py": (
        "from .types import type_ok, type_name\n\n\ndef check(v):\n    pass"
    ),
    "src/miniscema/report.py": "from .validator import v\n",
}


def test_a_real_src_layout_edit_passes(tmp_path):
    new = {
        "src/miniscema/types.py": SRC_BASE["src/miniscema/types.py"] + "\nEXTRA = 1\n",
        "src/miniscema/validator.py": SRC_BASE["src/miniscema/validator.py"] + "\nEXTRA = 1\n",
    }
    assert _verdict(tmp_path, SRC_BASE, new) is None


def test_the_same_src_layout_with_a_conftest_sys_path_edit_is_refused(tmp_path):
    new = {
        "src/miniscema/types.py": SRC_BASE["src/miniscema/types.py"] + "\nEXTRA = 1\n",
        "src/miniscema/validator.py": SRC_BASE["src/miniscema/validator.py"] + "\nEXTRA = 1\n",
        "conftest.py": SRC_BASE["conftest.py"].replace('"src"', '"."'),
    }
    assert _verdict(tmp_path, SRC_BASE, new) == REFUSED


def test_a_relative_import_with_a_same_file_sys_path_insert_is_refused(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/types.py": TYPES, "pkg/v.py": "X = 1\n"}
    new = {
        "pkg/types.py": TYPES + "\nEXTRA = 1\n",
        "pkg/v.py": "import sys\nsys.path.insert(0, 'pkg')\nfrom .types import type_ok\n",
    }
    assert _verdict(tmp_path, base, new) == REFUSED


def test_an_init_that_puts_its_directory_on_sys_path_next_to_a_shadow_file_is_refused(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/a.py": "X = 1\n"}
    new = {
        "pkg/__init__.py": "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n",
        "pkg/csv.py": "X = 2\n",
    }
    assert _verdict(tmp_path, base, new) == REFUSED


_PK = {"pkg/__init__.py": "", "pkg/mock.py": MOCK, "pkg/types.py": TYPES}
_EDIT = {"pkg/types.py": TYPES + "\nEXTRA = 1\n"}


@pytest.mark.parametrize(
    ("validator", "also_edit_types"),
    [
        (
            'import pkg.validator as me\nme.__setattr__("__pack" + "age__", "unittest")\n'
            "from .mock import patch\n",
            True,
        ),
        (
            "from . import validator as _me\n"
            'setattr(_me, "__" + "package__", "unittest")\nfrom .mock import patch\n',
            True,
        ),
        (
            "from . import validator as _me\n"
            'setattr(_me, "__" + "package__", "unittest")\nfrom .mock import patch\n',
            False,
        ),
        ('__package__ = "unittest"\nfrom .mock import patch\n', True),
        ('__package__ = "asyncio"\nfrom .subprocess import create_subprocess_exec\n', True),
    ],
)
def test_repointed_relative_import_of_a_stdlib_package_submodule_stays_refused(
    tmp_path, validator, also_edit_types
):
    """A relative import whose first step is named like a stdlib machinery sub-module
    (``unittest.mock``, ``asyncio.subprocess``, ``trace``) keeps the old comparison, so a file
    that repoints its package context cannot reach the real module (main refuses these too)."""
    base = {**_PK, "pkg/validator.py": "X = 1\n", "pkg/subprocess.py": "X = 1\n"}
    new = {"pkg/validator.py": validator} | (_EDIT if also_edit_types else {})
    assert _verdict(tmp_path, base, new) == REFUSED


def test_a_target_above_the_top_package_is_not_exempt(tmp_path):
    base = {
        "types.py": TYPES,
        "pkg/__init__.py": "",
        "pkg/types.py": TYPES,
        "pkg/validator.py": "X = 1\n",
    }
    new = {"pkg/types.py": TYPES + "\nEXTRA = 1\n", "pkg/validator.py": "from ..types import X\n"}
    assert _verdict(tmp_path, base, new) == REFUSED


def test_a_namespace_step_in_the_module_path_is_not_exempt(tmp_path):
    base = {"pkg/__init__.py": "", "pkg/types/inner.py": TYPES, "pkg/validator.py": "X = 1\n"}
    new = {
        "pkg/types/inner.py": TYPES + "\nEXTRA = 1\n",
        "pkg/validator.py": "from .types.inner import X\n",
    }
    assert _verdict(tmp_path, base, new) == REFUSED


def test_a_top_package_named_like_stdlib_in_another_case_is_not_exempt(tmp_path):
    base = {"Inspect/__init__.py": "", "Inspect/types.py": TYPES, "Inspect/v.py": "X = 1\n"}
    new = {"Inspect/types.py": TYPES + "\nEXTRA = 1\n", "Inspect/v.py": "from .types import X\n"}
    assert _verdict(tmp_path, base, new) == REFUSED


def test_a_sibling_named_like_the_stdlib_but_not_machinery_stays_exempt(tmp_path):
    base = {**_PK, "pkg/validator.py": "X = 1\n"}
    new = _EDIT | {"pkg/validator.py": "from .types import X\n"}
    assert _verdict(tmp_path, base, new) is None
