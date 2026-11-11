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
