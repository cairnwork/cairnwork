"""Consumer side of the isolated #1353 runner boundary."""

# Evasion source strings below are intentionally preserved as executable snippets.
# ruff: noqa: E501

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_NO_SANDBOX_BASE = "UNCOMPARABLE: BASE_ENV_FAILED: unchanged base: base-probe-failed (no-sandbox: no usable sandbox backend, so nothing ran)"
_ACCOUNT_GAP = (
    "[blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 1/1]]"
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "pra1353_runner.py"
SEQUENCES = set(range(56))


def check_pins(pins: dict[str, str]) -> None:
    for relative, expected in pins.items():
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"pin mismatch: {relative}")


def decode_report(completed: Any) -> dict[str, Any]:
    if completed.returncode != 0:
        raise ValueError(f"runner exited {completed.returncode}: {completed.stderr[-400:]}")
    lines = completed.stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise ValueError("runner did not produce exactly one JSON record")
    try:
        report = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("runner output was not JSON") from exc
    validate_rows(report.get("rows"))
    return report


def validate_rows(rows: Any) -> None:
    if not isinstance(rows, list) or len(rows) != 56:
        raise ValueError("runner row count mismatch")
    seqs = [row.get("seq") for row in rows if isinstance(row, dict)]
    if len(seqs) != len(rows) or len(seqs) != 56 or set(seqs) != SEQUENCES:
        raise ValueError("runner sequence set mismatch")
    if len(set(seqs)) != 56:
        raise ValueError("runner duplicate sequence")


def launch(
    pins: dict[str, str],
    *,
    runner: Path = RUNNER,
    run=subprocess.run,
    extra_env: dict[str, str] | None = None,
    cwd_override: Path | None = None,
):
    check_pins(pins)
    with tempfile.TemporaryDirectory(prefix="cw1353-consumer-") as tmp:
        cwd = Path(tmp) / "empty"
        cwd.mkdir()
        if cwd_override is not None:
            cwd = cwd_override
        env = {
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            "HOME": tmp,
            "TMPDIR": tmp,
            "PYTHONHASHSEED": "0",
            "LANG": "C.UTF-8",
        }
        env.update(extra_env or {})
        completed = run(
            [sys.executable, "-I", "-B", str(runner)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    report = decode_report(completed)
    allowed = {_module_name(name) for name in pins if name.startswith("tests/")}
    if "tests/pra1353_runner.py" in pins:
        allowed.add("__main__")
    loaded = set(report.get("loaded_test_modules", []))
    if any(name.startswith("_w1353_") for name in loaded) or not loaded <= allowed:
        raise ValueError(f"runner loaded unpinned test modules: {sorted(loaded - allowed)}")
    return report


def _module_name(relative):
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if len(parts) == 2:
        return parts[-1]
    return ".".join(parts)


def sandbox_available():
    from cairnwork_sandbox import select

    return sys.platform == "darwin" and select() is not None


def c2_unavailable(rows):
    return [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]


def blackbox_classification(status, reason):
    from pra1353_derive import blackbox_classification as classify

    return classify(status, reason)


def evasion_sources():
    return {
        "C1": [
            "import importlib; importlib.import_module('_pyt' + 'est.reports'); import pytest; pytest.Function.runtest=lambda self: None",
            "import sys, importlib; vars(sys)['pycache_'+'prefix'] = '/tmp/cw1353-cache'; loader=vars(importlib)['import_module']; loader('_pyt'+'est.reports'); import pytest; pytest.Function.runtest=lambda self: None",
            "import importlib; python=importlib.import_module('_pyt'+'est.python'); setattr(python.Function, 'runtest', lambda self: None)",
        ],
        "C2": [
            "import pytest\nfor target in (pytest.Function,): setattr(target, 'runtest', lambda self: None)",
            "import pytest\nF = pytest.Function\nF.runtest = lambda self: None",
        ],
        "C3": [
            "from pathlib import Path\ntarget=Path('tests/test_calc.py'); print('from calc import add\\ndef test_deferred(): assert add(2,3)==5', file=target.open('w'))",
            "from functools import partial\nfrom pathlib import Path\ntarget=Path('tests/test_calc.py'); writer=partial(open, target, 'w'); writer().write('from calc import add\\ndef test_deferred(): assert add(2,3)==5')\nimport os\nos.sys.path.insert(0, 'src')",
        ],
        "C4": [
            "from pathlib import Path\nimport importlib\nPath('src/pkg').mkdir(); Path('src/pkg/__init__.py').write_text(\"from pathlib import Path\\nPath('tests/test_calc.py').write_text('from calc import add\\\\ndef test_deferred(): assert add(2,3)==5')\\n\")\nname='pkg'; importlib.import_module(name)",
            "from pathlib import Path\nimport importlib\nPath('src/pkg').mkdir(); Path('src/pkg/__init__.py').write_text(\"from pathlib import Path\\nPath('tests/test_calc.py').write_text('from calc import add\\\\ndef test_deferred(): assert add(2,3)==5')\\n\")\nPath('src/pkg/mod.py').write_text('VALUE=2')\nname='pkg.mod'; module=importlib.import_module(name)",
        ],
    }


def _literal_observations(source):
    tree = ast.parse(source)

    def literal_tree(node):
        return isinstance(node, ast.Constant) or (
            isinstance(node, (ast.List, ast.Tuple))
            and all(literal_tree(item) for item in node.elts)
        )

    def allowed(node):
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and isinstance(node.func.value, ast.Constant)
                and node.args
                and literal_tree(node.args[0])
            ):
                return False
            return True
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            return True
        return (
            isinstance(node, ast.Name)
            and bool(bindings.get(node.id))
            and all(
                isinstance(value, (ast.Call, ast.Attribute, ast.Subscript))
                for value in bindings[node.id]
            )
        )

    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name):
                    bindings.setdefault(name.id, []).append(node.value)

    row_names = {"row"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            row_names.update(
                alias.asname or alias.name
                for alias in node.names
                if node.module == "_w1353_probe" and alias.name == "row"
            )

    flagged = []
    for node in ast.walk(tree):
        is_row_call = isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id in row_names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "row")
        )
        if is_row_call and isinstance(node, ast.Call):
            raw = (
                node.args[3]
                if len(node.args) > 3
                else next((kw.value for kw in node.keywords if kw.arg == "raw"), None)
            )
            if raw is None or not allowed(raw):
                flagged.append(node.lineno)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(name, ast.Name) and name.id == "observed"
                for target in targets
                for name in ast.walk(target)
            ):
                if not allowed(node.value):
                    flagged.append(node.lineno)
    return flagged
