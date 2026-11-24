"""#1677: a credential-mask change (``MaskChanged``) raised from the verifier stage reaches the two
outermost catch-alls through the real entries. The pipeline records FAILED with an INFRA_ERROR
reason (the run is not counted, nothing UNCOMPARABLE); ``cairnwork-verify run`` ends with INTERNAL,
exit 1, and says the test commit is used up. Never a pass, never exit 0, never a receipt."""
# ruff: noqa: F811 - the stub fixture is imported from test_uncomparable_paths

import json

from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_pipeline import EditWorker
from test_uncomparable_paths import TEETH, run_worker, stub  # noqa: F401
from verify_helpers import FIX, candidate

from cairnwork import pipeline_judge, verify_cli
from cairnwork.journal import Journal, no_evidence
from cairnwork.mask_recheck import MaskChanged

PASSING_P2P = {"rc": 0, "outcomes": {"tests/test_calc.py::test_add": "passed"}}
MESSAGE = "the credential masks did not hold"


def _candidate_run_breaks(monkeypatch):
    """The candidate's own verifier run (id ``…-v``) ends with the guard's MaskChanged."""
    staged = pipeline_judge.run_verifiers

    def run(ctx, commit, specs, run_id, **kw):
        if run_id.endswith("-v"):
            raise MaskChanged(MESSAGE)
        return staged(ctx, commit, specs, run_id, **kw)

    monkeypatch.setattr(pipeline_judge, "run_verifiers", run)


def test_pipeline_a_mask_change_is_infra_error_and_never_counted(
    project, tmp_path, stub, monkeypatch
):
    stub(TEETH, PASSING_P2P)
    _candidate_run_breaks(monkeypatch)
    result = run_worker(project, tmp_path, EditWorker({"src/calc.py": FIX}))
    assert result.status == "FAILED" and result.verified is False, result
    assert result.reason.startswith("INFRA_ERROR: MaskChanged: "), result.reason
    assert "CREDENTIAL_MASK_CHANGED" in result.reason and MESSAGE in result.reason
    assert result.uncomparable == ""  # not evidence either way, and not a counted run
    entries = Journal(tmp_path / "wstate" / "journal.sqlite").entries(result.task_id)
    assert no_evidence(entries) == ""


def test_verify_cli_a_mask_change_is_internal_exit_1(project, state, stub, monkeypatch, capsys):
    stub(TEETH, PASSING_P2P)
    _candidate_run_breaks(monkeypatch)
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    cand = candidate(project, base, {"src/calc.py": FIX})
    code = verify_cli.main([
        "--state", str(state), "run", "--repo-path", str(project), "--base", base,
        "--candidate", cand, "--allowed-file", "src/calc.py",
        "--verifier", " ".join(CHECK[0][1]), "--test-commit", test_commit,
    ])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"] == "INTERNAL"
    assert out["detail"].startswith("MaskChanged: "), out["detail"]
    assert "CREDENTIAL_MASK_CHANGED" in out["detail"] and MESSAGE in out["detail"]
    assert out["tests_consumed"] is True and "verified" not in out
