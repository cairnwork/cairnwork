"""#995 step 1: a recorded stream only ever proves a denial, never its absence (AC1-AC3)."""

import pytest
from test_deny_channel import C1, line, stream, window

from cairnwork import deny_channel
from cairnwork.deny_channel import Evidence, judge

TARGET = ("file-read-data", "/private/etc/protocols")
OTHER_TARGET = "Sandbox: python3.13(7) deny(1) file-read-data /private/etc/services"
OWN = "Sandbox: python3.13(7) deny(1) file-read-data /private/etc/protocols"


def test_a_recorded_denial_of_the_target_is_denied():
    assert judge(stream(line(OWN)), window()).denial_of(*TARGET) == "DENIED"


def test_a_clean_window_never_says_not_denied():
    evidence = judge(stream(), window())
    assert evidence.state == "CLEAN"
    assert evidence.denial_of(*TARGET) == "UNKNOWN"
    assert not evidence.denied(*TARGET)


def test_a_denied_window_without_the_target_is_unknown_not_clear():
    # the run's own line for the target is missing, another denial of the run is there (low volume)
    evidence = judge(stream(line(OTHER_TARGET)), window())
    assert evidence.state == "DENIED" and evidence.peak_per_second < 10
    assert not evidence.denied(*TARGET)
    assert evidence.denial_of(*TARGET) == "UNKNOWN"


@pytest.mark.parametrize("state", ["UNAVAILABLE", "CLEAN", "DENIED"])
def test_no_state_of_an_empty_evidence_answers_not_denied(state):
    assert Evidence(state).denial_of(*TARGET) == "UNKNOWN"


def test_the_answer_is_one_of_two_words_and_the_summary_is_unchanged():
    answers = {judge(stream(line(OWN)), window()).denial_of(*t) for t in (TARGET, C1)}
    assert answers == {"DENIED", "UNKNOWN"}
    assert str(judge(stream(line(OWN)), window())).startswith("DENIED: file-read-data /private/")
    assert deny_channel.Evidence is Evidence


def test_a_denial_beyond_the_summary_limit_is_still_denied():
    many = [
        line(f"Sandbox: python3.13(7) deny(1) file-read-data /noise/{n}")
        for n in range(deny_channel.MAX_LISTED + 2)
    ]
    evidence = judge(stream(*many), window())
    last = ("file-read-data", f"/noise/{deny_channel.MAX_LISTED + 1}")
    assert "/noise/9" not in evidence.detail  # the summary names only MAX_LISTED of them
    assert evidence.denial_of(*last) == "DENIED"


def test_the_match_is_exact_so_another_case_or_path_is_unknown():
    evidence = judge(stream(line(OWN)), window())
    assert evidence.denial_of("FILE-READ-DATA", TARGET[1]) == "UNKNOWN"
    assert evidence.denial_of(TARGET[0], TARGET[1].upper()) == "UNKNOWN"
    assert evidence.denial_of(TARGET[0], TARGET[1] + "/") == "UNKNOWN"
