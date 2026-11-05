"""#854 (#797 step 3) in the real Seatbelt sandbox, through ``cairnwork-verify``.

Before/after for the same candidates (main → this change):
- a1: checkout code catches a sandbox denial and raises it inside an ExceptionGroup; a wrong
  candidate drops the denied call. VERIFIED → UNCOMPARABLE BASE_ENV_ERROR.
- a4: the same base with another candidate: UNCOMPARABLE on both sides (the base has no teeth).
- b1: an honest bug reported as a group of plain errors, next to a denial the code catches and
  handles; a correct candidate. VERIFIED → VERIFIED.
- b4: the same base, a wrong candidate. FAILED → FAILED.
- d1: a candidate whose own failure wraps a denial-shaped OSError. FAILED → FAILED.
- A wrapped OSError whose ``errno`` raises when read: the plugin fails, so the base is UNCOMPARABLE.
"""

import sys

import pytest
from conftest import commit_change
from test_uncomparable_paths import run_cli

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt denials"),
]

DENIED = "/private/etc/protocols"  # outside the policy's read roots
KEEP = "def add(a, b):\n    return a + b + 1\n"  # the project's own tests import it
TEST = "def test_regression():\n    from calc import load\n    assert load() == 1\n"
GROUPED = KEEP + (
    "def load():\n"
    "    errors = []\n"
    "    try:\n"
    f"        open({DENIED!r}).close()\n"
    "    except OSError as exc:\n"
    "        errors.append(exc)\n"
    "    if errors:\n"
    "        raise ExceptionGroup('load failed', errors)\n"
    "    return 1\n"
)
SKIPS_THE_READ = KEEP + "def load():\n    return 1\n"
READS_ANOTHER = KEEP + "def load():\n    import os\n    os.stat(__file__)\n    return 1\n"

HANDLED = KEEP + (
    "def total(xs):\n"
    "    return sum(xs) - 1\n"  # the bug
    "def load():\n"
    "    try:\n"
    f"        open({DENIED!r}).close()\n"
    "    except OSError:\n"
    "        pass\n"  # an optional file: the code handles the denial
    "    return total([1, 2])\n"
)
CHECKS = (
    "def test_regression():\n"
    "    from calc import load\n"
    "    problems = [AssertionError(f'load() gave {load()}')] if load() != 3 else []\n"
    "    if problems:\n"
    "        raise ExceptionGroup('wrong result', problems)\n"
)
FIXED = HANDLED.replace("sum(xs) - 1", "sum(xs)")
STILL_WRONG = HANDLED.replace("sum(xs) - 1", "sum(xs) + 1")
OWN_DENIAL = KEEP + (
    "import errno\n"
    "def load():\n"
    "    raise ValueError('cannot load') from PermissionError(errno.EPERM, 'denied')\n"
)


def test_a1_a_denial_re_raised_in_a_group_is_not_teeth(project, state):
    commit_change(project, {"src/calc.py": GROUPED})
    code, out = run_cli(project, state, TEST, {"src/calc.py": SKIPS_THE_READ})
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False, out
    assert out["uncomparable"]["code"] == "BASE_ENV_ERROR", out
    assert out["core_status"] == "" and out["binding"] is None


def test_a4_any_candidate_on_that_base_is_uncomparable_never_verified(project, state):
    commit_change(project, {"src/calc.py": GROUPED})
    code, out = run_cli(project, state, TEST, {"src/calc.py": READS_ANOTHER})
    assert out["status"] == "UNCOMPARABLE" and out["verified"] is False, out


def test_b1_a_handled_denial_next_to_a_grouped_bug_keeps_its_teeth(project, state):
    commit_change(project, {"src/calc.py": HANDLED})
    code, out = run_cli(project, state, CHECKS, {"src/calc.py": FIXED})
    assert code == 0 and out["status"] == "VERIFIED", out["uncomparable"]


def test_b4_a_wrong_candidate_on_that_base_fails(project, state):
    commit_change(project, {"src/calc.py": HANDLED})
    code, out = run_cli(project, state, CHECKS, {"src/calc.py": STILL_WRONG})
    assert out["status"] == "FAILED" and out["verified"] is False, out["uncomparable"]


def test_d1_a_candidate_that_wraps_a_denial_in_its_own_failure_fails(project, state):
    commit_change(project, {"src/calc.py": KEEP + "def load():\n    return 0\n"})  # plain teeth
    code, out = run_cli(project, state, TEST, {"src/calc.py": OWN_DENIAL})
    assert out["status"] == "FAILED" and out["verified"] is False, out["uncomparable"]


ODD_ERRNO = KEEP + (
    "class Odd(OSError):\n"
    "    errno = property(lambda self: 1 / 0)\n"  # the walk reads it: the plugin itself fails
    "def load():\n"
    "    raise ExceptionGroup('load failed', [Odd()])\n"
)


def test_an_oserror_whose_errno_raises_makes_the_base_uncomparable(project, state):
    # Codex #882 r1 (nit): the walk must fail closed when reading a wrapped error's errno raises
    commit_change(project, {"src/calc.py": ODD_ERRNO})
    code, out = run_cli(project, state, TEST, {"src/calc.py": SKIPS_THE_READ})
    assert out["status"] == "UNCOMPARABLE" and out["verified"] is False, out
