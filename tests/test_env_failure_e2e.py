"""#808 end to end (real Seatbelt, a deferred test commit through run_task): a base test that
fails from the environment gives no teeth, so the candidate that "fixes" it is not VERIFIED.

Before/after for the same candidate (the fix in FIX), main -> this change:
- a MemoryError, a RecursionError, a built-in OSError wrapped in a RuntimeError (cause or
  context only), a child that exited 127 or was killed by a signal: COMPLETED -> FAILED with
  BASE_ENV_ERROR (UNCOMPARABLE).
- an AssertionError, an assert inside ``except OSError``, a checkout-defined RuntimeError,
  a child that ran the code under test and exited 1: COMPLETED -> COMPLETED.
- a checkout-defined OSError subclass: BASE_ENV_ERROR -> BASE_ENV_ERROR (as on main; #797's gate).
- one target with real teeth next to an environment one: COMPLETED -> COMPLETED.
"""

import pytest
import test_deferred_tests as td
from conftest import commit_change
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

import cairnwork.deferred_tests as dt

pytestmark = pytest.mark.sandbox
project = td.project  # the deferred-tests project fixture, under its own name

HEAD = "import subprocess, sys\nfrom calc import add\n"
GONE = "open('/nonexistent/zz')"
ENV = {
    "ac1_memoryerror": "if add(1, 1) != 2:\n        raise MemoryError('limit')",
    "ac2_recursionerror": "if add(1, 1) != 2:\n        f(0)",
    "ac3_oserror_as_cause": "if add(1, 1) != 2:\n        load('cause')",
    "ac3_oserror_as_context": "if add(1, 1) != 2:\n        load('context')",
    "ac4_child_exit_127": "ok = add(1, 1) == 2\n"
    "    subprocess.run(['sh', '-c', 'exit 0' if ok else 'exit 127'], check=True)",
    "ac4_child_killed_by_signal": "ok = add(1, 1) == 2\n"
    "    subprocess.run(['sh', '-c', 'exit 0' if ok else 'kill -9 $$'], check=True)",
}
HELPERS = (
    "def f(n):\n    return f(n + 1)\n"
    "def load(how):\n"
    "    try:\n        " + GONE + "\n"
    "    except OSError as exc:\n"
    "        if how == 'cause':\n            raise RuntimeError('cannot load') from exc\n"
    "        raise RuntimeError('cannot load')\n"
)
TEETH = {
    "n1_assertion": "assert add(1, 1) == 2",
    "n4_child_ran_the_code_and_exited_1": "subprocess.run([sys.executable, '-c', 'import sys; "
    'sys.path.insert(0, "src"); from calc import add; sys.exit(add(1, 1) - 2)\'], check=True)',
}
N5 = "try:\n        " + GONE + "\n    except OSError:\n        assert add(1, 1) == 2"


def body(statement):
    return HEAD + HELPERS + f"def test_r():\n    {statement}\n"


def verdict(project, tmp_path, monkeypatch, tests, src=None):
    """(status, reason, the base's teeth kind) of run_task for the fix FIX on a base whose
    deferred tests are ``tests`` (src: code committed to the base first)."""
    if src:
        commit_change(project, {"src/calc.py": src})
    seen = []
    orig = dt.base_teeth

    def spy(runs, targets):
        result = orig(runs, targets)
        seen.append(result[0])
        return result

    monkeypatch.setattr(dt, "base_teeth", spy)
    base, commit = make_test_commit(project, {"tests/test_regression.py": tests})
    res = run(project, tmp_path, base, commit, SeeingWorker(FIX))
    return res.status, res.reason, seen[0] if seen else None


@pytest.mark.parametrize("name", list(ENV))
def test_environment_failures_on_the_base_give_no_teeth(name, project, tmp_path, monkeypatch):
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, body(ENV[name]))
    assert teeth == "UNCOMPARABLE", (name, status, reason)
    assert status == "FAILED" and reason.startswith("BASE_ENV_ERROR: "), reason
    assert "subprocess that could not run" in reason


@pytest.mark.parametrize("name", list(TEETH))
def test_real_test_failures_on_the_base_keep_their_teeth(name, project, tmp_path, monkeypatch):
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, body(TEETH[name]))
    assert (teeth, status, reason) == ("TEETH", "COMPLETED", "CORE_VERIFIED"), name


def test_n5_an_assert_inside_except_oserror_is_the_tests_own_judgement(
    project, tmp_path, monkeypatch
):
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, body(N5))
    assert (teeth, status, reason) == ("TEETH", "COMPLETED", "CORE_VERIFIED")


def test_n3_a_checkout_defined_runtimeerror_is_behaviour(project, tmp_path, monkeypatch):
    src = "class OwnRun(RuntimeError):\n    pass\ndef add(a, b):\n    raise OwnRun('broken')\n"
    tests = HEAD + "def test_r():\n    assert add(1, 1) == 2\n"
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, tests, src=src)
    assert (teeth, status, reason) == ("TEETH", "COMPLETED", "CORE_VERIFIED")


def test_n2_a_checkout_defined_oserror_subclass_is_environment_as_on_main(
    project, tmp_path, monkeypatch
):
    src = "class OwnOS(OSError):\n    pass\ndef add(a, b):\n    raise OwnOS('broken')\n"
    tests = HEAD + "def test_r():\n    assert add(1, 1) == 2\n"
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, tests, src=src)
    assert teeth == "UNCOMPARABLE" and reason.startswith("BASE_ENV_ERROR: "), (status, reason)


def test_ac6_one_target_with_real_teeth_is_enough_next_to_an_environment_one(
    project, tmp_path, monkeypatch
):
    tests = body(ENV["ac1_memoryerror"]) + "def test_real():\n    assert add(1, 1) == 2\n"
    status, reason, teeth = verdict(project, tmp_path, monkeypatch, tests)
    assert (teeth, status, reason) == ("TEETH", "COMPLETED", "CORE_VERIFIED")


@pytest.mark.skip(reason="AC5: pytest_timeout is not installed in the verifier Python (step 3)")
def test_ac5_a_pytest_timeout_failure_gives_no_teeth():
    from cairnwork.pytest_config import PLUGINS

    assert any(module == "pytest_timeout" for _, module, _ in PLUGINS)
