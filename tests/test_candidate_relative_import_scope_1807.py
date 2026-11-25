"""Issue #1807: a relative import is exempt only when the judged tree proves its target (the
chain of ``__init__.py`` files, a top-level package not named like the stdlib) and nothing in the
changed files or the package can repoint it. Cases and helpers live in the sibling test file."""

from __future__ import annotations

import pytest
from conftest import git
from test_candidate_relative_import_1807 import GUARD, MOCK, REFUSED, TYPES, _verdict

from cairnwork import relative_import_scope as scope
from cairnwork.sys_path_scan import may_edit_sys_path, scan_sys_path_blob

# --- design (layer1 ruling): a relative import is the package's own only when the judged tree
# proves it (A) and nothing in the changed files or the package can repoint it (B).
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


HELPERS = [
    '__all__ = ["__package__"]\n__package__ = "unittest"\n',
    '__all__ = ["__spec__"]\n__spec__ = None\n__package__ = "unittest"\n',
    '__all__ = ["__name__"]\n__name__ = "unittest.zz"\n',
    'globals().update({"__package__": "unittest"})\n',
]


@pytest.mark.parametrize("helper_changed", [False, True])
@pytest.mark.parametrize("helper", HELPERS)
def test_star_import_from_a_sibling_that_repoints_is_refused(tmp_path, helper, helper_changed):
    base = {
        "pkg/__init__.py": "",
        "pkg/types.py": TYPES,
        "pkg/mock.py": MOCK,
        "pkg/helpers.py": helper,
        "pkg/validator.py": "from .helpers import *\n" + PATCH,
    }
    new = {
        "pkg/types.py": TYPES + "EXTRA = 1\n",
        "pkg/validator.py": base["pkg/validator.py"] + "\n#\n",
    }
    if helper_changed:
        new["pkg/helpers.py"] = helper + "\nEXTRA = 1\n"
    assert _verdict(tmp_path, base, new) == REFUSED


@pytest.mark.parametrize("cycle", ["v.__package__", "v.__spec__", "v.__name__"])
def test_a_cycle_that_rewrites_the_importer_is_refused(tmp_path, cycle):
    base = {
        "pkg/__init__.py": "",
        "pkg/types.py": TYPES,
        "pkg/mock.py": MOCK,
        "pkg/helper.py": f'import pkg.validator as v\n{cycle} = "unittest"\n',
        "pkg/validator.py": "from . import helper\n" + PATCH,
    }
    new = {
        "pkg/types.py": TYPES + "EXTRA = 1\n",
        "pkg/validator.py": base["pkg/validator.py"] + "\n#\n",
    }
    assert _verdict(tmp_path, base, new) == REFUSED


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


SIZED = {
    "pkg/__init__.py": "",
    "pkg/types.py": TYPES,
    "pkg/validator.py": "from .types import type_ok\n",
}
SIZED_NEW = {path: text + "\nEXTRA = 1\n" for path, text in SIZED.items()}
SIZES = [len(text.encode()) for text in SIZED_NEW.values()]


@pytest.mark.parametrize(
    ("limit", "value", "refused"),
    [
        ("MAX_TOTAL_BYTES", sum(SIZES), False),
        ("MAX_TOTAL_BYTES", sum(SIZES) - 1, True),
        ("MAX_FILE_BYTES", max(SIZES), False),
        ("MAX_FILE_BYTES", max(SIZES) - 1, True),
    ],
    ids=["total-at-limit", "total-over", "file-at-limit", "file-over"],
)
def test_the_package_read_is_bounded_and_over_the_bound_is_not_exempt(
    tmp_path, monkeypatch, limit, value, refused
):
    monkeypatch.setattr(scope, "MAX_TOTAL_BYTES", 1 << 30)
    monkeypatch.setattr(scope, "MAX_FILE_BYTES", 1 << 30)
    monkeypatch.setattr(scope, limit, value)
    assert (_verdict(tmp_path, SIZED, SIZED_NEW) == REFUSED) is refused
