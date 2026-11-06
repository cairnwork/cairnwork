"""#929 and #930 (deep audit 11/06, report 02 F2 and F4): a base the P2P probe cannot fully see is
never read as "nothing to bind". Real pipeline runs (Seatbelt); each is a before/after row of the
PR: on main the candidate that breaks neg() ended COMPLETED / Core VERIFIED."""

import pytest
from conftest import commit_change
from test_p2p import CALC, CONFTEST, FIX_ADD, FIX_BREAKS_NEG, SIBLING, run

pytestmark = pytest.mark.sandbox

ADD = "from calc import add\ndef test_add():\n    assert add(2, 3)\n"
BUG = "from calc import add\ndef test_bug():\n    assert add(1, 1) == 2\n"  # fails on the base
# the base's own config ends the session at the first failure (as a maxfail=1 in its CI would)
STOPS = CONFTEST + "\n\ndef pytest_configure(config):\n    config.option.maxfail = 1\n"


@pytest.fixture
def project(repo):  # test_p2p's project: a flat tests/ with test_calc.py and test_sibling.py
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": CONFTEST,
                         "tests/test_calc.py": ADD,
                         "tests/test_sibling.py": SIBLING})  # fmt: skip
    return repo


@pytest.mark.parametrize("mode", ["whole-suite", "module-dir"])
def test_an_early_stopped_base_probe_is_not_used(project, tmp_path, mode):
    """#929: the base stops after test_bug fails; test_neg is collected but never runs."""
    extra = {"tests/conftest.py": STOPS, "tests/test_a_bug.py": BUG}
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode=mode, extra=extra)
    assert result.status == "FAILED" and not result.verified, result.reason
    assert "base-probe-incomplete" in result.reason + " ".join(result.logs)


def test_a_base_that_runs_every_collected_test_still_completes(project, tmp_path):
    """#929 control: the same base without the early stop; a correct candidate is COMPLETED."""
    extra = {"tests/test_a_bug.py": BUG}
    result = run(project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite", extra=extra)
    assert result.status == "COMPLETED", result.reason


def test_a_base_test_under_a_dotted_directory_is_not_dropped_silently(project, tmp_path):
    """#930: pytest collects tests/v1.0/test_far.py, the only base test of neg(); no verifier
    target can name it (on main: dropped, P2P bound nothing about neg(), VERIFIED)."""
    extra = {"tests/test_sibling.py": ADD, "tests/v1.0/test_far.py": SIBLING}
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite",
                 extra=extra)  # fmt: skip
    assert result.status == "FAILED" and not result.verified, result.reason  # before the worker
    assert result.reason.startswith("UNCOMPARABLE: P2P_BASE_UNTARGETABLE: "), result.reason


def test_a_hidden_directory_pytest_skips_is_no_reason_to_refuse(project, tmp_path):
    """#930 control: pytest's default norecursedirs skips '.*' directories, so they are ignored."""
    commit_change(project, {"tests/.cache/test_tmp.py": SIBLING})
    result = run(project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite")
    assert result.status == "COMPLETED", result.reason


@pytest.mark.parametrize("skipped", ["tests/.cache/test_sibling.py", "tests/b.egg/test_sibling.py"])
def test_a_test_file_pytest_skips_is_never_named_to_the_probe(project, tmp_path, skipped):
    """#930 (review r1): a file under a directory pytest's default norecursedirs skips is left out
    of the probe as on main. Named, it would be collected, and its module name clashes with
    tests/test_sibling.py: the whole-suite probe fails and module-dir misses the neg() test."""
    commit_change(project, {skipped: SIBLING})
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite")
    assert result.status == "FAILED" and not result.verified, result.reason
    assert result.p2p["degraded"] == "" and result.p2p["applied"] == "whole-suite"
