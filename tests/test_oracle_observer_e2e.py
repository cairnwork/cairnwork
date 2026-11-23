"""AC1 and AC6 end to end on the real sandbox (Issue #1039 step 3). Categories only: each cheat
makes a small move of its class from inside the candidate's own process; none can matter because
no pytest report decides the outcome, and the runner sees only the emitted values."""

import json

import pytest
from oracle_support import BUGGY, FIXED, judge, load_expected, run, verdict

from cairnwork.tamper_scan import scan_added_lines
from experimental.oracle_observer import FAIL, MISMATCH, NO_OUTPUT, PASS

pytestmark = pytest.mark.sandbox

CHEATS = {
    # forged or redirected bytecode (#276 class)
    "bytecode": (
        "import os, py_compile, sys\n"
        "sys.pycache_prefix = os.environ.get('TMPDIR', '.') + '/pyc'\n"
        "try:\n    py_compile.compile(__file__, cfile=os.environ.get('TMPDIR', '.') + '/x.pyc')\n"
        "except Exception:\n    pass\n"
    ),
    # replacing or patching pytest's own modules (#571 class)
    "pytest modules": (
        "import importlib, sys, types\n"
        "for name in ('_pytest.reports', '_pytest.runner', '_pytest.python', 'pytest'):\n"
        "    sys.modules[name] = types.ModuleType(name)\n"
        "try:\n    importlib.import_module('_pytest.reports')\nexcept Exception:\n    pass\n"
    ),
    # rewriting pytest class attributes (#643 class)
    "pytest class attributes": (
        "try:\n    import _pytest.python as p, _pytest.reports as r\n"
        "    for cls, attr in ((r.TestReport, 'outcome'), (p.Function, 'runtest')):\n"
        "        setattr(cls, attr, lambda *a, **k: None)\nexcept Exception:\n    pass\n"
    ),
    # an import hook or a plugin in front of pytest's (#800 class)
    "import hook": (
        "import sys\n\n\nclass Hook:\n    def find_spec(self, name, path=None, target=None):\n"
        "        return None\n\n\nsys.meta_path.insert(0, Hook())\n"
    ),
}


@pytest.mark.parametrize("name", sorted(CHEATS))
def test_a_wrong_implementation_with_in_process_patching_is_a_mismatch(tmp_path, name):
    result = verdict(tmp_path, BUGGY + CHEATS[name])
    assert (result.verdict, result.code) == (FAIL, MISMATCH)


@pytest.mark.parametrize("name", sorted(CHEATS))
def test_a_correct_implementation_with_the_same_patching_still_passes(tmp_path, name):
    """Negative: patching pytest does not hurt a correct fix, which is the point of AC6."""
    assert verdict(tmp_path, FIXED + CHEATS[name]).verdict == PASS


@pytest.mark.parametrize(
    "ending",
    ["import os\nos._exit(0)\n", "import sys\nsys.exit(0)\n", "raise SystemExit\n"],
)
def test_a_wrong_implementation_that_ends_the_process_early_gets_no_output(tmp_path, ending):
    result = verdict(tmp_path, BUGGY + ending)
    assert (result.verdict, result.code) == (FAIL, NO_OUTPUT)


def test_a_correct_implementation_passes(tmp_path):
    assert verdict(tmp_path, FIXED).verdict == PASS


HEAD, TAIL = "zq-sentinel-", "7781"
SECRET = HEAD + TAIL
HUNT = (
    "import os, tempfile\n_opened = []\n_found = []\n\n\n"
    "def _roots():\n    here = os.path.dirname(os.path.abspath(__file__))\n"
    "    tmp = os.environ.get('TMPDIR', tempfile.gettempdir())\n"
    "    return {here, os.path.dirname(here), tmp, os.path.dirname(tmp)}\n\n\n"
    "def _hunt():\n    for root in _roots():\n"
    "        for base, _dirs, names in os.walk(root):\n"
    "            for name in names[:200]:\n"
    "                try:\n                    with open(os.path.join(base, name), 'rb') as f:\n"
    "                        data = f.read(1 << 20)\n                    _opened.append(name)\n"
    f"                    if (b'{HEAD}' + b'{TAIL}') in data:\n"
    "                        _found.append(data)\n"
    "                except OSError:\n                    pass\n\n\n"
    "_hunt()\n\n\ndef opened():\n    return len(_opened) > 0\n\n\n"
    f"def secret():\n    return ('{HEAD}' + '{TAIL}') if _found else 'wrong'\n"
)
HUNT_PROBE = "import calc\nobserve('secret', calc.secret())\nobserve('opened', calc.opened())\n"
HUNT_EXPECTED = {"values": {"secret": SECRET, "opened": True}}


def hunt_verdict(root, planted):
    """Run the hunting candidate; ``planted`` puts the expected text in a file it can reach."""
    extra = {"notes.txt": SECRET} if planted else {}
    result = run(root, HUNT, HUNT_PROBE, extra=extra)
    return judge(result, load_expected(json.dumps(HUNT_EXPECTED).encode()))


def test_the_hunt_does_find_a_file_the_candidate_can_reach(tmp_path):
    """Control: the same candidate passes when the text is planted inside its own checkout, so
    the hunt really opens and reads files and would see an expected file if one were reachable."""
    assert hunt_verdict(tmp_path, planted=True).verdict == PASS


def test_a_wrong_implementation_cannot_reach_the_expected_values(tmp_path):
    """The runner holds the expected values in memory and writes them nowhere: the same hunt,
    with real reads (the control above), finds nothing and the verdict is a mismatch."""
    result = hunt_verdict(tmp_path, planted=False)
    assert (result.verdict, result.code) == (FAIL, MISMATCH)


def test_patching_json_in_the_candidate_does_not_change_what_the_runner_writes(tmp_path):
    patch = (
        "import json, json.encoder\n"
        "json.dumps = lambda *a, **k: 'garbage'\n"
        "json.encoder.JSONEncoder.encode = lambda self, o: 'garbage'\n"
        "json.encoder.encode_basestring_ascii = lambda s: '\"x\"'\n"
    )
    assert verdict(tmp_path, FIXED + patch).verdict == PASS
    (tmp_path / "again").mkdir()
    result = verdict(tmp_path / "again", BUGGY + patch)
    assert (result.verdict, result.code) == (FAIL, MISMATCH)


# AC6: a candidate that edits pytest itself, with correct values.
PYTEST_EDIT = {
    "src/_pytest/python.py": (
        "import sys\nfrom _pytest import reports as _r  # fix: report the skipped reason\n"
        "sys.modules['_pytest.reports'] = _r\n"
    ),
    "src/pytest_plugin.py": (
        "import sys\nsys.meta_path.insert(0, object())  # plugin fix\nsys.pycache_prefix = None\n"
    ),
}


def test_a_candidate_that_edits_pytest_itself_with_correct_values_passes(tmp_path):
    result = verdict(tmp_path, FIXED, extra=PYTEST_EDIT)
    assert result.verdict == PASS


def test_the_same_edit_is_refused_by_the_static_scan_on_the_pytest_form_path():
    """The before column: today's pytest-form path refuses the same edit by its added lines."""
    refused = {
        path: scan_added_lines(path, list(enumerate(text.splitlines(), 1)))
        for path, text in PYTEST_EDIT.items()
    }
    assert all(refused.values()), refused
