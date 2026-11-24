"""Adversarial witnesses for the #1440 review round."""

import pytest
from conftest import commit_change
from test_collect_exposed_1413 import collect
from test_p2p_nodes import CALC, CONFTEST, MUL, MUL_TEST, _run

from cairnwork.p2p_classify_child import classify_payload

TARGET = "tests/test_a.py"
BASE = "class TestA:\n    def test_x(self):\n        assert True\n"
NODE = f"{TARGET}::TestA::test_x"


@pytest.mark.parametrize(
    "code",
    [
        "class K:\n    def __init__(self): assert True\nK()\n",
        "class D:\n    def __set_name__(self, o, n): assert True\nclass K:\n    x = D()\n",
        "class M(type):\n    def __init__(cls, *a): assert True\nclass K(metaclass=M): pass\n",
        "class B:\n    def __init_subclass__(cls): assert True\nclass K(B): pass\n",
        "class W:\n    def __enter__(self): assert True\n"
        "    def __exit__(self,*a): pass\nwith W(): pass\n",
        "@(lambda f: f())\ndef new_body(): assert True\n",
        "if True:\n    def new_body(): assert True\nnew_body()\n",
        "try:\n    def new_body(): assert True\nexcept Exception: pass\nnew_body()\n",
        "def new_body(): assert True\nlocals()['new_body']()\n",
        "import sys\ndef new_body(): assert True\nsys._getframe().f_globals['new_body']()\n",
        "import builtins\ndef new_body(): assert True\nbuiltins.globals()['new_body']()\n",
    ],
    ids=[
        "init",
        "set-name",
        "metaclass-init",
        "init-subclass",
        "enter",
        "lambda-decorator",
        "if-def",
        "try-def",
        "locals",
        "frame-globals",
        "builtins-globals",
    ],
)
def test_import_execution_protocols_keep_deleted_test_dropped(code):
    source = "class TestA: pass\n" + code
    payload = {
        "base_ids": [NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": [TARGET],
        "exposed": [],
        "sources": {"base": {TARGET: BASE}, "test": {TARGET: source}},
    }
    result = classify_payload(payload)
    assert NODE in result["dropped"] and NODE not in result["removed"]


@pytest.mark.parametrize(
    "source",
    [
        "class TestA: pass\ndef helper(): return 2\ndef test_new(): assert helper() == 2\n",
        "import pytest\nclass TestA: pass\n@pytest.fixture\ndef thing(): return 1\n",
        "class TestA: pass\nclass K:\n    def run(self): return 1\n",
    ],
    ids=["test-helper", "fixture", "uninstantiated-method"],
)
def test_nonexecuted_new_definitions_remain_removed(source):
    payload = {
        "base_ids": [NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": [TARGET],
        "exposed": [],
        "sources": {"base": {TARGET: BASE}, "test": {TARGET: source}},
    }
    assert NODE in classify_payload(payload)["removed"]


def test_collect_only_retains_holding_and_origin_files(tmp_path):
    source = "from test_b import Mix\nclass Holder(Mix): pass\n"
    record = collect(
        tmp_path,
        source,
        extra=(("test_b.py", "class Mix:\n    def test_x(self): assert True\n"),),
        collect_files=("tests/test_b.py",),
    )
    assert record["uncollected_exposed"] == [
        "tests/test_b.py::Holder::test_x",
        "tests/test_b.py::Mix::test_x",
        "tests/test_dynamic.py::Holder::test_x",
    ]


@pytest.mark.sandbox
def test_e2e_mixin_origin_does_not_hide_deleted_test(repo, tmp_path):
    base_a = "import test_b\n" + BASE + "class Holder(test_b.Mix): pass\n"
    base_b = "class Mix:\n    def test_x(self): assert True\n"
    commit_change(
        repo,
        {
            "src/calc.py": CALC,
            "tests/conftest.py": CONFTEST,
            "tests/test_a.py": base_a,
            "tests/test_b.py": base_b,
        },
    )
    files = {
        "tests/test_a.py": "import calc\nfrom test_b import Mix\nclass Holder(Mix): pass\n"
        + MUL_TEST,
        "tests/test_b.py": base_b,
    }
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_a.py", "tests/test_b.py")),)
    result = _run(repo, tmp_path, ("src/calc.py",), {"src/calc.py": CALC + MUL}, files, check)
    assert not result.verified
    assert result.reason.startswith("TEST_COMMIT_DROPS_BASE_TESTS"), result.reason
