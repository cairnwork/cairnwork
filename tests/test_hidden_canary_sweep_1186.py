"""#1186: real hidden-test runs (--test-commit) never put a test file, function, message,
exception text, path or written file name into anything the author reads."""

import json

import pytest
from test_deferred_tests import make_test_commit
from verify_helpers import FIX, candidate, verify

F = "tests/test_CANARYfile.py"
CAN = ("CANARYfile", "CANARYname", "CANARYassert", "CANARYexc", "CANARYmodule", "CANARYreason",
       "CANARYwritten", "CANARYdenied", "CANARYfixture")  # fmt: skip


def _src(*lines):
    return "\n".join(lines) + "\n"


ADD = "from calc import add"
SCENARIOS = {
    "failed": _src(ADD, "def test_CANARYname():", "    assert add(1, 1) == 3, 'CANARYassert'"),
    "fixture-setup": _src(
        "import pytest", ADD, "@pytest.fixture", "def CANARYfixture():",
        "    raise RuntimeError('CANARYexc')",
        "def test_CANARYname(CANARYfixture):", "    assert add(1, 1) == 2",
    ),
    "collect-error": _src("import CANARYmodule", "def test_CANARYname():", "    assert True"),
    "skipped": _src("import pytest", "def test_CANARYname():", "    pytest.skip('CANARYreason')"),
    "xfailed": _src(
        "import pytest", "@pytest.mark.xfail(reason='CANARYreason')",
        "def test_CANARYname():", "    assert False",
    ),
    "writes-file": _src(
        "from pathlib import Path", ADD, "def test_CANARYname():",
        "    Path('tests/CANARYwritten.txt').write_text('x')", "    assert add(1, 1) == 3",
    ),
    "denied-path": _src(
        ADD, "def test_CANARYname():",
        "    open('/CANARYdenied/x', 'w')", "    assert add(1, 1) == 2",
    ),
    "raises": _src(ADD, "def test_CANARYname():", "    raise ValueError('CANARYexc')"),
}  # fmt: skip


def author_text(out) -> str:
    """Every field the author reads: the whole verify output but the receipt (``binding`` and
    ``replay_set`` carry the author's own verifier argv and the test paths by design)."""
    return json.dumps(
        {k: v for k, v in out.items() if k not in ("binding", "replay_set")}, sort_keys=True
    )


@pytest.mark.sandbox
@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("fixed", [False, True], ids=["bad-fix", "good-fix"])
def test_hidden_run_output_names_nothing(project, state, name, fixed):
    base, test_commit = make_test_commit(project, {F: SCENARIOS[name]})
    cand = candidate(
        project, base, {"src/calc.py": FIX if fixed else "def add(a, b):\n    return 0\n"}
    )
    verifier = f"python3 -m pytest -q {F}"
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(verifier,))
    text = author_text(out)
    leaked = [c for c in CAN if c in text]
    assert not leaked, (name, fixed, leaked, out.get("reason"), out.get("uncomparable"))


def test_not_run_and_not_targeted_refusals_count_only():
    from types import SimpleNamespace

    from cairnwork.pipeline_judge import _not_run_refusal

    node = f"{F}::test_CANARYname[CANARYreason]"
    files = SimpleNamespace(base_nodes={"v": (node,)})
    run = SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), outcomes={}, observation=SimpleNamespace(passed=True)
    )
    missing = _not_run_refusal([node], files, [run], (run,))
    assert missing == "DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION: no verifier collected or ran 1"
    gone = _not_run_refusal([], files, [run], (run,))
    assert gone.startswith("NAMED_TESTS_NOT_RUN: 1 test(s)") and "CANARY" not in gone
