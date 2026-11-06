"""#962 step 1: a candidate run whose failing tests all died with a sandbox-denied write says so.

The verdict stays FAILED (UNCOMPARABLE is step 2); only the reason names the denied path and says
it is likely the run environment. The base run keeps BASE_ENV_ERROR. Real Seatbelt denials."""

import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import commit_change
from test_uncomparable_paths import run_cli

from cairnwork.reasons import candidate_denial
from cairnwork.verdict_record import denied_paths

DENIED = "/private/tmp/cw962-denied-probe"  # outside every write root of a run
KEEP = "def add(a, b):\n    return a + b + 1\n"  # the project's own tests import it
TEST = "def test_regression():\n    from calc import load\n    assert load() == 1\n"
TWO = (
    TEST + "def test_other():\n    from calc import add\n    assert add(1, 1) == 99\n"
)  # plain failure
BASE_TEETH = KEEP + "def load():\n    return 0\n"  # plain assertion failure on the base
WRITES = KEEP + f"def load():\n    open({DENIED!r}, 'w').close()\n    return 1\n"
WRONG = KEEP + "def load():\n    return 2\n"
WORDS = "report an operation not permitted (EPERM) at"
SKIPS = TEST + "def test_other():\n    from calc import other\n    assert other() == 1\n"
SKIPPER = WRITES + "def other():\n    import pytest\n    pytest.skip('the candidate skips it')\n"
MUTATES = KEEP + (
    f"def load():\n    open(__file__, 'a').write('\\n')\n    open({DENIED!r}, 'w').close()\n"
    "    return 1\n"
)  # rewrites its own checkout, then hits the denial


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt denials")
class TestRealSandbox:
    def test_ac1_a_denied_write_names_the_path_and_stays_failed(self, project, state):
        commit_change(project, {"src/calc.py": BASE_TEETH})
        code, out = run_cli(project, state, TEST, {"src/calc.py": WRITES})
        text = json.dumps(out)
        assert out["status"] == "FAILED" and out["verified"] is False, out
        assert WORDS in text and DENIED in text and "unless the change raised this itself" in text

    def test_ac1_negative_an_assertion_failure_keeps_todays_reason(self, project, state):
        commit_change(project, {"src/calc.py": BASE_TEETH})
        code, out = run_cli(project, state, TEST, {"src/calc.py": WRONG})
        assert out["status"] == "FAILED" and WORDS not in json.dumps(out), out

    def test_ac1_negative_a_mix_keeps_todays_reason(self, project, state):
        commit_change(project, {"src/calc.py": BASE_TEETH})
        code, out = run_cli(project, state, TWO, {"src/calc.py": WRITES})
        assert out["status"] == "FAILED" and WORDS not in json.dumps(out), out

    def test_a_denial_plus_a_skipped_test_keeps_todays_reason(self, project, state):
        commit_change(project, {"src/calc.py": BASE_TEETH})
        code, out = run_cli(project, state, SKIPS, {"src/calc.py": SKIPPER})
        assert out["status"] == "FAILED" and WORDS not in json.dumps(out), out

    def test_a_denial_after_rewriting_the_checkout_keeps_todays_reason(self, project, state):
        commit_change(project, {"src/calc.py": BASE_TEETH})
        code, out = run_cli(project, state, TEST, {"src/calc.py": MUTATES})
        text = json.dumps(out)
        assert out["status"] == "FAILED" and "checkout changed" in text, out
        assert WORDS not in text

    def test_ac4_the_base_run_keeps_base_env_error(self, project, state):
        commit_change(project, {"src/calc.py": WRITES})  # the base itself hits the denial
        code, out = run_cli(project, state, TEST, {"src/calc.py": BASE_TEETH})
        assert out["status"] == "UNCOMPARABLE", out
        assert out["uncomparable"]["code"] == "BASE_ENV_ERROR", out
        assert WORDS not in json.dumps(out)


