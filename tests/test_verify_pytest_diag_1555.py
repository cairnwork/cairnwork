"""Issue #1555: verify returns pytest_exit_code / pytest_output (record-only, masked, bounded)."""

import json
from types import SimpleNamespace

import pytest
from verify_helpers import base_of, candidate, verify

from cairnwork import failure_evidence
from cairnwork.core_request import VerifierObservation

MARK = "UNIQUE-MARK-1555-zq7"
LEAKY = f"def add(a, b):\n    print({MARK!r})\n    return 0\n"


def _run(path, code=1, passed=False):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="pytest-0", targets=("tests/test_x.py",)),
        outcomes={"tests/test_x.py::t": "failed"}, collect_errors=(), log_path=path,
        returncode=code, observation=VerifierObservation("pytest-0", "a", "sha256:f", passed),
    )  # fmt: skip


@pytest.mark.sandbox
def test_a_real_failing_verify_reports_the_exit_code_and_leaks_no_log(state, project):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": LEAKY})
    _, out = verify(state, project, base, sha, "--import-path", "src")
    assert out["status"] == "FAILED"
    assert out["pytest_exit_code"] == 1
    assert out["pytest_output"] is None  # no dedicated account: the tail is withheld
    assert MARK not in json.dumps(out)  # the candidate's own output reaches no field


@pytest.mark.sandbox
def test_a_pass_has_the_keys_and_no_exit_code(state, project):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    _, out = verify(state, project, base, sha, "--import-path", "src")
    assert out["status"] == "VERIFIED"
    assert (out["pytest_exit_code"], out["pytest_output"]) == (None, None)


def test_with_the_account_the_masked_tail_is_the_output(tmp_path):
    log = tmp_path / "v.log"
    lines = [f"line {n}" for n in range(300)] + ["token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"]
    log.write_text("\n".join(lines) + "\n")
    entries = failure_evidence.capture([_run(log, 1)], home="", account=True)
    got = failure_evidence.headline(entries)
    assert got["pytest_exit_code"] == 1
    assert len(got["pytest_output"].splitlines()) <= failure_evidence.OUT_LINES
    assert len(got["pytest_output"]) <= failure_evidence.OUT_CHARS
    assert got["pytest_output"].splitlines()[-2] == "line 299"  # the END of the log
    assert "ghp_abcdefghij" not in got["pytest_output"]


def test_withheld_tail_and_deferred_tests_give_no_output(tmp_path):
    log = tmp_path / "v.log"
    log.write_text(MARK + "\n")
    for kwargs in ({"account": False}, {"account": True, "hidden": True}):
        entries = failure_evidence.capture([_run(log, 2)], home="", **kwargs)
        assert failure_evidence.headline(entries) == {"pytest_exit_code": 2, "pytest_output": None}
        assert MARK not in repr(entries)


@pytest.mark.parametrize("bad", [True, None, "1", 1.0])
def test_only_a_real_int_is_an_exit_code(tmp_path, bad):
    entries = failure_evidence.capture([_run(tmp_path / "none.log", bad)], home="")
    assert failure_evidence.headline(entries)["pytest_exit_code"] is None


def test_the_first_entry_with_a_code_wins_and_garbage_is_ignored():
    entries = [{"returncode": None}, {"returncode": 5, "log_tail": "x", "log_tail_withheld": True}]
    assert failure_evidence.headline(entries) == {"pytest_exit_code": 5, "pytest_output": None}
    assert failure_evidence.headline([None, 3]) == {"pytest_exit_code": None, "pytest_output": None}
    assert failure_evidence.headline([]) == {"pytest_exit_code": None, "pytest_output": None}


def _entry(code, tail, withheld=False):
    return {"returncode": code, "log_tail": tail, "log_tail_withheld": withheld}


def test_the_first_of_two_entries_with_codes_wins():
    entries = [_entry(3, "first"), _entry(4, "second")]
    assert failure_evidence.headline(entries) == {"pytest_exit_code": 3, "pytest_output": "first"}


@pytest.mark.parametrize(("width", "count"), [(300, 60), (512, 61)])
def test_a_long_tail_is_cut_to_the_char_cap(width, count):
    lines = [f"{n:03d}" + "x" * (width - 3) for n in range(count)]
    got = failure_evidence.headline([_entry(1, "\n".join(lines))])["pytest_output"]
    kept = "\n".join(lines[-failure_evidence.OUT_LINES :])
    assert len(got) == failure_evidence.OUT_CHARS == 4096
    assert got == kept[-4096:]  # the end of the last 60 lines, not the head


def test_a_short_tail_comes_back_unchanged():
    assert failure_evidence.headline([_entry(1, "a\nb")])["pytest_output"] == "a\nb"
