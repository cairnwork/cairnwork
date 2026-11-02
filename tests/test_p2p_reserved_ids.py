"""#669: p2p._plan refuses a task verifier that already uses the id of the plan's own verifier,
even when check_task (the up-front refusal, #644) was skipped."""

import pytest

from cairnwork import p2p, p2p_selection
from cairnwork.verifiers import VerifierSpec

MODES = [m for m in p2p_selection.MODES if m != "off"]


@pytest.mark.parametrize("mode", MODES)
def test_plan_refuses_a_verifier_id_clash_without_check_task(mode):
    specs = (VerifierSpec(f"p2p-{mode}", ("tests/test_a.py",)),)
    with pytest.raises(p2p.P2PRejected) as got:
        p2p._plan(
            specs, {"applied": mode}, (("tests/test_b.py",), ("tests/test_b.py::t",), ()), 300
        )
    assert got.value.code == "P2P_VERIFIER_ID_CLASH"
