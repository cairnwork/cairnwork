"""#962 step 2b end to end: verify as the isolated account, real sandbox, real control run.
A protected fixture writes a fixed path the sandbox denies on every tree (#774's shape)."""

import json

import pytest
from conftest import commit_change
from verify_helpers import verify

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]
DENIED = "/private/tmp/cw962-account-denied"
PATH = (
    "import pathlib, sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
LITERAL = f"import pytest\n\n\n@pytest.fixture\ndef held():\n    open({DENIED!r}, 'w')\n"
VIA_APP = "import pytest\nimport app\n\n\n@pytest.fixture\ndef held():\n    app.prepare()\n"
TEST = "import app\n\n\ndef test_a(held):\n    assert app.work() == 2\n"
APP = f"def prepare():\n    open({DENIED!r}, 'w')\n\n\ndef work():\n    return {{}}\n"
RUN = ("python3 -m pytest -q tests/test_a.py",)


@pytest.fixture
def account_state(state):
    (state / "config.json").write_text(json.dumps({"verifier_account": True}))
    return state


def _verify(state, repo, conftest):
    base = commit_change(repo, {"src/app.py": APP.format(1), "tests/conftest.py": PATH + conftest,
                                "tests/test_a.py": TEST})  # fmt: skip
    fixed = commit_change(repo, {"src/app.py": APP.format(2)})
    return verify(state, repo, base, fixed, allowed=("src/app.py",), tests=RUN)[1]


def test_ac1_a_literal_fixture_write_denied_on_both_trees_is_uncomparable(account_state, repo):
    result = _verify(account_state, repo, LITERAL)
    assert result["status"] == "UNCOMPARABLE", result
    assert result["verified"] is False and result["core_status"] == ""
    assert result["uncomparable"]["code"] == "CANDIDATE_ENV_DENIED"
    assert result["uncomparable"]["rule"] == "candidate-env-v1"


def test_ac2_the_write_made_by_an_allowed_file_stays_failed(account_state, repo):
    result = _verify(account_state, repo, VIA_APP)
    assert result["status"] == "FAILED", result
    assert result["uncomparable"] is None


def test_ac11_owner_mode_has_no_control_run(state, repo):
    result = _verify(state, repo, LITERAL)
    assert result["status"] == "FAILED", result
