"""cw#1463 C10: the outside-runner control must come from a child that never imports cairnwork."""

# ruff: noqa: E501

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pra1353_c10 as c10
import pytest

TESTS = Path(__file__).resolve().parent
SITE = Path(pytest.__file__).resolve().parent.parent
# Forges "outside" in any child that imports the product package and prints a result.
FORGE = (
    "import json as _j, sys as _s\n"
    "_o = _j.dumps\n"
    "_j.dumps = lambda v, *a, **k: _o({**v, 'outside': 5} if isinstance(v, dict) and 'raw' in v "
    "else v, *a, **k)\n"
)
# The pre-#1463 child: control computed in the same process that imported product code.
OLD_CHILD = """import json,sys,types
roots=json.loads(sys.argv[1]); report=sys.argv[2]; sys.path[:0]=roots
from cairnwork.blackbox_runner import MODULE
ns={}; exec("import sys\\ndef add(a,b):\\n if "+repr(MODULE)+" in sys.modules: return a+b\\n return a-b\\n",ns)
print(json.dumps({"raw":{"ok":ns["add"](2,3)},"outside":ns["add"](2,3)}))
"""


def _package(tmp_path):
    pkg = tmp_path / "cairnwork"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(FORGE)
    (pkg / "blackbox_runner.py").write_text(
        'MODULE = "_cairnwork_blackbox_runner"\n_RUNNER_SOURCE = ""\n'
    )
    return [str(tmp_path), str(TESTS), str(SITE), *[p for p in sys.path if p]]


def _run_child(source, roots, n=1):
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", source, json.dumps(roots), "/dev/null", str(n)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return done


def _control_child(n):
    boot = (
        f"import sys; sys.path.insert(0,{str(TESTS)!r})\n"
        "import pra1353_launch; pra1353_launch.child_main()\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", boot],
        input=json.dumps({"route": "c10_control", "args": {"n": n}}),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize(("n", "honest"), [(1, -1), (2, 5), (3, -1)])
def test_c10_control_is_honest_and_loads_no_product_module(n, honest):
    result = _control_child(n)
    assert result["outside"] == honest
    assert result["cairnwork_modules"] == []


def test_c10_product_child_value_is_not_the_control(tmp_path):
    """Must block: a forging product __init__ cannot change the control that c10_rows uses."""
    roots = _package(tmp_path)
    child = _run_child(c10._child_source(), roots)
    assert child.returncode == 0, child.stderr[-300:]
    # The product child may still print a forged "outside"; c10_rows never reads it.
    assert "outside" not in c10._child_source().split("print(json.dumps(")[1].split("\n")[0]
    assert _control_child(1)["outside"] == -1


def test_c10_mutation_old_order_is_forgeable(tmp_path):
    """Mutation: computing the control after import cairnwork lets product code forge it."""
    roots = _package(tmp_path)
    old = _run_child(OLD_CHILD, roots)
    assert old.returncode == 0, old.stderr[-300:]
    assert json.loads(old.stdout)["outside"] == 5  # forged; honest value is -1
    assert _control_child(1)["outside"] == -1


def test_c10_control_rejects_a_child_that_imported_product_code(monkeypatch):
    monkeypatch.setattr(
        c10, "launch_route", lambda *a, **k: {"outside": 5, "cairnwork_modules": ["cairnwork"]}
    )
    with pytest.raises(RuntimeError, match="imported product code"):
        c10._control(1)


def _outsides(src, monkeypatch, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    monkeypatch.setenv("PRA1353_CHILD_REPORT_DIR", str(reports))
    stdlib = [p for p in sys.path if p and "site-packages" not in p and "/lib/python3" in p]
    roots = [*stdlib, str(src), str(TESTS), str(SITE)]
    monkeypatch.setenv("PRA1353_IMPORT_ROOTS", json.dumps(roots))
    return [row["raw_return"]["outside"] for row in c10.c10_rows()]


def test_c10_rows_end_to_end_ignore_a_forging_product_init(monkeypatch, tmp_path):
    """Must block, through c10_rows: a src-only forgery of `outside` must not move the rows."""
    root = TESTS.parent
    forged = tmp_path / "src"
    shutil.copytree(root / "src", forged, ignore=shutil.ignore_patterns("__pycache__"))
    init = forged / "cairnwork" / "__init__.py"
    init.write_text(init.read_text() + "\n" + FORGE)
    assert _outsides(root / "src", monkeypatch, tmp_path) == [-1, 5, -1]
    assert _outsides(forged, monkeypatch, tmp_path) == [-1, 5, -1]
