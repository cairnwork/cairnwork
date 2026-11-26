"""#1820 third review: a changed file that reaches an archive only through state another file sets.

The first file of the directory cannot be collected; the commit mends it, edits the file that reads
the state, and changes one archive. Four kinds of state (environment value, path entry, module
attribute, a hook that runs at configure time), each with a twin that changes another archive;
main refuses both, so this head must too (never wider). Sandbox rows (CAIRNWORK_REQUIRE_SANDBOX=1).
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
import pytest
from conftest_scenarios import project  # noqa: F401  (the fixture)
from test_runtime_reach_tree_e2e import BROKEN, JOINED, OK, go, refused

pytestmark = pytest.mark.sandbox

ZIPS = {"tests/m/data/lib.zip": "z", "tests/m/data/other.zip": "o"}
HEAD = "import os, sys, pathlib, builtins\n"
STATE = {  # kind: (the sibling conftest, the condition the reader checks)
    "env": ("import os\nos.environ['USE_VEND'] = '1'\n", "os.environ.get('USE_VEND')"),
    "path": (
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / 'st'))\n",
        "any(p.endswith('/st') for p in sys.path)",
    ),
    "attr": ("import builtins\nbuiltins.USE_VEND = True\n", "getattr(builtins, 'USE_VEND', 0)"),
    "hook": (
        "import os\n\n\ndef pytest_configure(config):\n    os.environ['USE_VEND'] = '1'\n",
        "os.environ.get('USE_VEND')",
    ),
}


def _reader(cond):
    return HEAD + f"if {cond}:\n    " + JOINED + "\n\n" + OK


@pytest.mark.parametrize("bad", [True, False], ids=["bad", "twin"])
@pytest.mark.parametrize("kind", list(STATE))
def test_a_changed_reader_reaches_the_archive_through_state_a_sibling_sets(
    project, tmp_path, kind, bad
):
    conf, cond = STATE[kind]
    reader = _reader(cond)
    base = {
        "tests/a/conftest.py": conf,
        "tests/a/test_a.py": OK,
        "tests/m/test_00.py": BROKEN,
        "tests/m/test_01.py": reader,
        "tests/m/test_02.py": OK,
        **ZIPS,
    }
    files = {"tests/m/test_00.py": OK, "tests/m/test_01.py": reader + "# e\n"}
    files["tests/m/data/lib.zip" if bad else "tests/m/data/other.zip"] = "changed"
    out = go(project, tmp_path, base, files, extra=("tests/m/test_00.py", "tests/m/test_01.py"))
    refused(out)  # the twin is refused on main too (a base file that cannot be collected): pinned
