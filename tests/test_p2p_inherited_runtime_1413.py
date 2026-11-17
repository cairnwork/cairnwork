"""#1413: runtime collect evidence also gates inherited deletion classification."""

import pytest
from test_collect_exposed_1413 import TARGET, collect
from test_p2p_runtime_gate_1413 import ASSEMBLERS

from cairnwork.p2p_classify_child import classify_payload
from cairnwork.p2p_collect import classify

pytestmark = pytest.mark.sandbox

KEEP = "    def test_keep(self):\n        assert True\n"
BASE = (
    "class TestA:\n    def test_x(self):\n        assert True\n"
    + KEEP
    + "class TestB(TestA):\n    pass\n"
)
HONEST = "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\n"
NODE = f"{TARGET}::TestB::test_x"
# shapes the inherited AST path alone reads as removed; the runtime fact must catch every shape
INHERITED_MISSED_BY_AST = {"concat", "exec_concat", "fstring", "loop", "metaclass", "type_concat"}


def _assembled(shape):
    return (
        "def _x(self):\n    assert True\nf = _x\n" + HONEST + "class Helper: pass\n" + shape + "\n"
    )


def _side(tmp_path, test, *, real_fact):
    base_ids = set(collect(tmp_path / "base", BASE)["collected"])
    record = collect(tmp_path / "test", test)
    exposed = tuple(record["uncollected_exposed"]) if real_fact else ()
    return classify(
        base_ids,
        set(record["collected"]),
        {TARGET},
        lambda _: test,
        lambda _: BASE,
        exposed=exposed,
    )


@pytest.mark.parametrize("shape", sorted(ASSEMBLERS))
def test_inherited_runtime_reattachment_stays_dropped(tmp_path, shape):
    side = _side(tmp_path, _assembled(ASSEMBLERS[shape]), real_fact=True)
    assert NODE in side.dropped and NODE not in side.removed


@pytest.mark.parametrize("shape", sorted(ASSEMBLERS))
def test_real_runtime_fact_catches_shapes_ast_alone_misses(tmp_path, shape):
    side = _side(tmp_path, _assembled(ASSEMBLERS[shape]), real_fact=False)
    assert (NODE in side.removed) == (shape in INHERITED_MISSED_BY_AST)


def test_real_honest_inherited_deletion_is_removed(tmp_path):
    side = _side(tmp_path, HONEST, real_fact=True)
    assert {NODE, f"{TARGET}::TestA::test_x"} <= side.removed


@pytest.mark.parametrize(
    "base,test,base_ids,test_ids",
    [
        (
            "class TestA:\n    def test_x(self): pass\nclass TestB(TestA): pass\n",
            "class TestA: pass\nclass TestB(TestA): pass\n",
            ["t.py::TestA::test_x", "t.py::TestB::test_x"],
            [],
        ),
        (
            "class A:\n    def test_x(self): pass\nclass TestB(A): pass\n",
            "class A: pass\nclass TestB(A): pass\n",
            ["t.py::TestB::test_x"],
            [],
        ),
    ],
    ids=["I1-inherited", "T7-uncollected-parent"],
)
@pytest.mark.parametrize(
    "payload_exposed",
    ["absent", "malformed", "integer", "same-name", "space-in-owner", "trailing-space"],
)
def test_missing_or_invalid_runtime_fact_keeps_inherited_id_dropped(
    base, test, base_ids, test_ids, payload_exposed
):
    payload = {
        "base_ids": base_ids,
        "test_ids": test_ids,
        "edited": ["t.py"],
        "sources": {"base": {"t.py": base}, "test": {"t.py": test}},
    }
    if payload_exposed == "malformed":
        payload["exposed"] = "x"
    elif payload_exposed == "integer":
        payload["exposed"] = [4]
    elif payload_exposed == "same-name":
        payload["exposed"] = ["t.py::Helper::test_x"]
    elif payload_exposed == "space-in-owner":
        payload["exposed"] = ["t.py::Bad Owner::test_other"]
    elif payload_exposed == "trailing-space":
        payload["exposed"] = ["t.py::Helper::test_other "]
    assert set(classify_payload(payload)["dropped"]) == set(base_ids)


@pytest.mark.parametrize(
    "base,test",
    [
        (BASE, "class TestA: pass\nclass TestB(TestA): pass\n"),
        (
            "class A:\n    def test_x(self): pass\nclass TestB(A): pass\n",
            "class A: pass\nclass TestB(A): pass\n",
        ),
    ],
    ids=["I1", "T7"],
)
def test_inherited_honest_deletion_passes(base, test):
    base_node = "t.py::TestA::test_x" if "class TestA" in base else "t.py::TestB::test_x"
    test_node = "t.py::TestB::test_x" if base_node.endswith("TestA::test_x") else base_node
    payload = {
        "base_ids": [base_node, test_node] if base_node != test_node else [base_node],
        "test_ids": [],
        "edited": ["t.py"],
        "exposed": [],
        "sources": {"base": {"t.py": base}, "test": {"t.py": test}},
    }
    assert set(classify_payload(payload)["removed"]) == set(payload["base_ids"])


@pytest.mark.parametrize(
    "source",
    [
        "import re\nPATTERN = re.compile('x')\nclass TestA: pass\nclass TestB(TestA): pass\n",
        "class Box: pass\nNAMES = list(Box.__dict__)\n"
        "class TestA: pass\nclass TestB(TestA): pass\n",
        "class Box: pass\nclass TestA:\n    def test_keep(self, monkeypatch):\n"
        "        monkeypatch.setattr(Box, 'value', 1, raising=False)\nclass TestB(TestA): pass\n",
    ],
    ids=["re-compile", "dict-read", "monkeypatch-setattr"],
)
def test_honest_runtime_patterns_do_not_block_deletion(source):
    base = "class TestA:\n    def test_x(self): pass\nclass TestB(TestA): pass\n"
    payload = {
        "base_ids": ["t.py::TestA::test_x", "t.py::TestB::test_x"],
        "test_ids": [],
        "edited": ["t.py"],
        "exposed": [],
        "sources": {"base": {"t.py": base}, "test": {"t.py": source}},
    }
    assert set(classify_payload(payload)["removed"]) == set(payload["base_ids"])


def test_plain_move_keeps_direct_id_removed_and_inherited_id_dropped():
    # Spec C "a plain move stays accepted" holds for the direct id (as on main); the inherited id
    # stays dropped, as #1426 f05a8e65 tested (a move must not excuse the subclass copy).
    base = BASE
    test = "class TestA: pass\nclass TestB(TestA): pass\nclass TestC:\n    def test_x(self): pass\n"
    payload = {
        "base_ids": ["t.py::TestA::test_x", "t.py::TestB::test_x"],
        "test_ids": ["t.py::TestC::test_x"],
        "edited": ["t.py"],
        "exposed": [],
        "sources": {"base": {"t.py": base}, "test": {"t.py": test}},
    }
    side = classify_payload(payload)
    assert side["removed"] == ["t.py::TestA::test_x"]
    assert side["dropped"] == ["t.py::TestB::test_x"]
