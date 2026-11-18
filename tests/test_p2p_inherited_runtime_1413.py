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


LAMBDA_PAYLOAD = "_x = lambda self: None\nf = _x\n"
DEF_PAYLOAD = "def _x(self):\n    assert True\nf = _x\n"


def _assembled(shape, payload=LAMBDA_PAYLOAD):
    return payload + HONEST + "class Helper: pass\n" + shape + "\n"


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


@pytest.mark.parametrize("shape", sorted(INHERITED_MISSED_BY_AST))
def test_ast_catches_def_payload_run_at_import(tmp_path, shape):
    """#1413 Codex round 2: a module-level def loaded at import is not excused by AST."""
    side = _side(tmp_path, _assembled(ASSEMBLERS[shape], DEF_PAYLOAD), real_fact=False)
    assert NODE in side.dropped


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


KEPT_SRC = "class TestA:\n    def test_keep(self): pass\nclass TestB(TestA): pass\n"


def _inherited_deletion(test_src, exposed):
    payload = {
        "base_ids": ["t.py::TestA::test_x", "t.py::TestB::test_x"],
        "test_ids": ["t.py::TestA::test_keep", "t.py::TestB::test_keep"],
        "edited": ["t.py"],
        "exposed": exposed,
        "sources": {
            "base": {
                "t.py": "class TestA:\n    def test_x(self): pass\n    def test_keep(self): pass\n"
                "class TestB(TestA): pass\n"
            },
            "test": {"t.py": test_src},
        },
    }
    return classify_payload(payload)


@pytest.mark.parametrize(
    "exposed, verdict",
    [
        (["t.py::Helper::test_other", "t.py::Helper::test_other"], "dropped"),
        (["u.py::Helper::test_other", "u.py::Helper::test_other"], "dropped"),
        (["t.py::Helper::test_other"], "removed"),
        ([], "removed"),
    ],
    ids=["duplicate-same-file", "duplicate-other-file", "single", "empty"],
)
def test_duplicate_runtime_fact_is_no_fact(exposed, verdict):
    """Codex round 2 at 3a14ecc8: the collector never repeats an id, so a repeat is no fact."""
    assert "t.py::TestB::test_x" in _inherited_deletion(KEPT_SRC, exposed)[verdict]


IMPORT_TIME_RUNS = {
    "module-call": "def _run_test_body():\n    assert True\n_run_test_body()\n",
    "alias-call": "def _body():\n    assert True\nrun = _body\nrun()\n",
    "class-instance": "class Body:\n    def __init__(self):\n        assert True\nBody()\n",
    "default-arg": "def _body():\n    return 1\ndef helper(x=_body()):\n    pass\n",
    "decorator": "def _body(f):\n    return f\n@_body\ndef helper():\n    pass\n",
    "class-body": "def _body():\n    assert True\nclass Runner:\n    value = _body()\n",
}


@pytest.mark.parametrize("prefix", IMPORT_TIME_RUNS.values(), ids=IMPORT_TIME_RUNS.keys())
def test_local_code_run_at_import_keeps_inherited_id_dropped(prefix):
    """Codex round 2 at 3a14ecc8: a renamed test body called at module scope still runs."""
    assert "t.py::TestB::test_x" in _inherited_deletion(prefix + KEPT_SRC, [])["dropped"]


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "def _helper():\n    return 1\ndef _uses():\n    return _helper()\n",
        "import re\nPATTERN = re.compile('x')\n",
        "class Helper:\n    pass\nNAME = Helper.__name__\n",
    ],
    ids=["nothing", "helper-only-used-in-bodies", "imported-call", "class-reference"],
)
def test_no_local_code_at_import_still_removes_inherited_id(prefix):
    assert "t.py::TestB::test_x" in _inherited_deletion(prefix + KEPT_SRC, [])["removed"]
