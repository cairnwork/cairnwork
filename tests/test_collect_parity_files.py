"""#1768 AC1, AC2, AC4: the files pytest collects on the base against the files Cairnwork counts.

A test file the base's own collection hooks hide (``collect_ignore``, ``collect_ignore_glob``,
``pytest_ignore_collect``) is counted by the P2P walk but never collected: refused UNCOMPARABLE
``P2P_BASE_FILES_UNCOLLECTED``. A difference in items inside a collected file is not a file-level
difference. End to end on the real sandbox; the pure comparison is tested without it.
"""

from __future__ import annotations

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CALC_TESTS, CONFTEST, MUL, MUL_TEST, _run

from cairnwork.collect_parity import file_mismatch

OTHER = "def test_other():\n    assert True\n"
HIDDEN = "tests/test_hidden.py"
HOOKS = {
    "collect_ignore": "collect_ignore = ['test_hidden.py']\n",
    "collect_ignore_glob": "collect_ignore_glob = ['test_hid*.py']\n",
    "pytest_ignore_collect": (
        "def pytest_ignore_collect(collection_path):\n"
        "    return collection_path.name == 'test_hidden.py'\n"
    ),
}
ITEM_LEVEL = {
    "modifyitems": (
        "def pytest_collection_modifyitems(items):\n"
        "    items[:] = [i for i in items if i.name != 'test_two']\n"
    ),
    "generate_tests": (
        "def pytest_generate_tests(metafunc):\n"
        "    if 'n' in metafunc.fixturenames:\n"
        "        metafunc.parametrize('n', [1, 2])\n"
    ),
}


def _case(repo, tmp_path, hook, extra, candidate=CALC + MUL):
    files = {"src/calc.py": CALC, "tests/conftest.py": CONFTEST + hook,
             "tests/test_calc.py": CALC_TESTS, **extra}  # fmt: skip
    commit_change(repo, files)
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    return _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": candidate},
                {"tests/test_calc.py": CALC_TESTS + MUL_TEST}, check)  # fmt: skip


# pure comparison


def test_a_counted_file_that_was_not_collected_is_a_mismatch():
    assert file_mismatch({"tests/test_a.py", "tests/test_b.py"}, {"tests/test_a.py"}) == [
        "tests/test_b.py"
    ]


def test_equal_sets_and_extra_collected_files_are_no_mismatch():
    assert file_mismatch({"tests/test_a.py"}, {"tests/test_a.py", "tests/test_c.py"}) == []


# end to end, real sandbox


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", sorted(HOOKS))
def test_a_counted_file_the_base_never_collects_is_refused(repo, tmp_path, kind):
    result = _case(repo, tmp_path, HOOKS[kind], {HIDDEN: OTHER})
    assert result.reason.startswith("P2P_BASE_FILES_UNCOLLECTED: "), result.reason
    assert not result.verified


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", sorted(HOOKS))
def test_the_same_hook_hiding_no_counted_file_is_not_refused(repo, tmp_path, kind):
    result = _case(repo, tmp_path, HOOKS[kind], {})  # nothing named test_hidden.py exists
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
@pytest.mark.parametrize("kind", sorted(ITEM_LEVEL))
def test_an_item_level_difference_is_not_a_file_level_refusal(repo, tmp_path, kind):
    extra = {"tests/test_items.py": "def test_one():\n    pass\ndef test_two():\n    pass\n"
             "def test_n(n=1):\n    pass\n"}  # fmt: skip
    result = _case(repo, tmp_path, ITEM_LEVEL[kind], extra)
    assert "P2P_BASE_FILES_UNCOLLECTED" not in (result.reason or ""), result.reason
    assert result.status == "COMPLETED" and result.verified, result.reason


@pytest.mark.sandbox
def test_a_plain_repo_with_no_hooks_is_not_refused(repo, tmp_path):
    result = _case(repo, tmp_path, "", {"tests/test_other.py": OTHER})
    assert result.status == "COMPLETED" and result.verified, result.reason
