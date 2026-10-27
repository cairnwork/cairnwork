"""A state dir or run root under a path the verifier sandbox masks is refused before the
deferred tests are burned (#324): inside the run that directory is empty, so pytest fails at
getcwd and every retry needed a new test commit."""

import pytest
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_deferred_tests import project as project  # noqa: F401 - fixture
from verify_helpers import FIX, candidate, verify

from cairnwork.intake import Intake, IntakeRefused
from cairnwork.verify_cli import _require_unmasked_state

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


def test_account_mode_does_not_refuse_the_same_path(state, home, monkeypatch):
    # With a verifier account the run is another user: the owner's home is not masked for it.
    state = _move(state, home / ".local" / "state" / "cairnwork")
    intake = Intake(state)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: object())
    _require_unmasked_state(intake)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: None)
    with pytest.raises(IntakeRefused) as err:
        _require_unmasked_state(intake)
    assert err.value.code == "STATE_UNDER_SANDBOX_MASK"
