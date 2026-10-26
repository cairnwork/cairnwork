"""#303 under the verifier account, where a blackbox check can pass: the same three candidates as
test_blackbox_verify.py, plus one that tries to read the owner's answers from the declaration file.

The correct fix is VERIFIED with its check passing; the wrong fix, the ``sys.modules`` swap and the
attacks on the run (verify_helpers.ATTACKS; pytest alone calls the swap, forge, finder and syspath
VERIFIED) fail on the check, so Core reports FAILED_VERIFICATION.
"""

import json
import subprocess

import pytest
from verify_helpers import ATTACKS, CANDIDATES, base_of, candidate, declaration, verify

from cairnwork.verifier_account import VerifierAccount

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]

READER = (
    "import json\n"
    "def add(a, b):\n"
    "    try:\n"
    "        with open({path!r}) as handle:\n"
    "            cases = json.load(handle)['checks'][0]['cases']\n"
    "        return next(c['expected'] for c in cases if c['args'] == [a, b])\n"
    "    except Exception:\n"
    "        return a + b + 1\n"
)


@pytest.fixture
def account_state(state):
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    return state


def test_only_the_correct_fix_passes_its_check(account_state, helper_project, tmp_path):
    declared = declaration(tmp_path)
    base = base_of(helper_project)
    sources = CANDIDATES | ATTACKS | {"reader": READER.format(path=str(declared))}
    out = {}
    for name, source in sources.items():
        sha = candidate(helper_project, base, {"src/calc.py": source})
        args = ("--import-path", "src", "--blackbox", str(declared))
        out[name] = verify(account_state, helper_project, base, sha, *args)[1]
    assert out["correct"]["status"] == "VERIFIED", out["correct"]["reason"]
    assert "blackbox:add: PASS [matched 2/2]" in out["correct"]["reason"]
    for name in ("wrong", "attack", "reader", "forge", "finder", "child", "syspath"):
        assert out[name]["status"] == "FAILED" and out[name]["core_status"] == "FAILED_VERIFICATION"
        want = "VERIFICATION_INCOMPLETE: SURVIVORS" if name == "child" else "MISMATCH [matched 0/2]"
        assert f"blackbox:add: {want}" in out[name]["reason"], (name, out[name]["reason"])
    for name in ("attack", "forge", "finder", "syspath"):  # pytest was fooled, the check was not
        assert "pytest-0" not in out[name]["reason"], out[name]


def test_the_account_cannot_read_the_declaration(tmp_path):
    declared = declaration(tmp_path)
    argv = VerifierAccount().wrap(["/bin/cat", str(declared)], None)
    done = subprocess.run(argv, capture_output=True, timeout=60, check=False)
    assert done.returncode != 0 and b"salt" not in done.stdout


def test_the_reader_does_read_it_when_allowed(tmp_path):
    """The reader candidate is a real attack: given the file, it returns the answers."""
    namespace: dict = {}
    exec(READER.format(path=str(declaration(tmp_path))), namespace)  # noqa: S102
    assert namespace["add"](2, 3) == 5
