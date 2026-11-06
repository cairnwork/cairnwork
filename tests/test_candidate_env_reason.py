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
WORDS = "report a write denied at"


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
