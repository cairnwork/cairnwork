"""#1809 v2: the verdict is never wider than main on three shapes where main refuses.

A same-basename clash, a dependent file past the file cap, and a spent run budget; each with a twin
that must pass. Sandbox rows (CAIRNWORK_REQUIRE_SANDBOX=1).
"""

# ruff: noqa: F811

import pytest
from conftest_scenarios import project  # noqa: F401
from test_runtime_reach_tree_e2e import BROKEN, JOINED, OK, accepted, go, refused

pytestmark = pytest.mark.sandbox

ZIPS = {"tests/m/data/lib.zip": "z", "tests/m/data/other.zip": "o"}
HEAD = "import os, sys, pathlib, builtins\n"
CONF = "import os\nos.environ['USE_VEND'] = '1'\n"
READER = HEAD + "if os.environ.get('USE_VEND'):\n    " + JOINED + "\n\n" + OK


@pytest.mark.parametrize("bad", [True, False], ids=["bad", "twin"])
def test_clash_forces_rerun_with_no_lone_broken_file(project, tmp_path, bad):
    """First run fails from an interaction (same basename twice), no file fails alone."""
    base = {
        "tests/a/conftest.py": CONF,
        "tests/a/test_a.py": OK,
        "tests/m/test_01.py": READER,
        "tests/m/test_dup.py": OK,
        "tests/x/test_dup.py": OK,
        **ZIPS,
    }
    files = {"tests/m/test_01.py": READER + "# e\n", "tests/m/test_dup.py": OK + "# e\n"}
    files["tests/m/data/lib.zip" if bad else "tests/m/data/other.zip"] = "changed"
    out = go(project, tmp_path, base, files, extra=("tests/m/test_01.py", "tests/m/test_dup.py"))
    # The twin is refused on main too (the clash leaves the base run incomplete): pinned, not wider.
    refused(out)


VEND = "import sys, pathlib\n" + JOINED + "X = 1\n"
USES_VEND = "import vend\n\n\ndef test_vend():\n    assert vend.X == 1\n"


@pytest.mark.parametrize("bad", [True, False], ids=["bad", "twin"])
def test_budget_used_up_ignores_a_healthy_tree_file_past_the_cap(project, tmp_path, bad):
    base = {f"tests/m/test_{i:02d}.py": OK for i in range(20)}
    base["tests/m/test_00.py"] = BROKEN
    base["tests/m/test_20.py"] = USES_VEND  # no signal word; main's rerun collects it
    base.update({f"tests/m/zb_{i:02d}.py": "import no_such_mod_zb\n" for i in range(11)})
    base.update({"tests/m/vend.py": VEND, **ZIPS})
    files = {
        p: (OK if "/zb_" in p or p.endswith("test_00.py") else base[p]) + "# e\n"
        for p in base
        if p.endswith(".py") and p != "tests/m/vend.py"
    }
    files["tests/m/data/lib.zip" if bad else "tests/m/data/other.zip"] = "changed"
    extra = tuple(sorted(p for p in files if p.endswith(".py")))
    out = go(project, tmp_path, base, files, extra=extra)
    print("VERDICT", bad, out.status, out.reason)
    (refused if bad else accepted)(out)


DEP_ZIPS = {"tests/a/data/lib.zip": "z", "tests/a/data/other.zip": "o"}
DEP_CONF = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / 'lib'))\n"
VENDLIB = "import sys, pathlib\n" + JOINED.replace("parent /", "parent.parent /") + "X = 1\n"
USES = "import vendlib\n\n\ndef test_vend():\n    assert vendlib.X == 1\n"


@pytest.mark.parametrize("bad", [True, False], ids=["bad", "twin"])
def test_dependent_file_past_the_cap(project, tmp_path, bad):
    base = {f"tests/m/test_{i:02d}.py": OK for i in range(20)}
    base["tests/m/test_00.py"] = BROKEN
    base["tests/m/test_20.py"] = USES
    base.update(
        {
            "tests/a/conftest.py": DEP_CONF,
            "tests/a/test_a.py": OK,
            "tests/a/lib/vendlib.py": VENDLIB,
            **DEP_ZIPS,
        }
    )
    files = {
        f"tests/m/test_{i:02d}.py": (OK if i in (0, 20) else base[f"tests/m/test_{i:02d}.py"])
        + "# e\n"
        for i in range(21)
    }
    files["tests/a/data/lib.zip" if bad else "tests/a/data/other.zip"] = "changed"
    out = go(
        project, tmp_path, base, files, extra=tuple(sorted(p for p in files if p.endswith(".py")))
    )
    (refused if bad else accepted)(out)
