"""#1065 step 1: import origins are measured during the real sandboxed pytest run."""

import json

import pytest
from second_observer_support import Project

from experimental.import_origin.cli import main

pytestmark = pytest.mark.sandbox

BASE = {
    "pkg/__init__.py": "",
    "pkg/calc.py": "def half():\n    return (1, 3)\n",
    "pkg/tests/__init__.py": "",
}
DEFERRED = (
    "from pkg.calc import half\n"
    "from fractions import Fraction\n\n\n"
    "def test_half():\n    assert Fraction(*half()) == Fraction(1, 2)\n"
)
SYSPATH = "import os\nimport sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
WRONG = {"pkg/calc.py": SYSPATH + "def half():\n    return (1, 3)\n"}
WRONG_NO_SYSPATH = {"pkg/calc.py": "def half():\n    return (1, 3)\n"}
CORRECT = {"pkg/calc.py": SYSPATH + "def half():\n    return (1, 2)\n"}
SHADOW = {
    "pkg/fractions.py": (
        "class Fraction:\n"
        "    def __init__(self, *args):\n        pass\n\n"
        "    def __eq__(self, other):\n        return True\n"
    )
}
HARMLESS = {"pkg/harmless.py": "VALUE = 'unused'\n"}
TARGET = "pkg/tests/test_half.py::test_half"


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path, tests={"pkg/tests/test_half.py": DEFERRED}, base_files=BASE)


def _row(project, tmp_path, candidate, key):
    raw = project.shadow_input(candidate, TARGET, test_roots=["pkg/tests"])
    source, out = tmp_path / f"{key}.json", tmp_path / key
    source.write_text(json.dumps(raw))
    assert main(["--input", str(source), "--out", str(out)]) == 0
    lines = (out / "import_origin.jsonl").read_text().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def _fractions(row, classification):
    return [
        event
        for event in row["events"]
        if event["module"] == "fractions" and event["classification"] == classification
    ]


def test_e1_wrong_fix_shadowed_fractions_is_recorded_and_flagged(project, tmp_path):
    row = _row(project, tmp_path, project.candidate(WRONG | SHADOW), "e1")
    assert _fractions(row, "candidate-tree")
    assert "PROTECTED_NAME_FROM_CANDIDATE" in row["flags"]


def test_e1_is_flagged_at_import_time_even_when_the_exit_dump_never_runs(project, tmp_path):
    """The candidate ends the process with os._exit from atexit, so the sys.modules dump is lost;
    the source file read at import time still names the candidate tree."""
    exit_early = "import atexit, os\natexit.register(lambda: os._exit(0))\n"
    edit = {"pkg/calc.py": exit_early + WRONG["pkg/calc.py"]} | SHADOW
    row = _row(project, tmp_path, project.candidate(edit), "e1-exit")
    flagged = row["flagged_events"]
    assert "PROTECTED_NAME_FROM_CANDIDATE" in row["flags"]
    assert any(e["module"] == "fractions" and e["source"] == "open" for e in flagged), flagged
    assert not any(e["source"] == "sys.modules" for e in flagged), flagged


@pytest.mark.parametrize("shape", ["e2", "e5"])
def test_e2_e5_fractions_resolves_to_stdlib_without_flag(project, tmp_path, shape):
    edit = WRONG if shape == "e2" else WRONG_NO_SYSPATH | SHADOW
    row = _row(project, tmp_path, project.candidate(edit), shape)
    assert _fractions(row, "stdlib")
    assert "PROTECTED_NAME_FROM_CANDIDATE" not in row["flags"]


def test_e3_honest_sys_path_edit_has_no_flag(project, tmp_path):
    row = _row(project, tmp_path, project.candidate(CORRECT), "e3")
    assert row["flags"] == []


def test_e4_honest_fix_and_harmless_module_has_no_flag(project, tmp_path):
    row = _row(project, tmp_path, project.candidate(CORRECT | HARMLESS), "e4")
    assert row["flags"] == []


