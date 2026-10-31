"""#570: pytest's ``subtests`` fixture and unittest's ``self.subTest`` send one call report per
sub-test under the parent test's node id, before the parent's own call report. The guard keeps
the worst call outcome a node had (failed > skipped > passed), so a failing or skipped sub-test
stays in the record instead of being replaced by the parent's report."""

import pytest
from test_teardown_errors import guard_run

T = "tests/test_sub.py"

SUBTESTS_FAIL = (
    "def test_sub(subtests):\n"
    "    for i in range(3):\n"
    "        with subtests.test(i=i):\n"
    "            assert i != 1\n"
)
SUBTESTS_SKIP = (
    "import pytest\n"
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n"
    "        pytest.skip('not here')\n"
    "    with subtests.test(i=1):\n"
    "        assert True\n"
)
SUBTESTS_SKIP_THEN_FAIL = (
    "import pytest\n"
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n"
    "        pytest.skip('not here')\n"
    "    with subtests.test(i=1):\n"
    "        assert False\n"
)
SUBTESTS_FAIL_THEN_SKIP = (
    "import pytest\n"
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n"
    "        assert False\n"
    "    with subtests.test(i=1):\n"
    "        pytest.skip('not here')\n"
)
SUBTESTS_XFAIL = (
    "import pytest\n"
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n"
    "        pytest.xfail('not yet')\n"
)
SUBTESTS_PASS = (
    "def test_sub(subtests):\n"
    "    for i in range(3):\n"
    "        with subtests.test(i=i):\n"
    "            assert i >= 0\n"
)
UNITTEST_FAIL = (
    "import unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_sub(self):\n"
    "        for i in range(3):\n"
    "            with self.subTest(i=i):\n"
    "                self.assertNotEqual(i, 1)\n"
)
UNITTEST_SKIP = (
    "import unittest\n"
    "class T(unittest.TestCase):\n"
    "    def test_sub(self):\n"
    "        with self.subTest(i=0):\n"
    "            self.skipTest('not here')\n"
    "        with self.subTest(i=1):\n"
    "            self.assertTrue(True)\n"
)


@pytest.mark.parametrize(
    ("source", "rc", "outcome"),
    [
        pytest.param(SUBTESTS_FAIL, 1, "failed", id="subtests-fail"),
        pytest.param(SUBTESTS_SKIP, 0, "skipped", id="subtests-skip"),
        pytest.param(SUBTESTS_SKIP_THEN_FAIL, 1, "failed", id="subtests-skip-then-fail"),
        pytest.param(SUBTESTS_FAIL_THEN_SKIP, 1, "failed", id="subtests-fail-then-skip"),
        pytest.param(SUBTESTS_XFAIL, 0, "skipped", id="subtests-xfail"),
    ],
)
def test_a_sub_test_outcome_is_kept_for_its_test(tmp_path, source, rc, outcome):
    got_rc, v = guard_run(tmp_path, {T: source}, "tests")
    assert got_rc == rc and v.recorded and not v.ran
    assert v.outcomes == {f"{T}::test_sub": outcome}


@pytest.mark.parametrize(
    ("source", "rc", "outcome"),
    [
        pytest.param(UNITTEST_FAIL, 1, "failed", id="subTest-fail"),
        pytest.param(UNITTEST_SKIP, 0, "skipped", id="subTest-skip"),
    ],
)
def test_a_unittest_sub_test_outcome_is_kept_for_its_test(tmp_path, source, rc, outcome):
    got_rc, v = guard_run(tmp_path, {T: source}, "tests")
    assert got_rc == rc and v.recorded and not v.ran
    assert v.outcomes == {f"{T}::T::test_sub": outcome}


def test_sub_tests_that_all_pass_record_a_passed_test(tmp_path):
    rc, v = guard_run(tmp_path, {T: SUBTESTS_PASS}, "tests")
    assert rc == 0 and v.ran and v.outcomes == {f"{T}::test_sub": "passed"}


def test_a_sub_test_failure_next_to_another_failure_is_recorded_for_both(tmp_path):
    files = {T: SUBTESTS_FAIL, "tests/test_bug.py": "def test_bug():\n    assert False\n"}
    rc, v = guard_run(tmp_path, files, "tests")
    assert rc == 1 and v.outcomes == {"tests/test_bug.py::test_bug": "failed",
                                      f"{T}::test_sub": "failed"}  # fmt: skip


STATEFUL = (
    "import os\n"
    "def test_f():\n"
    "    first = not os.path.exists('seen')\n"
    "    open('seen', 'w').close()\n"
    "    assert not first\n"
)


