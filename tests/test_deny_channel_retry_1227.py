"""#1227: the retry in ``_caught_denial`` must not break on git. Stand-ins replace the macOS
sandbox runs, so this runs on any platform; the real git helpers (make_test_commit) are kept."""

from types import SimpleNamespace
from typing import Any

import pytest
import test_deny_channel_sandbox as deny
from conftest import commit_change
from test_deferred_tests import make_test_commit


def _install(monkeypatch, repo, outcomes):
    """Fake the two sandbox runs. The evidence run commits into the repo it is handed, like
    ``deny._run`` does; each attempt takes the next outcome (True = the denial was recorded)."""
    seen: list[Any] = []

    def run_cli(project, state, tests, files):
        make_test_commit(
            project, {"tests/test_regression.py": tests}
        )  # real git, as the real helper
        hit = outcomes[len(seen)]
        mark = ": DENIED: x" if hit else ": CLEAN: x"
        logs = ["base deny evidence 1" + mark, "second base deny evidence 2 -s/ x"]
        return 0, {"status": "VERIFIED", "logs": logs}

    def run(repo_arg, tmp_path, test, deny_flag=True, account=None):
        commit_change(repo_arg, {"tests/test_d.py": test})
        hit = outcomes[len(seen)]
        seen.append(repo_arg)
        denials = {deny.HIT} if hit else set()
        return SimpleNamespace(deny_evidence="DENIED: x", deny_denials=denials)

    monkeypatch.setattr(deny, "run_cli", run_cli)
    monkeypatch.setattr(deny, "_run", run)
    return seen


def test_a_missed_denial_is_retried_once_and_the_retry_can_pass(monkeypatch, repo, tmp_path):
    seen = _install(monkeypatch, repo, [False, True])
    deny._caught_denial(repo, tmp_path / "state", repo, tmp_path, deny.CAUGHT)
    assert len(seen) == 2


def test_a_second_miss_still_fails(monkeypatch, repo, tmp_path):
    seen = _install(monkeypatch, repo, [False, False])
    with pytest.raises(AssertionError):
        deny._caught_denial(repo, tmp_path / "state", repo, tmp_path, deny.CAUGHT)
    assert len(seen) == 2
