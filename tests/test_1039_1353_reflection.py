"""Static reflection check over the #1353 witness files, and runner row types.

Defence in depth, not a trust boundary: dynamic spellings (``vars(m)["sys"]``, computed
``getattr`` names, ``__import__``) are out of reach of an AST scan. The trust boundary is
PR-A's out-of-process broker, sealed pins and runner row binding.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections import Counter
from pathlib import Path

import pra1353_consume as consume
import pytest

FORBIDDEN = frozenset({"sys", "os", "builtins", "modules", "subprocess", "importlib"})
REFLECTIVE_CALLS = frozenset({"getattr", "setattr", "delattr", "hasattr"})
WITNESSES = (
    "pra1353_probe.py",
    "pra1353_extra.py",
    "pra1353_c10.py",
    "pra1353_receipt.py",
    "pra1353_launch.py",
)
# Every reviewed use, keyed by file, enclosing function and source text. A new use, or a
# known one moved to another function, changes the count and fails the real-file test.
KNOWN = Counter(
    {
        ("pra1353_extra.py", "load_c10", "sys.modules"): 1,
        ("pra1353_launch.py", "_c7_unprotected", "verify_cli._run"): 1,
        ("pra1353_launch.py", "_c8_rows", "sys.modules"): 1,
        # #1463: read-only listing of loaded module names, to prove the C10 control child never
        # imported cairnwork*; it neither assigns nor deletes any entry.
        ("pra1353_launch.py", "_c10_control", "sys.modules"): 1,
        ("pra1353_receipt.py", "_c8_mutations", "from cairnwork_attest.record import _digest"): 1,
    }
)


def _imported(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _hit(node: ast.AST, imported: set[str]) -> str | None:
    if isinstance(node, ast.Attribute):
        root = node.value
        while isinstance(root, ast.Attribute):
            root = root.value
        private = node.attr.startswith("_") and isinstance(root, ast.Name) and root.id in imported
        if node.attr in FORBIDDEN or node.attr.startswith("__") or private:
            return ast.unparse(node)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        args = node.args
        if node.func.id in REFLECTIVE_CALLS and len(args) >= 2:
            name = args[1].value if isinstance(args[1], ast.Constant) else None
            if isinstance(name, str) and (name in FORBIDDEN or name.startswith("_")):
                return ast.unparse(node)
    elif isinstance(node, ast.ImportFrom):
        bad = [a.name for a in node.names if a.name in FORBIDDEN or a.name.startswith("_")]
        if bad:
            return f"from {node.module} import {', '.join(bad)}"
    return None


def reflection_uses(source: str) -> list[tuple[str, str]]:
    """Return (enclosing function, source text) for each reflective reach in ``source``."""
    tree = ast.parse(source)
    imported = _imported(tree)
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        hit = _hit(node, imported)
        if hit is not None:
            found.append((scope, hit))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, "<module>")
    return found


def _real_uses(replace: dict[str, str] | None = None) -> Counter[tuple[str, str, str]]:
    folder = Path(__file__).parent
    uses: Counter[tuple[str, str, str]] = Counter()
    for name in WITNESSES:
        source = (replace or {}).get(name) or (folder / name).read_text()
        uses.update((name, scope, text) for scope, text in reflection_uses(source))
    return uses


def test_real_witness_files_reach_only_the_reviewed_reflection():
    assert _real_uses() == KNOWN


@pytest.mark.parametrize(
    "snippet",
    [
        'verify_cli.sys.modules["pra1353_probe"] = fake',
        "subprocess.sys.modules.clear()",
        "bh.subprocess.sys.exit(0)",
        "probe.os.environ.clear()",
        "json.builtins.print",
        "cairnwork.importlib.reload(cairnwork)",
        "value = verify_cli.__dict__",
        "row.__globals__['_CACHE'] = {}",
        "type(result).__eq__",
        "verify_cli._run_protected()",
        'getattr(verify_cli, "sys")',
        'setattr(verify_cli, "_run", stub)',
        'hasattr(row, "__wrapped__")',
        "from sys import modules",
        "from cairnwork.verify_cli import _run",
    ],
)
def test_reflective_reach_is_found(snippet):
    prelude = "import json, subprocess\nimport cairnwork.blackbox as bh\n"
    prelude += "from cairnwork import verify_cli, probe\nfrom pra1353_probe import row\n"
    assert reflection_uses(prelude + f"def f():\n    {snippet}\n") != []


@pytest.mark.parametrize(
    "snippet",
    [
        "sys.platform",
        'os.environ["HOME"]',
        "subprocess.run(['true'], check=False)",
        'importlib.util.spec_from_file_location("x", path)',
        "raw.status",
        'getattr(raw, "reason", "")',
        "local._cache",
    ],
)
def test_ordinary_module_use_is_not_a_finding(snippet):
    prelude = "import importlib.util, os, subprocess, sys\n"
    assert reflection_uses(prelude + f"def f(raw, local, path):\n    {snippet}\n") == []


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        (
            "pra1353_launch.py",
            '        value = verify_cli._run(Intake(Path("/no-such-intake")), namespace)',
            '        verify_cli.sys.modules["pra1353_probe"] = None\n'
            '        value = verify_cli._run(Intake(Path("/no-such-intake")), namespace)',
        ),
        (
            "pra1353_launch.py",
            "def _c8_rows(args: dict[str, Any]) -> dict[str, Any]:\n",
            "def _c8_rows(args: dict[str, Any]) -> dict[str, Any]:\n"
            "    verify_cli = None\n    verify_cli._run\n",
        ),
        ("pra1353_probe.py", "def row(", "sys.modules.pop('x', None)\n\n\ndef row("),
    ],
    ids=["extra-module-chain", "known-text-in-another-function", "probe-sys-modules"],
)
def test_a_new_use_in_a_real_witness_file_is_refused(name, old, new):
    source = (Path(__file__).parent / name).read_text()
    assert old in source
    assert _real_uses({name: source.replace(old, new, 1)}) != KNOWN


def _rows(seqs):
    return [{"seq": seq} for seq in seqs]


@pytest.mark.parametrize(
    "report",
    [
        [{"seq": 0}],
        "rows",
        {"rows": [*_rows(range(55)), "row"]},
        {"rows": [{"seq": True}, *_rows(range(2, 56)), {"seq": 0}]},
        {"rows": [*_rows(range(55)), {"seq": 55.0}]},
        {"rows": [*_rows(range(55)), {"seq": "55"}]},
        {"rows": [*_rows(range(55)), {}]},
    ],
    ids=[
        "list-record",
        "string-record",
        "string-row",
        "bool-seq",
        "float-seq",
        "str-seq",
        "no-seq",
    ],
)
def test_runner_record_of_the_wrong_type_is_refused(report):
    completed = subprocess.CompletedProcess([], 0, json.dumps(report), "")
    with pytest.raises(ValueError):
        consume.decode_report(completed)


def test_exact_integer_rows_still_pass():
    report = {"rows": _rows(range(56))}
    completed = subprocess.CompletedProcess([], 0, json.dumps(report), "")
    assert consume.decode_report(completed) == report