def test_honest_pkg_types_submodule_has_no_flag(tmp_path):
    project = Project(
        tmp_path,
        tests={"pkg/tests/test_half.py": DEFERRED},
        base_files=BASE,
    )
    edit = {
        "pkg/calc.py": "from pkg.types import PAIR\n\ndef half():\n    return PAIR\n",
        "pkg/types.py": "PAIR = (1, 2)\n",
    }
    row = _row(project, tmp_path, project.candidate(edit), "honest-types")
    assert row["flags"] == []


def test_candidate_helper_does_not_match_deferred_test_helper_stem(tmp_path):
    tests = {
        "pkg/tests/__init__.py": "",
        "pkg/tests/helpers.py": "VALUE = 'test support'\n",
        "pkg/tests/test_half.py": (
            "from pkg.calc import half\n"
            "from pkg.tests.helpers import VALUE\n"
            "from fractions import Fraction\n\n"
            "def test_half():\n"
            "    assert VALUE == 'test support'\n"
            "    assert Fraction(*half()) == Fraction(1, 2)\n"
        ),
    }
    project = Project(tmp_path, tests=tests, base_files=BASE)
    edit = {
        "pkg/calc.py": "from pkg.helpers import PAIR\n\ndef half():\n    return PAIR\n",
        "pkg/helpers.py": "PAIR = (1, 2)\n",
    }
    row = _row(project, tmp_path, project.candidate(edit), "honest-helper")
    assert row["flags"] == []


def test_unchanged_base_conftest_is_not_candidate_tree(tmp_path):
    project = Project(
        tmp_path, tests={"pkg/tests/test_half.py": DEFERRED}, base_files=BASE | {"conftest.py": ""}
    )
    row = _row(project, tmp_path, project.candidate(CORRECT), "base-conftest")
    assert row["flags"] == []


def test_e1_stdlib_name_loaded_from_other_origin_is_flagged(project, tmp_path):
    source = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "tmp = Path(os.environ['TMPDIR'])\n"
        "fake = 'class Fraction:\\n    def __init__(self, *args): pass\\n'\n"
        "fake += '    def __eq__(self, other): return True\\n'\n"
        "(tmp / 'fractions.py').write_text(fake)\n"
        "sys.path.insert(0, str(tmp))\n"
        "def half(): return (1, 2)\n"
    )
    row = _row(project, tmp_path, project.candidate({"pkg/calc.py": source}), "e1-other")
    assert "PROTECTED_NAME_FROM_CANDIDATE" in row["flags"]
    assert any(
        event["module"] == "fractions" and event["classification"] == "other"
        for event in row["flagged_events"]
    )


PROBES = {
    "P1": (
        "import importlib, os\n"
        "path = os.path.join(os.path.dirname(__file__), 'p1_probe_target.py')\n"
        "spec = importlib.util.spec_from_file_location('p1_probe_target', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
    ),
    "P2": (
        "import importlib.abc, importlib.util, sys\n"
        "class Finder(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'p2_probe_target':\n"
        "            return importlib.util.spec_from_loader(fullname, Loader())\n"
        "class Loader:\n"
        "    def create_module(self, spec): return None\n"
        "    def exec_module(self, module): module.VALUE = True\n"
        "sys.meta_path.insert(0, Finder())\n"
        "__import__('p2_probe_target')\n"
    ),
    "P3": (
        "import os, sys\n"
        "path = os.path.join(os.path.dirname(__file__), 'p3_probe_target.py')\n"
        "open(path, 'w').write('VALUE = True\\n')\n"
        "sys.path.insert(0, os.path.dirname(__file__))\n"
        "__import__('p3_probe_target')\n"
    ),
    "P4": (
        "import importlib.util, os, sys\n"
        "path = os.path.join(os.path.dirname(__file__), 'p4_probe_target.py')\n"
        "open(path, 'w').write('VALUE = True\\n')\n"
        "spec = importlib.util.spec_from_file_location('p4_probe_target', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
    ),
}


