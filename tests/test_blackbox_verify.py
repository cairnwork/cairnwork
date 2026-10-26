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

import cairnwork_sandbox


def run_all(state, project, *extra):
    base = base_of(project)
    out = {}
    for name, source in CANDIDATES.items():
        sha = candidate(project, base, {"src/calc.py": source})
        out[name] = verify(state, project, base, sha, "--import-path", "src", *extra)[1]
    return out


@pytest.mark.sandbox
def test_without_a_declaration_the_swap_is_a_false_green(state, helper_project):
    """The gap #303 is about, pinned: undeclared pytest tests stay unprotected."""
    out = run_all(state, helper_project)
    assert out["correct"]["status"] == "VERIFIED"
    assert out["wrong"]["status"] == "FAILED"
    assert out["attack"]["status"] == "VERIFIED"


@pytest.mark.sandbox
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
    "syspath": f"{NEEDS_ACCOUNT} [matched 0/2]",
}


@pytest.mark.sandbox
@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_attacks_on_the_run_are_never_verified(state, helper_project, tmp_path, name):
    base = base_of(helper_project)
    sha = candidate(helper_project, base, {"src/calc.py": ATTACKS[name]})
    declared = str(declaration(tmp_path))
    out = verify(state, helper_project, base, sha, "--import-path", "src", "--blackbox", declared)[
        1
    ]
    assert out["status"] == "FAILED" and out["core_status"] == "FAILED_VERIFICATION", out
    backend = cairnwork_sandbox.platform_backend()
    if name == "child" and not (backend is not None and backend.name == "seatbelt"):
        # bwrap kills it with the PID namespace (test_blackbox_account.py): right, never passed
        assert "SURVIVORS" in out["reason"] or f"{NEEDS_ACCOUNT} [matched 2/2]" in out["reason"]
        return
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


def test_an_empty_blackbox_path_is_refused_not_ignored(state, helper_project):
    base = base_of(helper_project)
    sha = candidate(helper_project, base, {"src/calc.py": CANDIDATES["correct"]})
    code, out = verify(state, helper_project, base, sha, "--blackbox", "")
    assert code == 2 and out["error"] == "BAD_BLACKBOX", out


def test_the_receiver_gets_the_commitment_from_its_own_copy(state, tmp_path):
    from verify_helpers import cli

    from cairnwork import blackbox

    declared = declaration(tmp_path)
    code, out = cli(state, "blackbox-commitment", "--blackbox", str(declared))
    assert code == 0 and out == {"blackbox_commitment": blackbox.load(declared).commitment()}


@pytest.mark.sandbox
def test_methods_raises_and_floats_run_through_the_sandbox(state, helper_project, tmp_path):
    """The new kinds through cairnwork-verify's real run (as the owner, so never a pass)."""
    import json
    import secrets

    from test_blackbox_kinds import ACCOUNT, ACCOUNT_CHECK, MEAN, MEAN_CHECK

    path = tmp_path / "kinds.json"
    path.write_text(
        json.dumps({"salt": secrets.token_hex(32), "checks": [ACCOUNT_CHECK, MEAN_CHECK]})
    )
    path.chmod(0o600)
    wrong = ACCOUNT.replace("self.balance += amount", "self.balance = amount") + MEAN.replace(
        "return scale * sum(values) / len(values)",
        "return round(scale * sum(values) / len(values), 3)",
    )
    base = base_of(helper_project)
    for source, acct, mean in ((ACCOUNT + MEAN, 1, 3), (wrong, 0, 2)):
        sha = candidate(helper_project, base, {"src/calc.py": source + CANDIDATES["correct"]})
        out = verify(
            state, helper_project, base, sha, "--import-path", "src", "--blackbox", str(path)
        )[1]
        assert out["status"] == "FAILED" and not out["verified"], out
        for check, matched, total in (("acct", acct, 1), ("mean", mean, 3)):
            want = f"blackbox:{check}: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT"
            assert f"{want} [matched {matched}/{total}]" in out["reason"], out["reason"]
