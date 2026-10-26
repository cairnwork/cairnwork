"""#303 strict mode under the verifier account, where a blackbox check can really pass.

The in-process tests (test_protection.py) replace the blackbox run with a fixed verdict. Here the
check runs for real: the correct fix with ``--require-protection`` is VERIFIED and labelled
``partial``, and the wrong fix is not. Linux CI's test-linux-verifier-account job runs it.
"""

import json

import pytest
from verify_helpers import CANDIDATES, base_of, candidate, declaration, verify

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]


@pytest.fixture
def account_state(state):
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    return state


def test_a_protected_fix_passes_strict_mode(account_state, helper_project, tmp_path):
    declared = str(declaration(tmp_path))
    base = base_of(helper_project)
    args = ("--import-path", "src", "--blackbox", declared, "--require-protection")
    out = {}
    for name in ("correct", "wrong"):
        sha = candidate(helper_project, base, {"src/calc.py": CANDIDATES[name]})
        out[name] = verify(account_state, helper_project, base, sha, *args)[1]
    assert out["correct"]["status"] == "VERIFIED", out["correct"]["reason"]
    assert out["correct"]["protection"] == "partial"
    assert "[protection: PASS [partial]]" in out["correct"]["reason"]
    assert out["wrong"]["core_status"] == "FAILED_VERIFICATION", out["wrong"]
    assert "blackbox:add: MISMATCH" in out["wrong"]["reason"]
