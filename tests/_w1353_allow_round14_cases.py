"""Round fourteen row-registry checker probes."""

STATIC_BLOCK = {
    "cache_extra_fabricated": 'def h():\n    _CACHE["extra_rows"] = [dict(observed="x")] ',
    "cache_core_empty": 'def h():\n    _CACHE["core_rows"] = []',
    "dict_row_keyword": (
        'def h():\n    return dict(row("C1", "x", "r", "FAILED", "x", "y"), observed="VERIFIED")'
    ),
    "dict_observed_keyword": 'def h():\n    return dict(observed="VERIFIED")',
    "dict_zip_keys": (
        '_KEYS = ("observed", "raw_return")\ndef h():\n    return dict(zip(_KEYS, vals))'
    ),
    "empty_dict_compare": "def h():\n    if result == dict():\n        return True",
    "unsafe_raw_order": 'def h():\n    if result.get("status") < "x":\n        return True',
    "top_level_cache": '_CACHE["extra_rows"] = []',
    "top_level_call": "_p()",
    "registry_import_extra": (
        "from tests._w1353_allow_rowreg import issue\ndef h():\n    return issue({})"
    ),
    "registry_call_extra": "def h():\n    return issue({})",
    "probe_issue_alias": (
        "from _w1353_probe import issue as mk\ndef h():\n    x = {}\n"
        '    x["observed"] = "VERIFIED"\n    return mk(x)'
    ),
    "tests_package_import": "from tests import _w1353_allow\ndef h():\n    return 1",
}

STATIC_PASS = {
    "append_normal_row": (
        'def h(rows):\n    rows.append(_normal_row("C1", 1, root, state, "b", "c"))'
    ),
    "append_blackbox_row": (
        'def h(rows):\n    rows.append(_blackbox_row("C1", 1, root, state, "b", "c", decl))'
    ),
    "append_row": (
        'def h(rows):\n    result = verify("root")\n'
        '    rows.append(row("C1", "x", "r", result, "FAILED", "REFUSED"))'
    ),
    "row_aggregation": (
        "def h():\n    result = _c6() + _c7(st, d) + _c9(d) + module.c10_rows() "
        "+ receipt.c8_rows(parent)"
    ),
    "configuration_dict": 'def h():\n    config = {"salt": "s", "checks": ["a"]}',
    "simple_namespace": "def _c7():\n    config = types.SimpleNamespace(require_protection=True)",
}

RULE_ONLY = {
    "empty_dict_compare": {"row rules"},
    "unsafe_raw_order": {"syntax whitelist"},
    "top_level_cache": {"row rules", "syntax whitelist"},
    "top_level_call": {"syntax whitelist"},
}

POST_ROW_TAMPERS = {
    "out_store": lambda out, rows: out.__setitem__("observed", "VERIFIED"),
    "out_update": lambda out, rows: out.update(observed="VERIFIED"),
    "out_setdefault": lambda out, rows: out.setdefault("observed", "VERIFIED"),
    "nested_store": lambda out, rows: out["raw_return"].__setitem__("status", "VERIFIED"),
    "last_store": lambda out, rows: rows[-1].__setitem__("observed", "VERIFIED"),
    "last_update": lambda out, rows: rows[-1].update(observed="VERIFIED"),
    "first_raw_return": lambda out, rows: rows[0].__setitem__("raw_return", {"status": "VERIFIED"}),
}

FABRICATED = {"dict_row", "dict_keyword", "dict_zip"}
