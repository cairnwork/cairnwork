"""#783 AC2: an interrupted ``cairnwork-verify run`` says whether the test commit is spent."""

import json

import pytest

from cairnwork import verify_cli
from cairnwork.deferred_store import consume

OID = "a" * 40
ARGS = ["--state", "{state}", "run", "--repo-path", "r", "--base", OID, "--candidate", OID,
        "--allowed-file", "f", "--verifier", "true"]  # fmt: skip


def _interrupt(*_a, **_k):
    raise KeyboardInterrupt


def _run(state, capsys, extra):
    argv = [a.format(state=state) for a in ARGS] + extra
    rc = verify_cli.main(argv)
    return rc, json.loads(capsys.readouterr().out)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_cli, "_run", _interrupt)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    return state


def test_interrupt_after_the_burn_says_the_test_commit_is_consumed(state, capsys):
    consume(state / "verify-used-tests", OID, record=True)
    rc, out = _run(state, capsys, ["--test-commit", OID])
    assert rc == 130 and out["error"] == "INTERRUPTED" and out["tests_consumed"] is True
    assert "new one" in out["detail"] and "TEST_COMMIT_ALREADY_EXPOSED" in out["detail"]


def test_interrupt_before_the_burn_does_not_claim_consumption(state, capsys):
    rc, out = _run(state, capsys, ["--test-commit", OID])
    assert rc == 130 and out["tests_consumed"] is False and "consumed" not in out["detail"]