def test_a_node_run_twice_keeps_its_failed_run(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_d.py": STATEFUL}, "--keep-duplicates",
                      "tests/test_d.py", "tests/test_d.py")  # fmt: skip
    assert rc == 1 and not v.ran and v.outcomes == {"tests/test_d.py::test_f": "failed"}


SETUP_SKIPS_SECOND_TIME = (
    "import os, pytest\n"
    "@pytest.fixture\ndef once():\n"
    "    if os.path.exists('seen'):\n        pytest.skip('second run')\n"
    "    open('seen', 'w').close()\n"
    "def test_f(once):\n    assert True\n"
)


def test_a_node_run_twice_keeps_its_skipped_setup(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_d.py": SETUP_SKIPS_SECOND_TIME}, "--keep-duplicates",
                      "tests/test_d.py", "tests/test_d.py")  # fmt: skip
    assert rc == 0 and not v.ran and v.outcomes == {"tests/test_d.py::test_f": "skipped"}


FAILS_THEN_SETUP_SKIPS = (
    "import os, pytest\n"
    "@pytest.fixture\ndef second():\n"
    "    if os.path.exists('seen'):\n        pytest.skip('second run')\n"
    "def test_f(second):\n"
    "    open('seen', 'w').close()\n"
    "    assert False\n"
)


def test_a_failed_run_is_kept_when_the_next_run_of_the_node_skips_in_setup(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_d.py": FAILS_THEN_SETUP_SKIPS}, "--keep-duplicates",
                      "tests/test_d.py", "tests/test_d.py")  # fmt: skip
    assert rc == 1 and not v.ran and v.outcomes == {"tests/test_d.py::test_f": "failed"}


SUB_TEST_IN_FIXTURE_AFTER_YIELD = (
    "import pytest\n"
    "@pytest.fixture\ndef checked(subtests):\n"
    "    yield\n"
    "    with subtests.test(msg='after'):\n        assert True\n"
    "def test_sub(checked):\n    assert False\n"
)
SUB_TEST_IN_FIXTURE_BEFORE_YIELD = (
    "import pytest\n"
    "@pytest.fixture\ndef checked(subtests):\n"
    "    with subtests.test(msg='before'):\n        assert False\n"
    "    yield\n"
    "def test_sub(checked):\n    assert True\n"
)
SUB_TEST_IN_XFAIL = (
    "import pytest\n"
    "@pytest.mark.xfail(reason='known')\n"
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n        assert False\n"
)


@pytest.mark.parametrize(
    ("source", "rc", "outcome"),
    [
        pytest.param(SUB_TEST_IN_FIXTURE_AFTER_YIELD, 1, "failed", id="fixture-after-yield"),
        pytest.param(SUB_TEST_IN_FIXTURE_BEFORE_YIELD, 1, "failed", id="fixture-before-yield"),
        pytest.param(SUB_TEST_IN_XFAIL, 0, "skipped", id="xfail-test"),
    ],
)
def test_a_sub_test_in_a_fixture_or_an_xfail_test_is_kept(tmp_path, source, rc, outcome):
    got_rc, v = guard_run(tmp_path, {T: source}, "tests")
    assert got_rc == rc and v.recorded and not v.ran
    assert v.outcomes == {f"{T}::test_sub": outcome}


PLAIN = (
    "import pytest\n"
    "@pytest.fixture\ndef broken():\n    raise RuntimeError('setup')\n"
    "@pytest.fixture\ndef skipping():\n    pytest.skip('setup skip')\n"
    "def test_pass():\n    assert True\n"
    "def test_fail():\n    assert False\n"
    "def test_skip():\n    pytest.skip('no')\n"
    "@pytest.mark.xfail(reason='known')\ndef test_xfail():\n    assert False\n"
    "@pytest.mark.xfail(reason='known')\ndef test_xpass():\n    assert True\n"
    "@pytest.mark.xfail(reason='known', strict=True)\ndef test_xpass_strict():\n    assert True\n"
    "def test_setup_error(broken):\n    pass\n"
    "def test_setup_skip(skipping):\n    pass\n"
)


def test_tests_without_sub_tests_are_recorded_as_before(tmp_path):
    rc, v = guard_run(tmp_path, {"tests/test_plain.py": PLAIN}, "tests")
    p = "tests/test_plain.py::"
    assert rc == 1 and not v.ran and v.teardown_errors == ()
    assert v.outcomes == {
        f"{p}test_pass": "passed", f"{p}test_fail": "failed", f"{p}test_skip": "skipped",
        f"{p}test_xfail": "skipped", f"{p}test_xpass": "passed",
        f"{p}test_xpass_strict": "failed", f"{p}test_setup_error": "failed",
        f"{p}test_setup_skip": "skipped",
    }  # fmt: skip
