"""Tests for Issue #1020: fixture alias resolution by source module."""

from __future__ import annotations

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

CONFTEST = "tests/sub/conftest.py"
FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
PATH = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
ABOVE = "tests/conftest.py"


def test_source_alias_imported_as_another_name_is_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_a.py": "import pytest\nfx = pytest.fixture\n",
        "tests/helper_b.py": (
            "from helper_a import fx as reg\n\n\n"
            "class Helpers:\n"
            "    @reg(autouse=True)\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_source_alias_assigned_at_module_level_is_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_a.py": "import pytest\nfx = pytest.fixture\n",
        "tests/helper_b.py": (
            "from helper_a import fx\n"
            "reg = fx\n\n\n"
            "class Helpers:\n"
            "    @reg(autouse=True)\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_source_alias_star_imported_is_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_a.py": "import pytest\nfx = pytest.fixture\n",
        "tests/helper_b.py": (
            "from helper_a import *\n\n\n"
            "class Helpers:\n"
            "    @fx(autouse=True)\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_tuple_target_with_no_fixture_source_is_not_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_b.py": (
            "something = object()\n"
            "reg, other = something, 1\n\n\n"
            "class Helpers:\n"
            "    @reg\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_alias_assigned_only_inside_function_is_not_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_a.py": (
            "import pytest\n\n\ndef make():\n    reg = pytest.fixture\n\n\nreg = object()\n"
        ),
        "tests/helper_b.py": (
            "from helper_a import reg\n\n\n"
            "class Helpers:\n"
            "    @reg(autouse=True)\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_star_import_from_unread_module_is_not_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_b.py": (
            "from unread_module import *  # noqa: F403\n\n\n"
            "reg = object()\n\n\n"
            "class Helpers:\n"
            "    @reg\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_unrelated_imported_plain_function_is_not_refused(repo):
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helper_a.py": "def reg(fn):\n    return fn\n",
        "tests/helper_b.py": (
            "from helper_a import reg\n\n\n"
            "class Helpers:\n"
            "    @reg\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


@pytest.mark.parametrize(
    ("size", "alias_source_first"),
    [(2, True), (2, False), (3, True), (3, False)],
    ids=["two-source-first", "two-reexport-first", "three-source-first", "three-reexport-first"],
)
def test_import_cycle_entry_orders_are_refused(repo, size, alias_source_first):
    """Two- and three-module cycles resolve regardless of which cycle node is visited first."""
    names = ["helper_a", "helper_b", "helper_c"][:size]
    if not alias_source_first:
        names.reverse()
    source = names[0]
    decorated = names[-1]
    base_files = {
        ABOVE: PATH + f"from {decorated} import Helpers  # noqa: F401\n",
    }
    base_files[f"tests/{source}.py"] = (
        "from pytest import fixture as fx\nreg = fx\n"
        f"from {names[-1]} import reg as reexport  # noqa: F401\n"
    )
    for previous, current in zip(names, names[1:], strict=False):
        base_files[f"tests/{current}.py"] = f"from {previous} import reg\n"
    base_files[f"tests/{decorated}.py"] += (
        "\n\nclass Helpers:\n    @reg\n    def guard(self, D):\n        pass\n"
    )
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_parent_conftest_uses_resolved_alias_extra_for_registered_fixture(repo):
    """M3b: a resolved source alias reaches bound() for a parent conftest."""
    base_files = {
        "tests/conftest.py": PATH
        + "from helper import reg\n\n\n@reg(name='D')\ndef other():\n    return 1\n",
        "tests/helper.py": "from pytest import fixture as reg\n",
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_changed_conftest_base_uses_resolved_alias_extra(repo):
    """M3c: a resolved source alias reaches bound() for the changed conftest's base version."""
    base_files = {
        CONFTEST: "from helper import reg\n\n\n@reg(name='D')\ndef other():\n    return 1\n",
        "tests/helper.py": "from pytest import fixture as reg\n",
    }
    changed = (
        base_files[CONFTEST] + "\n\nimport pytest\n\n\n@pytest.fixture\ndef D():\n    return 2\n"
    )
    with pytest.raises(dt.TestCommitRejected, match=r"base binds or a base test requests \(D\)"):
        _check(repo, {CONFTEST: changed}, path=CONFTEST, base_files=base_files)


def test_imported_module_attribute_decorator_is_refused(repo):
    """import helpers then @helpers.reg where helpers binds reg = pytest.fixture."""
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helpers.py": "import pytest\nreg = pytest.fixture\n",
        "tests/helper_b.py": (
            "import helpers\n\n\n"
            "class Helpers:\n"
            "    @helpers.reg\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)


def test_imported_module_attribute_decorator_through_alias_is_refused(repo):
    """import helpers then @helpers.reg where helpers binds fx then reg = fx."""
    base_files = {
        ABOVE: PATH + "from helper_b import Helpers  # noqa: F401\n",
        "tests/helpers.py": "from pytest import fixture as fx\nreg = fx\n",
        "tests/helper_b.py": (
            "import helpers\n\n\n"
            "class Helpers:\n"
            "    @helpers.reg\n"
            "    def guard(self, D):\n"
            "        pass\n"
        ),
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(D\)"):
        _check(repo, {CONFTEST: FIXTURE.format("D")}, path=CONFTEST, base_files=base_files)
