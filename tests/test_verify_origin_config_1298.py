"""The verify CLI takes the deferred tests' origin only from the verifier's state config."""

import json

import pytest
import verify_helpers
from conftest import git
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from verify_helpers import FIX, candidate, make_mirror, verify


@pytest.mark.sandbox
@pytest.mark.parametrize("configured", [False, True])
def test_the_verify_cli_needs_origin_url_in_state_config(
    project, state, tmp_path, monkeypatch, configured
):
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    config = {"verifier_account": False, "mirror": str(make_mirror(project, tmp_path))}
    if configured:
        config["origin_url"] = git(project, "remote", "get-url", "origin")
    (state / "config.json").write_text(json.dumps(config))
    cand = candidate(project, base, {"src/calc.py": FIX})
    monkeypatch.setattr(verify_helpers, "configure_origin", lambda *_: None)
    code, out = verify(
        state, project, base, cand, "--test-commit", test_commit, tests=(" ".join(CHECK[0][1]),)
    )
    if configured:
        assert code == 0 and out["status"] == "VERIFIED", json.dumps(out)
    else:  # the repo's own remote is not an origin source
        assert code != 0 and out["status"] in ("INFRA_ERROR", "FAILED"), json.dumps(out)
        assert "no verifier-owned origin" in json.dumps(out)
