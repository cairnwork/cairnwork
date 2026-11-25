"""#1781 item 2: a base run that died from a sandbox-denied operation says so in BASE_ENV_ERROR.

Before this change the reason said only "the test body failed from the environment (OSError ...)"
for a write, a file copy or a tree copy that the sandbox denied. The verdict (UNCOMPARABLE
BASE_ENV_ERROR) is unchanged; only the reason now names the cause class and, when the tests are
visible, the path. The run has deferred tests, so the path is withheld (#1186). Real Seatbelt."""

import sys

import pytest
from conftest import commit_change
from test_uncomparable_paths import run_cli

DENIED = "/private/tmp/cw1781-denied-probe"  # outside every write root of a run
KEEP = "def add(a, b):\n    return a + b + 1\n"
TEST = "def test_regression():\n    from calc import load\n    assert load() == 1\n"
BASE_TEETH = KEEP + "def load():\n    return 0\n"
WORDS = "operation not permitted (EPERM)"
OPERATIONS = {
    "write": f"open({DENIED!r}, 'w').close()",
    "file_copy": f"__import__('shutil').copy(__file__, {DENIED!r})",
    "tree_copy": f"__import__('shutil').copytree('.', {DENIED!r})",
}


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt denials")
@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_a_denied_base_operation_is_named_in_base_env_error(project, state, name):
    base_src = KEEP + f"def load():\n    {OPERATIONS[name]}\n    return 1\n"
    commit_change(project, {"src/calc.py": base_src})  # the base itself hits the denial
    code, out = run_cli(project, state, TEST, {"src/calc.py": BASE_TEETH})
    assert out["status"] == "UNCOMPARABLE", out
    assert out["uncomparable"]["code"] == "BASE_ENV_ERROR", out
    # the reason fields only: the run's separate deny-channel notes list their own paths (not
    # part of this change)
    for text in (out["uncomparable"]["detail"], out["reason"]):
        assert WORDS in text and "1 denied path (withheld)" in text and DENIED not in text, out


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt denials")
def test_a_base_env_error_without_a_denial_keeps_todays_reason(project, state):
    base_src = KEEP + "def load():\n    open('/nonexistent/zz')\n    return 1\n"
    commit_change(project, {"src/calc.py": base_src})
    code, out = run_cli(project, state, TEST, {"src/calc.py": BASE_TEETH})
    assert out["uncomparable"]["code"] == "BASE_ENV_ERROR", out
    assert WORDS not in out["uncomparable"]["detail"], out


def _run(outcomes, kinds, denied, hidden=False):
    from types import SimpleNamespace

    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), returncode=1, forged=False, data_refused=(),
        timed_out=False, survivors=0, mutated=(), report_too_big=False, log_too_big=False,
        collect_errors=(), recorded=True, teardown_errors=(), skipped_deps=(),
        outcomes=outcomes, fail_kind=kinds, denied=denied, hidden_tests=hidden,
    )  # fmt: skip


def test_the_visible_form_names_the_path_and_a_mix_keeps_todays_reason():
    from cairnwork.base_teeth import base_teeth

    both = {"t.py::a": "failed", "t.py::b": "failed"}
    kinds = {"t.py::a": "env", "t.py::b": "env"}
    targets = {"t.py::a", "t.py::b"}
    one = _run(both, kinds, {"t.py::a": "/x/it's\n::error::y", "t.py::b": "/x/z"})
    _, code, detail = base_teeth([one], targets)
    assert code == "BASE_ENV_ERROR" and WORDS in detail
    assert "\n" not in detail and "/x/it" in detail  # the path is escaped, never raw
    mixed = _run(both, kinds, {"t.py::a": "/x/y"})  # t.py::b failed from the environment otherwise
    assert WORDS not in base_teeth([mixed], targets)[2]
