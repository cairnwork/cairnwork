import json

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from test_named_nodes import CALC_BASE, CALC_FIX, CONFTEST, KEEP, _verify

sandbox = pytest.mark.sandbox
ALLOWED = ("src/calc.py", "src/sel.py")


def _case(project, state, parametrisation):
    existing = (
        "import pathlib, pytest\nfrom calc import sub\n"
        f"@pytest.mark.parametrize('p', [{parametrisation}])\n"
        "def test_checkout_path(p):\n    assert p\n"
        "def test_sub():\n    assert sub(3, 1) == 2\n"
    )
    commit_change(
        project,
        {
            "tests/conftest.py": CONFTEST,
            "src/calc.py": CALC_BASE,
            "src/sel.py": KEEP,
            "tests/test_existing.py": existing,
        },
    )
    config_path = state / "config.json"
    config = json.loads(config_path.read_text())
    config_path.write_text(json.dumps({**config, "p2p_selection": "whole-suite"}))
    base, test_commit = make_test_commit(
        project,
        {"tests/test_new.py": "from calc import add\ndef test_new():\n    assert add(1, 1) == 2\n"},
    )
    return _verify(project, state, base, test_commit, {"src/calc.py": CALC_FIX})


@sandbox
@pytest.mark.parametrize(
    "parametrisation",
    [
        "str(pathlib.Path(__file__).parents[1])",
        "str(pathlib.Path(__file__).resolve().parents[1])",
    ],
    ids=["raw-checkout", "realpath-checkout"],
)
def test_checkout_path_parameter_ids_allow_correct_candidate(project, state, parametrisation):
    out = _case(project, state, parametrisation)
    assert out["verified"] is True, out


@sandbox
def test_uuid_parameter_still_refuses_with_stable_id_advice(project, state):
    out = _case(project, state, "__import__('uuid').uuid4().hex")
    assert out["verified"] is False, out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_outside_checkout_path_is_refused_without_host_path(project, state):
    out = _case(project, state, "str(pathlib.Path(__file__).parents[2])")
    assert out["verified"] is False, out
    assert "per-run" in out["reason"] and "<path>" in out["reason"], out
    assert all(prefix not in out["reason"] for prefix in ("/Users/", "/private/", "/var/")), out
