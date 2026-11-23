"""#1688: a rejected test commit (non-public tests) never puts test-commit text (a path, a file
name, a message, a git error) into anything the author reads; the reason is the code plus a
count. Categories end to end through the real verify CLI: scope refusal, unparsable test,
conftest check. Overlay refusals are not reachable once the scope checks passed (the overlay is
laid over paths already validated), so they are pinned through ``author_reason`` and the sinks."""

import json

import pytest
from test_deferred_tests import make_test_commit
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_overlay import TestCommitRejected, author_reason

MARK = "CANARY1688"
SCENARIOS = {
    "scope": ({f"tests/{MARK}dir/setup.cfg": "[x]\n"}, "TEST_COMMIT_SCOPE"),
    "unparsable": ({f"tests/test_{MARK}.py": f"def {MARK}(:\n"}, "TEST_COMMIT_UNPARSABLE"),
    "conftest": (
        {
            f"tests/test_{MARK}.py": "def test_x():\n    assert True\n",
            "tests/conftest.py": f"import os\nos.environ['{MARK}'] = 'x'\ndef {MARK}(): pass\n",
        },
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
}


def author_text(out) -> str:
    return json.dumps(
        {k: v for k, v in out.items() if k not in ("binding", "replay_set")}, sort_keys=True
    )


@pytest.mark.sandbox
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_rejected_test_commit_names_nothing(project, state, name):
    files, code = SCENARIOS[name]
    base, test_commit = make_test_commit(project, files)
    cand = candidate(project, base, {"src/calc.py": FIX})
    status, out = verify(state, project, base, cand, "--test-commit", test_commit)
    assert out["verified"] is False and status != 0, out
    assert out["reason"].startswith(code), (name, out["reason"])
    assert MARK not in author_text(out), (name, out["reason"])
    assert out["reason"] == f"{code}: 1 refusal (detail withheld)", out["reason"]


@pytest.mark.parametrize(
    "code", ["OVERLAY_UNSUPPORTED_ENTRY", "TEST_OVERLAY_FAILED", "TEST_COMMIT_SCOPE"]
)
def test_author_reason_withholds_detail_only_when_hidden(code):
    exc = TestCommitRejected(code, f"{MARK} path / git error")
    assert author_reason(exc, True) == f"{code}: 1 refusal (detail withheld)"
    assert MARK not in author_reason(exc, True)
    assert author_reason(exc, False) == str(exc)  # visible runs byte for byte


def test_author_reason_leaves_other_text_alone():
    bare = TestCommitRejected("TEST_COMMIT_EMPTY")
    assert author_reason(bare, True) == str(bare) == "TEST_COMMIT_EMPTY"
    other = ValueError(f"{MARK} stays")
    assert author_reason(other, True) == str(other)
