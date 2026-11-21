import json

from cairnwork.reasons import why_not_passed
from cairnwork.report_guard import parse_verdict

NONCE = "nonce"


def _payload(**extra):
    return {
        "nonce": NONCE,
        "collected": ["tests/test_canary.py::test_failure"],
        "outcomes": {"tests/test_canary.py::test_failure": "failed"},
        "files": ["tests/test_canary.py"],
        "file_of": [0],
        "exitstatus": 1,
        "testsfailed": 1,
        **extra,
    }


def _reason(*, hidden=False, assertions=None, outcomes=None):
    return why_not_passed(
        ran_sandboxed=True,
        returncode=1,
        timed_out=False,
        mutated=(),
        survivors=0,
        log_too_big=False,
        tests_ran=False,
        outcomes=outcomes or _payload()["outcomes"],
        assertions=assertions or {},
        hidden_tests=hidden,
    )


def test_visible_failure_includes_node_and_bounded_assertion_without_verdict_change():
    node = "tests/test_canary.py::test_failure"
    reason = _reason(assertions={node: "expected a very long explanation" * 20})
    assert node in reason
    assert "expected a very long explanation" in reason
    assert len(reason.rsplit(": ", 1)[-1]) <= 240
    assert "pytest exit code 1" in reason


def test_hidden_failure_reason_contains_count_only_and_never_canary():
    canary = "SECRET_CANARY_1186"
    reason = why_not_passed(
        ran_sandboxed=True,
        returncode=1,
        timed_out=False,
        mutated=("tests/hidden.py",),
        survivors=0,
        log_too_big=False,
        tests_ran=False,
        outcomes={"tests/test_canary.py::test_failure": "failed"},
        assertions={"tests/test_canary.py::test_failure": canary},
        hidden_tests=True,
        fail_kind={"tests/test_canary.py::test_failure": "setup"},
        denied_path="hidden.py",
    )
    assert "deferred: 1 failed" in reason
    for text in (canary, "test_failure", "test_canary.py"):
        assert text not in reason


def test_visible_failure_names_are_bounded_with_more_count():
    outcomes = {f"tests/test_{index}.py::test_failure": "failed" for index in range(7)}
    reason = _reason(outcomes=outcomes, assertions={node: "assertion" for node in outcomes})
    assert "+2 more" in reason
    assert "tests/test_5.py" not in reason


def test_assertion_controls_ansi_and_workflow_commands_are_neutralized():
    node = "tests/test.py::test_one\n::warning::forged\x1b[31mred\x1b[0m\x01"
    assertion = "##[command] injected\n\x1b[31mred\x1b[0m\x01"
    reason = _reason(assertions={node: assertion}, outcomes={node: "failed"})
    assert "\x1b" not in reason and "\x01" not in reason
    assert not any(line.startswith(("::", "##[")) for line in reason.splitlines())
    assert "?##[command] injected" in reason


def test_report_parser_preserves_assertion_text_for_reason_layer():
    node = "tests/test_canary.py::test_failure"
    verdict = parse_verdict(
        json.dumps(_payload(assertions={node: "AssertionError: expected 2"})).encode(),
        NONCE,
    )
    assert verdict.recorded
    assert verdict.assertions == {node: "AssertionError: expected 2"}
