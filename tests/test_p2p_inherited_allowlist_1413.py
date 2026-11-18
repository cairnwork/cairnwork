"""#1413: inherited deletion needs a simple module and an isolated test-file change."""

import pytest
from test_collect_exposed_1413 import TARGET
from test_p2p_inherited_runtime_1413 import (
    BASE,
    HONEST,
    IMPORT_TIME_RUNS,
    NODE,
    _inherited_deletion,
    _side,
)
from test_p2p_runtime_gate_1413 import ASSEMBLERS, _e2e

from cairnwork.p2p_classify_child import classify_payload

BLOCKED_MODULES = {
    "new_class": "import types\nf = lambda self: None\n"
    "types.new_class('Helper', (), exec_body=lambda ns: ns.update({'test_' + 'x': f}))\n",
    "abc_meta": "import abc\nf = lambda self: None\n"
    "abc.ABCMeta('Helper', (), {'test_' + 'x': f})\n",
    "other_module": "import types\nf = lambda self: None\n"
    "m = types.ModuleType('helper_mod')\nexec('class H: pass', m.__dict__)\n"
    "setattr(m.H, 'test_' + 'x', f)\n",
    "fresh_exec": "import types\nns = {}\nexec('def g(self=None): pass', ns)\n"
    "types.new_class('Helper', (), exec_body=lambda d: d.update({'test_' + 'x': ns['g']}))\n",
    "if": "if True:\n    pass\n",
    "for": "for _ in ():\n    pass\n",
    "try": "try:\n    pass\nexcept Exception:\n    pass\n",
    "module_call": "def local():\n    return None\nlocal()\n",
    "decorator_call": "def local(f):\n    return f\n@local\ndef helper():\n    pass\n",
    "default_call": "def local():\n    return None\ndef helper(x=local()):\n    pass\n",
    "class_keyword": "class Helper(metaclass=type):\n    pass\n",
    "class_alias": "f = lambda self: None\nclass Helper:\n    test_y = f\n",
    "nested_class": "class Helper:\n    class Nested:\n        pass\n",
    **IMPORT_TIME_RUNS,
}


@pytest.mark.parametrize("shape", sorted(BLOCKED_MODULES))
def test_non_simple_inherited_modules_stay_dropped_with_real_fact(tmp_path, shape):
    side = _side(tmp_path, BLOCKED_MODULES[shape] + HONEST, real_fact=True)
    assert NODE in side.dropped and NODE not in side.removed


@pytest.mark.parametrize("shape", sorted(ASSEMBLERS))
def test_runtime_assembler_shapes_stay_dropped_with_real_fact(tmp_path, shape):
    source = (
        "def _x(self):\n    assert True\nf = _x\n"
        + HONEST
        + "class Helper: pass\n"
        + ASSEMBLERS[shape]
    )
    side = _side(tmp_path, source, real_fact=True)
    assert NODE in side.dropped and NODE not in side.removed


@pytest.mark.parametrize("shape", ["new_class", "abc_meta"])
@pytest.mark.sandbox
def test_dynamic_inherited_reattachment_is_refused_end_to_end(repo, tmp_path, shape):
    result = _e2e(repo, tmp_path, BASE, BLOCKED_MODULES[shape] + HONEST)
    assert not result.verified
    assert result.reason.startswith(("TEST_COMMIT_DROPS_BASE_TESTS", "DEFERRED_TESTS_UNREADABLE"))


@pytest.mark.parametrize("path", ["tests/conftest.py", "tests/helper.py"])
def test_other_changed_file_keeps_inherited_id_dropped(path):
    payload = _payload(HONEST) | {"changed_test_files": [TARGET, path]}
    assert NODE in classify_payload(payload)["dropped"]


def test_missing_changed_file_fact_keeps_inherited_id_dropped():
    payload = _payload(HONEST)
    payload.pop("changed_test_files")
    assert NODE in classify_payload(payload)["dropped"]


