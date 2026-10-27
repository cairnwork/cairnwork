"""A state dir or run root under a path the verifier sandbox masks is refused before the
deferred tests are burned (#324): inside the run that directory is empty, so pytest fails at
getcwd and every retry needed a new test commit."""

import json

import pytest
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_deferred_tests import project as project  # noqa: F401 - fixture
from verify_helpers import FIX, candidate, verify

import cairnwork_sandbox
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.verify_cli import _require_unmasked_state
from cairnwork_sandbox import bwrap, seatbelt

TESTS = {"tests/test_regression.py": REGRESSION}
VERIFIER = " ".join(CHECK[0][1])


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = (tmp_path / "home").resolve()
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))  # the CLI subprocess inherits it
    return path


def _move(state, to):
    to.parent.mkdir(parents=True, exist_ok=True)
    state.rename(to)
    return to


def test_a_state_under_a_masked_home_path_is_refused_before_burning(project, state, home):  # noqa: F811
    base, sha = make_test_commit(project, TESTS)
    state = _move(state, home / ".local" / "state" / "cairnwork")
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    assert code == 2, out
    assert out["error"] == "STATE_UNDER_SANDBOX_MASK"
    assert str(state) in out["detail"]
    assert str(home / ".local" / "state") in out["detail"]
    assert not (state / "verify-used-tests" / sha).exists()  # not burned


def test_a_run_root_linked_into_a_masked_path_is_refused_before_burning(project, state, home):  # noqa: F811
    base, sha = make_test_commit(project, TESTS)
    masked = home / ".config" / "runs"
    masked.mkdir(parents=True)
    (state / "runs").symlink_to(masked)
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    assert code == 2, out
    assert out["error"] == "STATE_UNDER_SANDBOX_MASK"
    assert str(home / ".config") in out["detail"]
    assert not (state / "verify-used-tests" / sha).exists()  # not burned


@pytest.mark.sandbox
def test_a_state_outside_the_masked_paths_still_runs(project, state, home):  # noqa: F811
    base, sha = make_test_commit(project, TESTS)
    state = _move(state, home / "work" / "cairnwork")
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    assert code == 0, out
    assert out["tests_consumed"] is True


def test_a_masked_path_spelled_in_another_case_is_refused(state, home):
    (home / ".local" / "state").mkdir(parents=True)
    if not (home / ".LOCAL" / "STATE").exists():
        pytest.skip("case-sensitive filesystem: another spelling is another directory")
    state = _move(state, home / ".LOCAL" / "STATE" / "cairnwork")
    with pytest.raises(IntakeRefused) as err:
        _require_unmasked_state(Intake(state))
    assert err.value.code == "STATE_UNDER_SANDBOX_MASK"


def test_account_mode_does_not_refuse_the_same_path(state, home):
    # With a verifier account the runs live under the account's own run root, not the state.
    state = _move(state, home / ".local" / "state" / "cairnwork")
    intake = Intake(state)
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    _require_unmasked_state(intake)
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))
    with pytest.raises(IntakeRefused) as err:
        _require_unmasked_state(intake)
    assert err.value.code == "STATE_UNDER_SANDBOX_MASK"


@pytest.mark.parametrize(("backend", "refused"), [(bwrap.BACKEND, True), (seatbelt.BACKEND, False)])
def test_a_symlinked_masked_dir_masks_its_target_on_bwrap_only(
    state, home, monkeypatch, backend, refused
):
    # #339: bwrap's --tmpfs on a relative symlink mounts over its target (an absolute one fails
    # the mount, so the state cannot work there either); Seatbelt denies the link's own path.
    target = home / "dotfiles" / "config"
    target.mkdir(parents=True)
    (home / ".config").symlink_to("dotfiles/config")  # relative, as bwrap masks its target
    state = _move(state, target / "cairnwork")
    monkeypatch.setattr(cairnwork_sandbox, "platform_backend", lambda: backend)
    if not refused:
        _require_unmasked_state(Intake(state))
        return
    with pytest.raises(IntakeRefused) as err:
        _require_unmasked_state(Intake(state))
    assert err.value.code == "STATE_UNDER_SANDBOX_MASK"
    assert str(home / ".config") in err.value.detail