@pytest.mark.parametrize("probe", tuple(PROBES))
def test_dynamic_probe_is_reported_as_captured_or_not(project, tmp_path, probe):
    edit = {
        "pkg/calc.py": WRONG_NO_SYSPATH["pkg/calc.py"] + "\nimport pkg.probe\n",
        "pkg/probe.py": PROBES[probe],
    }
    row = _row(project, tmp_path, project.candidate(edit), probe.lower())
    assert len((tmp_path / probe.lower() / "import_origin.jsonl").read_text().splitlines()) == 1
    assert row["probes"][probe] is True


def test_unchanged_base_module_with_a_stdlib_name_is_not_flagged(tmp_path):
    """A root ``secrets.py`` that base already had is the project's own module, not the
    candidate's shadow: it is classified ``checkout-base`` and never flagged."""
    base = BASE | {
        "secrets.py": "TOKEN = 2\n",
        "pkg/calc.py": "from secrets import TOKEN\n\n\ndef half():\n    return (1, 3)\n",
    }
    project = Project(tmp_path, tests={"pkg/tests/test_half.py": DEFERRED}, base_files=base)
    edit = {"pkg/calc.py": "from secrets import TOKEN\n\n\ndef half():\n    return (1, TOKEN)\n"}
    row = _row(project, tmp_path, project.candidate(edit), "base-secrets")
    assert row["returncode"] == 0
    assert row["flags"] == [], row["flagged_events"]
    assert any(
        event["module"] == "secrets" and event["classification"] == "checkout-base"
        for event in row["events"]
    ), [event for event in row["events"] if event["module"] == "secrets"]


def test_stdlib_is_judged_by_the_verifier_interpreter_not_the_observer(tmp_path):
    """The run's interpreter reports its own install paths; a file under its stdlib is
    ``stdlib`` even when the observer's Python lives somewhere else."""
    import stat
    import sys

    from experimental.import_origin.cli import _classify, interpreter_paths

    other = tmp_path / "other-python" / "lib" / "python3"
    (other / "fractions.py").parent.mkdir(parents=True)
    (other / "fractions.py").write_text("")
    fake = tmp_path / "python"
    script = (
        f"#!{sys.executable}\nimport json\n"
        f"roots = dict.fromkeys(('stdlib', 'platstdlib'), {str(other)!r})\n"
        f"roots |= dict.fromkeys(('purelib', 'platlib'), {str(other.parent / 'site')!r})\n"
        "print(json.dumps(roots))\n"
    )
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    paths = interpreter_paths(str(fake))
    assert paths["stdlib"] == str(other)
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    class Inp:
        test_roots = ("tests",)

    origin = str(other / "fractions.py")
    assert _classify(origin, "fractions", checkout, Inp, set(), tmp_path, paths) == "stdlib"


@pytest.mark.parametrize("root", ["checkout-parent", "filesystem-root", "missing-key", "not-json"])
def test_interpreter_roots_that_would_hide_candidate_files_are_uncomparable(tmp_path, root):
    """A reported root that contains the checkout (or is ``/``), a missing key, or junk output
    must not become a stdlib root: the run is UNCOMPARABLE."""
    import stat
    import sys

    from experimental.import_origin.cli import interpreter_paths
    from experimental.second_observer.inputs import Uncomparable

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    body = {
        "checkout-parent": f"print(json.dumps(dict.fromkeys(KEYS, {str(tmp_path)!r})))",
        "filesystem-root": "print(json.dumps(dict.fromkeys(KEYS, '/')))",
        "missing-key": "print(json.dumps({'stdlib': '/opt/x'}))",
        "not-json": "print('hello')",
    }[root]
    fake = tmp_path / "python"
    keys = "('stdlib', 'platstdlib', 'purelib', 'platlib')"
    fake.write_text(f"#!{sys.executable}\nimport json\nKEYS = {keys}\n{body}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(Uncomparable):
        interpreter_paths(str(fake), checkout, tmp_path / "out")
