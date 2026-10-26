"""#303 end to end on cairnwork-verify: a candidate that swaps what a protected test reads.

The project's protected test compares ``add(2, 3)`` with ``EXPECTED`` from tests/helper.py. The
attack candidate leaves ``add`` wrong and, when calc is imported, puts a fake ``helper`` into
``sys.modules`` whose ``EXPECTED`` matches the wrong answer: pytest alone calls it VERIFIED. A
blackbox declaration holds the answers outside the run, so the same candidate no longer passes.
These run as the owner (no verifier account): there a declared check never passes (the candidate
could read the answers), but the log line still says how many cases matched. The account runs,
where the correct fix is VERIFIED, are in test_blackbox_account.py.
"""

import pytest
from verify_helpers import ATTACKS, CANDIDATES, base_of, candidate, declaration, verify


def run_all(state, project, *extra):
    base = base_of(project)
    out = {}
    for name, source in CANDIDATES.items():
        sha = candidate(project, base, {"src/calc.py": source})
        out[name] = verify(state, project, base, sha, "--import-path", "src", *extra)[1]
    return out


def test_without_a_declaration_the_swap_is_a_false_green(state, helper_project):
    """The gap #303 is about, pinned: undeclared pytest tests stay unprotected."""
    out = run_all(state, helper_project)
    assert out["correct"]["status"] == "VERIFIED"
    assert out["wrong"]["status"] == "FAILED"
    assert out["attack"]["status"] == "VERIFIED"


def test_declared_checks_never_pass_as_the_owner(state, helper_project, tmp_path):
    out = run_all(state, helper_project, "--blackbox", str(declaration(tmp_path)))
    for name, matched in (("correct", 2), ("wrong", 0), ("attack", 0)):
        assert out[name]["status"] == "FAILED" and not out[name]["verified"], name
        assert (
            "blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT"
            f" [matched {matched}/2]" in out[name]["reason"]
        ), out[name]["reason"]


NEEDS_ACCOUNT = "blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT"
OWNER_ATTACKS = {
    "forge": f"{NEEDS_ACCOUNT} [matched 0/2]",
    "finder": f"{NEEDS_ACCOUNT} [matched 0/2]",
    "child": "blackbox:add: VERIFICATION_INCOMPLETE: SURVIVORS]",
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attacks_on_the_run_are_never_verified(state, helper_project, tmp_path, name):
    base = base_of(helper_project)
    sha = candidate(helper_project, base, {"src/calc.py": ATTACKS[name]})
    declared = str(declaration(tmp_path))
    out = verify(state, helper_project, base, sha, "--import-path", "src", "--blackbox", declared)[
        1
    ]
    assert out["status"] == "FAILED" and out["core_status"] == "FAILED_VERIFICATION", out
    assert OWNER_ATTACKS[name] in out["reason"], out["reason"]
    if name != "child":  # pytest alone was fooled; the blackbox check was not
        assert "pytest-0" not in out["reason"]


def test_a_declaration_others_can_read_is_refused_before_anything_runs(
    state, helper_project, tmp_path
):
    base = base_of(helper_project)
    sha = candidate(helper_project, base, {"src/calc.py": CANDIDATES["correct"]})
    declared = declaration(tmp_path)
    declared.chmod(0o644)
    code, out = verify(state, helper_project, base, sha, "--blackbox", str(declared))
    assert code == 2 and out["error"] == "BAD_BLACKBOX" and "salt" not in out["detail"]
    assert not (state / "ownership.sqlite").exists()  # prepare() never ran
