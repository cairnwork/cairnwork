"""A task with no verifiers is refused at intake (#639).

Core cannot verify a task whose required-verifier list is empty, so such a task could only end
FAILED after its worker ran. Submit and check both go through task_from_payload and refuse it
before anything is bound."""

import pytest
from test_intake import FakePopen, payload
from test_intake import intake as intake  # noqa: F401 - fixture
from test_intake import repo as repo  # noqa: F401 - fixture

from cairnwork.intake import IntakeRefused, task_from_payload


def test_task_from_payload_refuses_an_empty_verifier_list(repo):  # noqa: F811
    with pytest.raises(IntakeRefused, match="BAD_REQUEST.*at least one verifier"):
        task_from_payload({**payload(repo), "verifiers": []})


def test_submit_with_no_verifiers_binds_nothing(intake, repo):  # noqa: F811
    with pytest.raises(IntakeRefused, match="BAD_REQUEST"):
        intake.submit({**payload(repo), "verifiers": []})
    assert intake.in_flight()["count"] == 0 and FakePopen.held == []
    assert not list((intake.state / "requests").glob("*.json"))


def test_one_verifier_is_still_accepted(repo):  # noqa: F811
    assert len(task_from_payload(payload(repo)).verifiers) == 1
