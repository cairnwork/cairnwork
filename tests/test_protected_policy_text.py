"""#963 step 1: a protected-path refusal says it is Cairnwork policy and names the rule.

Codes and statuses are unchanged (test_protected_paths.py pins them); only the detail text is
new, and only for a path on the list."""

import json
import sys
from pathlib import Path

import pytest
from test_protected_paths import VERIFY, _attempt, _commit

from cairnwork.issue_task import IssueRefused, parse_task_block
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.protected_paths import path_refusal, protected_note, protected_rule
from cairnwork.workspace import CandidateRejected, collect_candidate

POLICY = "protected by Cairnwork policy"
RULE = "nexus/services/external_intelligence*"
PATH = "nexus/services/external_intelligence.py"


def _issue(path):
    block = json.dumps(
        {
            "difficulty": "easy",
            "allowed_files": [path],
            "verification": ["python3 -m pytest tests/test_calc.py"],
            "capability_ids": ["c1"],
        }
    )
    with pytest.raises(IssueRefused) as info:
        parse_task_block(f"```eia-task\n{block}\n```")
    return info.value


def _submit(path):
    task = TaskSpec("k", "card", "t", "d", Path("."), "a" * 40, (path,), VERIFY)
    with pytest.raises(TaskRejected) as info:
        check_task(task)
    return info.value


def test_the_rule_is_the_pattern_as_written_in_the_list():
    assert protected_rule(PATH) == RULE
    assert protected_rule("Tests/conftest.py") == "conftest.py"
    assert protected_rule(".github/workflows/x.yml") == ".github"
    assert protected_rule("src/calc.py") is None and protected_note("src/calc.py") == ""


def test_path_refusal_says_policy_and_names_the_rule():
    code, detail = path_refusal(PATH)  # type: ignore[misc]
    assert code == "TASK_ALLOWED_FILES_PROTECTED"
    assert POLICY in detail and repr(RULE) in detail and PATH in detail


def test_path_refusal_of_a_path_that_is_not_protected_has_no_policy_text():
    code, detail = path_refusal("src/.env")  # type: ignore[misc]
    assert code == "TASK_ALLOWED_FILES_INVALID" and POLICY not in detail
    assert path_refusal("src/calc.py") is None


def test_issue_intake_refusal_names_the_rule_and_keeps_its_code():
    err = _issue(PATH)
    assert err.code == "TASK_ALLOWED_FILES_PROTECTED"
    assert POLICY in str(err) and repr(RULE) in str(err)


def test_issue_intake_unprotected_refusal_has_no_policy_text():
    err = _issue("src/.env")
    assert err.code == "TASK_ALLOWED_FILES_INVALID" and POLICY not in str(err)


def test_submit_refusal_names_the_rule_and_keeps_its_code():
    err = _submit(PATH)
    assert err.code == "TASK_ALLOWED_FILES_PROTECTED"
    assert POLICY in str(err) and repr(RULE) in str(err)


def test_submit_unprotected_refusal_has_no_policy_text():
    err = _submit("src/.env")
    assert err.code == "BAD_SCOPE" and POLICY not in str(err)


def test_candidate_check_refusal_names_the_rule_and_keeps_its_code(repo, tmp_path):
    attempt, clone = _attempt(repo, tmp_path)
    (clone / "scripts").mkdir(exist_ok=True)
    (clone / "scripts" / "check_size.py").write_text("LIMIT = 10**9\n")
    _commit(clone)
    with pytest.raises(CandidateRejected) as info:
        collect_candidate(repo, attempt, ("scripts/check_size.py",), python=sys.executable)
    assert info.value.code == "PROTECTED_PATH"
    assert POLICY in str(info.value) and "scripts/check_size.py" in str(info.value)


def test_candidate_check_out_of_scope_has_no_policy_text(repo, tmp_path):
    attempt, clone = _attempt(repo, tmp_path)
    (clone / "src" / "a.py").write_text("x = 2\n")
    (clone / "src" / "b.py").write_text("y = 2\n")
    _commit(clone)
    with pytest.raises(CandidateRejected) as info:
        collect_candidate(repo, attempt, ("src/a.py",), python=sys.executable)
    assert info.value.code == "OUT_OF_SCOPE" and POLICY not in str(info.value)


def test_the_note_cannot_carry_a_ci_log_command():
    """A candidate file name is untrusted: a newline plus ``::warning::`` must not reach a log."""
    note = protected_note("nexus/services/external_intelligence\n::warning::x##[group]y.py")
    assert "\n" not in note and "::" not in note and "##[" not in note
    assert POLICY in note and repr(RULE) in note