ROOTS = ("/work/checkouts", "/run/x")


def test_the_rule_needs_every_failed_test_denied_outside_the_roots():
    out = {"a": "failed", "b": "failed", "c": "passed"}
    assert candidate_denial(out, {"a": "/tmp/x", "b": "/tmp/y"}, ROOTS) == "/tmp/x"
    assert candidate_denial(out, {"a": "/tmp/x"}, ROOTS) == ""  # b failed another way
    assert candidate_denial({"a": "passed"}, {}, ROOTS) == ""  # nothing failed
    skip = {"a": "failed", "s": "skipped"}
    assert candidate_denial(skip, {"a": "/tmp/x"}, ROOTS) == ""  # a skip is not a denial


def test_a_denial_inside_a_write_root_or_a_relative_path_is_no_sandbox_denial():
    out = {"a": "failed"}
    assert candidate_denial(out, {"a": "/run/x/f"}, ROOTS) == ""
    assert candidate_denial(out, {"a": "/run/x/sub/../f"}, ROOTS) == ""
    assert candidate_denial(out, {"a": "rel/path"}, ROOTS) == ""  # not absolute: unknown


def test_a_malformed_denied_record_is_ignored():
    kind = {"a": "env", "b": "setup"}
    assert denied_paths({"denied": {"a": "/tmp/x"}}, kind) == {"a": "/tmp/x"}
    assert denied_paths({"denied": {"b": "/tmp/x"}}, kind) == {}  # not an env node
    assert denied_paths({"denied": {"a": 5}}, kind) == {}
    assert denied_paths({"denied": ["a"]}, kind) == {}
    assert denied_paths({}, kind) == {}


def test_ac4_a_base_run_gets_no_denied_path_wording():
    from types import SimpleNamespace

    from cairnwork.verifiers import _denial

    verdict = SimpleNamespace(outcomes={"a": "failed"}, denied={"a": "/private/tmp/x"})
    assert (
        _denial(cast(Any, SimpleNamespace(deny=True)), verdict, Path("/w/c"), Path("/r")) == ""
    )  # base
    assert _denial(cast(Any, SimpleNamespace(deny=False)), verdict, Path("/w/c"), Path("/r")) == (
        "/private/tmp/x"
    )


def test_only_eperm_names_a_path():
    """A Seatbelt denial is EPERM; EACCES (plain file permissions, a read) names nothing."""
    from cairnwork.report_guard_parts import PARTS_SOURCE

    plugin: dict = {"os": __import__("os")}
    exec(PARTS_SOURCE, plugin)  # plugin text, run as the guard runs it
    _denied_path = plugin["_denied_path"]
    assert _denied_path(PermissionError(1, "Operation not permitted", "/p/x")) == "/p/x"
    assert _denied_path(PermissionError(13, "Permission denied", "/p/x")) is None
    assert _denied_path(RuntimeError("wrapped")) is None


MIXED = (
    "def test_sub(subtests):\n"
    "    with subtests.test(i=0):\n"
    "        assert False\n"
    "    with subtests.test(i=1):\n"
    "        raise PermissionError(1, 'Operation not permitted', '/private/tmp/cw962-sub')\n"
)


def test_a_node_that_also_failed_another_way_has_no_denied_path(tmp_path):
    from test_teardown_errors import guard_run

    rc, verdict = guard_run(tmp_path, {"tests/test_sub.py": MIXED}, "tests")
    assert rc == 1 and verdict.outcomes == {"tests/test_sub.py::test_sub": "failed"}
    assert verdict.denied == {}
    only = MIXED.replace("assert False", "raise PermissionError(1, 'x', '/p/y')")
    (tmp_path / "b").mkdir()
    rc, verdict = guard_run(tmp_path / "b", {"tests/test_sub.py": only}, "tests")
    assert list(verdict.denied) == ["tests/test_sub.py::test_sub"]  # every call was denied
