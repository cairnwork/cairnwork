import json

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from test_named_nodes import CALC_BASE, CALC_FIX, CONFTEST, KEEP, _verify

sandbox = pytest.mark.sandbox
ALLOWED = ("src/calc.py", "src/sel.py")


def _case(
    project, state, parametrisation, *, new_test=None, new_test_files=None, p2p="whole-suite"
):
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
    config_path.write_text(json.dumps({**config, "p2p_selection": p2p}))
    default_test = "from calc import add\ndef test_new():\n    assert add(1, 1) == 2\n"
    test_files = {"tests/test_new.py": new_test or default_test}
    test_files.update(new_test_files or {})
    base, test_commit = make_test_commit(project, test_files)
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
@pytest.mark.parametrize(
    "parametrisation",
    [
        "str(pathlib.Path(__file__).parents[1])",
        "str(pathlib.Path(__file__).resolve().parents[1])",
    ],
    ids=["raw-checkout", "realpath-checkout"],
)
def test_checkout_path_parameter_ids_allow_correct_candidate_with_p2p_off(
    project, state, parametrisation
):
    out = _case(project, state, parametrisation, p2p="off")
    assert out["verified"] is True, out


@sandbox
def test_uuid_parameter_still_refuses_with_stable_id_advice(project, state):
    out = _case(project, state, "__import__('uuid').uuid4().hex")
    assert out["verified"] is False, out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_uuid_parameter_in_base_test_with_p2p_off_is_refused(project, state):
    out = _case(project, state, "__import__('uuid').uuid4().hex", p2p="off")
    assert out["verified"] is False, out
    assert "NAMED_TESTS_NOT_RUN" in out["reason"], out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_time_ns_parameter_still_refuses_with_stable_id_advice(project, state):
    out = _case(project, state, "__import__('time').time_ns()")
    assert out["verified"] is False, out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_uuid_parameter_in_deferred_test_is_seed_sensitive(project, state):
    new_test = (
        "import pytest\nfrom calc import add\n"
        "@pytest.mark.parametrize('value', [__import__('uuid').uuid4().hex])\n"
        "def test_new(value):\n    assert add(1, 1) == 2\n"
    )
    out = _case(project, state, "'stable'", new_test=new_test)
    assert out["verified"] is False, out
    assert "BASE_SEED_SENSITIVE" in out["reason"], out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_checkout_path_parameter_in_deferred_test_is_verified_on_seed_replay(project, state):
    new_test = (
        "import pathlib, pytest\nfrom calc import add\n"
        "@pytest.mark.parametrize('value', [str(pathlib.Path(__file__).parents[1])])\n"
        "def test_new(value):\n    assert value and add(1, 1) == 2\n"
    )
    out = _case(project, state, "'stable'", new_test=new_test)
    assert out["verified"] is True, out
    assert "BASE_SEED_SENSITIVE" not in out["reason"], out


@sandbox
def test_literal_placeholder_with_checkout_path_keeps_refusal(project, state):
    parametrisation = "str(pathlib.Path(__file__).parents[1]), '<cw-checkout>/x'"
    out = _case(project, state, parametrisation, p2p="off")
    assert out["verified"] is False, out
    assert "NAMED_TESTS_NOT_RUN" in out["reason"], out
    assert "per-run" in out["reason"] and "ids=" in out["reason"], out


@sandbox
def test_outside_checkout_path_is_refused_without_host_path(project, state):
    out = _case(project, state, "str(pathlib.Path(__file__).parents[2])")
    assert out["verified"] is False, out
    assert "per-run" in out["reason"] and "<path>" in out["reason"], out
    assert all(prefix not in out["reason"] for prefix in ("/Users/", "/private/", "/var/")), out
