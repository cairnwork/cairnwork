"""#1833: a base that is only imported can never be an earlier class of the same file."""

from __future__ import annotations

import ast

from cairnwork.deferred_nodes import test_functions as derive

_P = "class P(unittest.TestCase):\n    def test_a(self):\n        pass\n"
_I = "class I(Base):\n    def test_b(self):\n        pass\n"


def _ids(source: str) -> set[str]:
    return set(derive(ast.parse(source))[0])


def test_an_imported_base_does_not_pull_in_the_earlier_class() -> None:
    ids = _ids(f"import unittest\nfrom helpers import Base\n{_P}{_I}")
    assert "I::test_a" not in ids and {"P::test_a", "I::test_b"} <= ids


def test_an_import_as_name_is_still_only_imported() -> None:
    ids = _ids(f"import unittest\nfrom helpers import Other as Base\n{_P}{_I}")
    assert "I::test_a" not in ids and {"P::test_a", "I::test_b"} <= ids


def test_a_rebound_base_name_keeps_the_conservative_reading() -> None:
    source = f"import unittest\nfrom helpers import Base\nBase = make()\n{_P}{_I}"
    assert {"P::test_a", "I::test_a", "I::test_b"} <= _ids(source)


def test_a_star_import_keeps_the_conservative_reading() -> None:
    source = f"import unittest\nfrom helpers import *\n{_P}{_I}"
    assert {"P::test_a", "I::test_a", "I::test_b"} <= _ids(source)


def test_a_base_with_no_import_keeps_the_conservative_reading() -> None:
    assert {"I::test_a"} <= _ids(f"import unittest\n{_P}{_I}")


def test_a_real_test_class_with_an_imported_base_is_still_collected_by_name() -> None:
    source = "from helpers import Base\nclass TestI(Base):\n    def test_b(self):\n        pass\n"
    assert _ids(source) == {"TestI::test_b"}


def test_an_imported_base_keeps_the_test_case_flag_of_an_earlier_class() -> None:
    # helpers.BaseTest may be a TestCase, so a non-Test* name is still collected (as on main)
    source = (
        "import unittest\nfrom helpers import BaseTest\n"
        f"{_P}class UserApi(BaseTest):\n    def test_b(self):\n        pass\n"
    )
    assert "UserApi::test_b" in _ids(source)
