"""Shared guard assertions for the measured witness tests."""

from __future__ import annotations

import ast

from tests._w1353_allow import check as allow_check

LEGACY_MUTATIONS = (
    (
        "\n".join(
            [
                "import wrong",
                "from _w1353_probe import row",
                'row("C9","t","r",wrong.verify(1),"x","y")',
            ]
        ),
        "module",
    ),
    (
        "\n".join(
            [
                "from cairnwork.blackbox_judge import judge_run",
                "from _w1353_probe import row",
                "r=judge_run(1)",
                'row("C9","t","r",r.get("status","PASS"),"x","y")',
            ]
        ),
        "get",
    ),
    ("def f():\n x=1\n def g():\n  nonlocal x\n  pass", "nonlocal"),
    ("def f():\n import json\n return json", "nested_import"),
    ("import importlib", "import_allow"),
    ("def g(m): return m.row(1)", "row_attribute"),
)


def round_five_block(case_id, source):
    path = {
        "a6_run_path_row_key": "tests/_w1353_c10.py",
        "a6_run_path_iteration": "tests/_w1353_c10.py",
        "a6_run_path_return": "tests/_w1353_c10.py",
        "a6_runpy_from_import": "tests/_w1353_c10.py",
        "a10_sys_modules_escape": "tests/_w1353_receipt.py",
        "a10_sys_modules_get": "tests/_w1353_receipt.py",
        "a15_unpinned_observed_sink": "tests/_w1353_probe.py",
    }.get(case_id, "witness.py")
    assert allow_check(source, path), case_id


def round_five_pass(source):
    assert not allow_check(source)


def round_five_mutation(case_id, source, mutation, monkeypatch):
    import tests._w1353_allow_imports as imports
    import tests._w1353_allow_rules as rules

    path = {
        "a10_sys_modules_escape": "tests/_w1353_receipt.py",
        "a10_sys_modules_get": "tests/_w1353_receipt.py",
        "a15_unpinned_observed_sink": "tests/_w1353_probe.py",
    }.get(case_id, "witness.py")
    assert allow_check(source, path)
    if mutation == "builtin":
        monkeypatch.setattr(imports, "SAFE_BUILTINS", imports.SAFE_BUILTINS | {"getattr"})
    elif mutation == "modules":
        key = ("_w1353_receipt", "_c8_mutations", "sys")
        monkeypatch.setitem(imports.ADAPTER_ATTRIBUTES, key, {"platform", "modules"})
    else:
        monkeypatch.setitem(rules.SINK_PINS, "_w1353_probe.row", None)
    assert not allow_check(source, path), case_id


def legacy_mutation(source, mutation, monkeypatch):
    import tests._w1353_allow as guard
    import tests._w1353_allow_rules as rules

    baseline_imports = set().union(*rules.ALLOWED_IMPORTS.values())
    assert guard.check(source)
    if mutation == "module":
        monkeypatch.setattr(guard, "ORACLES", guard.ORACLES | {("wrong", "verify")})
        monkeypatch.setattr(rules, "ORACLES", rules.ORACLES | {("wrong", "verify")})
        monkeypatch.setitem(rules.ALLOWED_IMPORTS, "witness", baseline_imports | {"wrong"})
    elif mutation == "get":
        original = guard._arg_ok

        def permissive(node, index, *args):
            if (
                index == 3
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in args[0]
            ):
                return True
            return original(node, index, *args)

        monkeypatch.setattr(rules, "_arg_ok", permissive)
    elif mutation == "nested_import":
        monkeypatch.setattr(rules, "BLOCK_NESTED_IMPORTS", False)
    elif mutation == "import_allow":
        monkeypatch.setitem(rules.ALLOWED_IMPORTS, "witness", baseline_imports | {"importlib"})
    elif mutation == "row_attribute":
        monkeypatch.setattr(rules, "BLOCK_ROW_ATTRIBUTES", False)
    else:
        monkeypatch.setattr(rules, "BLOCKED_NODES", rules.BLOCKED_NODES - {ast.Nonlocal})
    assert not guard.check(source)