def _payload(test):
    return {
        "base_ids": [f"{TARGET}::TestA::test_x", NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_test_files": [TARGET],
        "exposed": [],
        "sources": {
            "base": {TARGET: BASE},
            "test": {TARGET: test},
        },
    }


PASSING_MODULES = {
    "honest": HONEST,
    "parametrize": "import pytest\nclass TestA:\n"
    "    @pytest.mark.parametrize('a', [1, 2])\n"
    "    def test_keep(self, a):\n        assert a\n"
    "class TestB(TestA):\n    pass\n",
    "constants": "N = 3\n" + HONEST.replace("class TestA:", "class TestA:\n    SIZE = 4"),
    "docstrings": '"""module docs"""\n'
    + HONEST.replace("class TestA:", 'class TestA:\n    """class docs"""'),
}

BLOCKED_GRAMMAR_MODULES = {
    "pytestmark": "import pytest\npytestmark = pytest.mark.slow\n" + HONEST,
    "fixture_staticmethod": "import pytest\nclass TestA:\n"
    "    @pytest.fixture\n    def fixture(self):\n        return 1\n"
    "    @staticmethod\n    def helper():\n        return 1\n"
    "class TestB(TestA):\n    pass\n",
    "helper": "def helper():\n    return 1\n" + HONEST,
    "builtin_annotation": "class TestA:\n"
    "    def test_keep(self, tmp_path: int):\n        assert tmp_path\n"
    "class TestB(TestA):\n    pass\n",
    "same_file_annotation": "class Local:\n    pass\nclass TestA:\n"
    "    def test_keep(self, value: Local):\n        assert value\n"
    "class TestB(TestA):\n    pass\n",
}


BLOCKED_IMPORT_SHAPES = {
    "import_os": "import os\n" + HONEST,
    "from_os": "from os import path\n" + HONEST,
    "star": "from helpers import *\n" + HONEST,
    "pytest_alias": "import pytest as pt\n" + HONEST,
    "pytest_from": "from pytest import mark\n" + HONEST,
    "pytest_rebound": "import pytest\npytest = 1\n" + HONEST,
    "staticmethod_rebound": "def staticmethod(f):\n    return f\n" + HONEST,
    "object_rebound": "class object: pass\n" + HONEST,
    "dotted_user_decorator": "import helpers\nclass TestA:\n"
    "    @helpers.attach\n    def test_keep(self):\n        pass\n"
    "class TestB(TestA):\n    pass\n",
    "imported_base": "import unittest\nclass TestA(unittest.TestCase):\n"
    "    def test_keep(self):\n        pass\nclass TestB(TestA):\n    pass\n",
    "unknown_base": "class TestA(Missing):\n    pass\nclass TestB(TestA):\n    pass\n",
    "imported_annotation": "from pathlib import Path\nclass TestA:\n"
    "    def test_keep(self, tmp_path: Path):\n        assert tmp_path\n"
    "class TestB(TestA):\n    pass\n",
}


@pytest.mark.parametrize("source", BLOCKED_IMPORT_SHAPES.values(), ids=BLOCKED_IMPORT_SHAPES.keys())
def test_untrusted_imports_and_names_keep_inherited_id_dropped(tmp_path, source):
    name = next(key for key, value in BLOCKED_IMPORT_SHAPES.items() if value == source)
    if name == "unknown_base":
        result = classify_payload(_payload(source))
        assert NODE in result["dropped"] and NODE not in result["removed"]
        return
    extra = (
        (("helpers.py", "def attach(cls):\n    return cls\n"),)
        if name in {"star", "dotted_user_decorator"}
        else ()
    )
    side = _side(tmp_path, source, real_fact=True, extra=extra)
    assert NODE in side.dropped and NODE not in side.removed


@pytest.mark.parametrize("source", PASSING_MODULES.values(), ids=PASSING_MODULES.keys())
def test_simple_inherited_deletion_remains_removed(source):
    result = classify_payload(_payload(source))
    assert NODE in result["removed"]


@pytest.mark.parametrize(
    "source", BLOCKED_GRAMMAR_MODULES.values(), ids=BLOCKED_GRAMMAR_MODULES.keys()
)
def test_closed_grammar_false_red_shapes_stay_dropped(source):
    result = classify_payload(_payload(source))
    assert NODE in result["dropped"] and NODE not in result["removed"]


def test_honest_inherited_deletion_with_real_collect_only_record(tmp_path):
    side = _side(tmp_path, HONEST, real_fact=True)
    assert NODE in side.removed


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\nclass TestA:\n"
        "    @pytest.mark.parametrize('a', [1, 2])\n"
        "    def test_keep(self, a):\n        assert a\n"
        "class TestB(TestA):\n    pass\n",
    ],
)
def test_collectable_allowlist_shapes_with_real_fact(tmp_path, source):
    side = _side(tmp_path, source, real_fact=True)
    assert NODE in side.removed


