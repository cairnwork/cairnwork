"""#927: an explicit ``allow_conftest: false`` in the receiver policy refuses a receipt with
conftest paths in full mode too (before, only a confidential receiver read the key)."""

from types import SimpleNamespace

import cairnwork_attest.binding_extras as bx

BINDING = {"test_paths": ["tests/conftest.py"], "conftest_paths": ["tests/conftest.py"]}


def _ctx(binding, *, full, policy=None):
    return SimpleNamespace(binding=binding, full=full, policy=policy, repo=None)


def test_a_full_receiver_honours_an_explicit_false():
    refused = bx.conftest_problem(_ctx(BINDING, full=True, policy={"allow_conftest": False}))
    assert refused is not None and refused.code == "CONFTEST_NOT_ALLOWED"


def test_a_confidential_receiver_still_refuses_false():
    refused = bx.conftest_problem(_ctx(BINDING, full=False, policy={"allow_conftest": False}))
    assert refused is not None and refused.code == "CONFTEST_NOT_ALLOWED"


def test_false_without_conftest_paths_is_not_refused():
    assert bx.conftest_problem(_ctx({}, full=False, policy={"allow_conftest": False})) is None
