"""#909: the state and stream readers refuse a value nested too deep with their existing result
instead of raising ``RecursionError``: the state config (BAD_CONFIG; remember_gh leaves it alone),
an Issue record (ISSUE_RECORD_UNREADABLE) and a sandbox log stream line (UNAVAILABLE)."""

from __future__ import annotations

import pytest
from test_deny_channel import stream, window

from cairnwork import issue_runner as runner
from cairnwork.deny_channel import judge
from cairnwork.intake import Intake, IntakeRefused

DEPTH = 100_000  # past every recursion limit on 3.11-3.13 for the C json decoder
DEEP_TEXT = "[" * DEPTH + "]" * DEPTH


@pytest.fixture
def intake(tmp_path):
    made = Intake(tmp_path / "state")
    made.prepare()
    return made


def test_a_too_deep_config_is_bad_config(intake):
    (intake.state / "config.json").write_text(DEEP_TEXT)
    with pytest.raises(IntakeRefused) as caught:
        intake.config()
    assert caught.value.code == "BAD_CONFIG"


def test_remember_gh_leaves_a_too_deep_config_alone(intake):
    path = intake.state / "config.json"
    path.write_text(DEEP_TEXT)
    intake.remember_gh("/bin/sh")
    assert path.read_text() == DEEP_TEXT


def test_a_too_deep_issue_record_is_unreadable(intake):
    path = runner._record_path(intake, "James3014/demo", 7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEEP_TEXT)
    with pytest.raises(ValueError, match="ISSUE_RECORD_UNREADABLE"):
        runner._recall(intake, "James3014/demo", 7)


def test_a_too_deep_stream_line_makes_the_evidence_unavailable():
    deep = '{"timestamp": ' + DEEP_TEXT + ', "eventMessage": "x"}\n'
    evidence = judge(stream(deep), window())
    assert evidence.state == "UNAVAILABLE" and "unreadable stream line" in evidence.detail