def test_import_time_shapes_are_blocked_by_allowlist():
    node = "t.py::TestB::test_x"
    for source in IMPORT_TIME_RUNS.values():
        assert node in _inherited_deletion(source + HONEST, [])["dropped"]


def test_changed_test_files_count_every_path(tmp_path):
    """Every changed path, including non-Python files, counts; a bad commit is no fact."""
    import subprocess
    from types import SimpleNamespace

    from cairnwork.p2p_collect import _changed_test_files

    def git(*args):
        cmd = ["git", "-c", "commit.gpgsign=false", "-C", str(tmp_path), *args]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "tests" / "test_a.py").write_text("x = 2\n")
    (tmp_path / "conftest.py").write_text("y = 1\n")
    (tmp_path / "README.md").write_text("r\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "tests" / "evil.pyc").write_bytes(b"compiled")
    git("add", "-A")
    git("commit", "-qm", "test")
    head = git("rev-parse", "HEAD")
    task = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit=head)
    assert _changed_test_files(task) == [
        "README.md",
        "conftest.py",
        "pytest.ini",
        "tests/evil.pyc",
        "tests/test_a.py",
    ]
    bad = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit="0" * 40)
    assert _changed_test_files(bad) is None


EVIL_AS_PYTEST = (
    "import evil as pytest\nclass TestA:\n    def test_keep(self):\n        pass\n"
    "@pytest.mark.attach\nclass TestB(TestA):\n    pass\n"
)
EVIL_HELPER = (
    "class _Mark:\n    def attach(self, cls):\n"
    "        def test_x(self):\n            assert 1\n"
    "        cls.test_x = test_x\n        cls.test_x(None)\n        del cls.test_x\n"
    "        return cls\nmark = _Mark()\n"
)


def test_unchanged_helper_imported_as_pytest_keeps_id_dropped(tmp_path):
    """The cross-model probe: an unchanged module bound to the name pytest runs the old body."""
    side = _side(tmp_path, EVIL_AS_PYTEST, real_fact=True, extra=(("evil.py", EVIL_HELPER),))
    assert NODE in side.dropped and NODE not in side.removed


AST_ONLY_BLOCKED = {
    "pytest_submodule": "import pytest.mark\n" + HONEST,
    "pytest_from_star": "from pytest import *\n" + HONEST,
    "module_getattr": "def __getattr__(name):\n    return None\n" + HONEST,
    "module_dir": "def __dir__():\n    return []\n" + HONEST,
    "globals_store": HONEST + "globals()['TestC'] = TestA\n",
    "globals_value": "G = globals()\n" + HONEST,
    "exec_stmt": HONEST + "exec('x = 1')\n",
    "exec_value": "X = exec('x = 1')\n" + HONEST,
}


@pytest.mark.parametrize("name", sorted(AST_ONLY_BLOCKED))
def test_pytest_submodules_module_getattr_and_exec_keep_id_dropped(name):
    result = classify_payload(_payload(AST_ONLY_BLOCKED[name]))
    assert NODE in result["dropped"] and NODE not in result["removed"]
