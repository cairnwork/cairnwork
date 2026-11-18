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
def test_other_changed_python_file_keeps_inherited_id_dropped(path):
    payload = _payload(HONEST) | {"changed_python_files": [TARGET, path]}
    assert NODE in classify_payload(payload)["dropped"]


def test_missing_changed_python_file_fact_keeps_inherited_id_dropped():
    payload = _payload(HONEST)
    payload.pop("changed_python_files")
    assert NODE in classify_payload(payload)["dropped"]


def _payload(test):
    return {
        "base_ids": [f"{TARGET}::TestA::test_x", NODE],
        "test_ids": [],
        "edited": [TARGET],
        "changed_python_files": [TARGET],
        "exposed": [],
        "sources": {
            "base": {TARGET: BASE},
            "test": {TARGET: test},
        },
    }


PASSING_MODULES = {
    "honest": HONEST,
    "imports": "import os\nfrom pathlib import Path\n" + HONEST,
    "pytestmark": "import pytest\npytestmark = pytest.mark.slow\n" + HONEST,
    "parametrize": "import pytest\nclass TestA:\n"
    "    @pytest.mark.parametrize('a', [1, 2])\n"
    "    def test_keep(self, a):\n        assert a\n"
    "class TestB(TestA):\n    pass\n",
    "fixture_staticmethod": "import pytest\nclass TestA:\n"
    "    @pytest.fixture\n    def fixture(self):\n        return 1\n"
    "    @staticmethod\n    def helper():\n        return 1\n"
    "class TestB(TestA):\n    pass\n",
    "helper": "def helper():\n    return 1\n" + HONEST,
    "constants": "N = 3\n" + HONEST.replace("class TestA:", "class TestA:\n    SIZE = 4"),
    "docstrings": '"""module docs"""\n'
    + HONEST.replace("class TestA:", 'class TestA:\n    """class docs"""'),
    "annotation": "from pathlib import Path\nclass TestA:\n"
    "    def test_keep(self, tmp_path: Path):\n        assert tmp_path\n"
    "class TestB(TestA):\n    pass\n",
}


@pytest.mark.parametrize("source", PASSING_MODULES.values(), ids=PASSING_MODULES.keys())
def test_simple_inherited_deletion_remains_removed(source):
    result = classify_payload(_payload(source))
    assert NODE in result["removed"]


def test_honest_inherited_deletion_with_real_collect_only_record(tmp_path):
    side = _side(tmp_path, HONEST, real_fact=True)
    assert NODE in side.removed


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\npytestmark = pytest.mark.slow\n" + HONEST,
        "import pytest\nclass TestA:\n"
        "    @pytest.mark.parametrize('a', [1, 2])\n"
        "    def test_keep(self, a):\n        assert a\n"
        "class TestB(TestA):\n    pass\n",
        "import pytest\nclass TestA:\n"
        "    @pytest.fixture\n    def fixture(self):\n        return 1\n"
        "    @staticmethod\n    def helper():\n        return 1\n"
        "class TestB(TestA):\n    pass\n",
        "from pathlib import Path\nclass TestA:\n"
        "    def test_keep(self, tmp_path: Path):\n        assert tmp_path\n"
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


def test_changed_python_files_count_every_python_path(tmp_path):
    """A root conftest.py outside the test roots still counts; a bad commit is no fact."""
    import subprocess
    from types import SimpleNamespace

    from cairnwork.p2p_collect import _changed_test_python_files

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
    git("add", "-A")
    git("commit", "-qm", "test")
    head = git("rev-parse", "HEAD")
    task = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit=head)
    assert _changed_test_python_files(task) == ["conftest.py", "tests/test_a.py"]
    bad = SimpleNamespace(repo=tmp_path, base_commit=base, test_commit="0" * 40)
    assert _changed_test_python_files(bad) is None
