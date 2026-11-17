"""Pinned round-ten closed-position probes, inserted after the oracle call."""

from pathlib import Path

ANCHOR = "    result = verify(root, state, base, candidate)\n"
SINK = 'def sink(items):\n    items[0] = "VERIFIED"\n'
EXTRA = Path(__file__).with_name("_w1353_extra.py").read_text(encoding="utf-8")

ROUND_TEN_BLOCK = {
    "r1_alias_store": 'alias = result\nalias["status"] = "VERIFIED"',
    "r1_alias_update": 'alias = result\nalias.update(status="VERIFIED")',
    "r1_list_box": 'box = [result]\nbox[0]["status"] = "VERIFIED"',
    "r1_walrus": '(alias := result)["status"] = "VERIFIED"',
    "r1_ior": 'alias = result\nalias |= {"status": "VERIFIED"}',
    "r1_module_attribute": "m = verify_cli\nm.SOME_GLOBAL = None",
    "r1_blackbox_attribute": "blackbox.parse = json.loads",
    "r2_boolop": 'alias = result or {}\nalias["status"] = "VERIFIED"',
    "r2_ifexp": 'alias = result if result else {}\nalias["status"] = "VERIFIED"',
    "r2_for_list": 'for a in [result]:\n    a["status"] = "VERIFIED"',
    "r2_double_store": 'alias = {}\nalias = result\nalias["status"] = "VERIFIED"',
    "r2_helper_call": 'def h(d):\n    d["status"] = "VERIFIED"\nh(result)',
    "r2_subobject": 'sub = result["verification"]\nsub["status"] = "VERIFIED"',
    "r2_append": 'box = []\nbox.append(result)\nbox[0]["status"] = "VERIFIED"',
    "r2_comprehension": 'box = [r for r in [result]]\nbox[0]["status"] = "VERIFIED"',
    "r3_walrus_discard": '_ = (alias := result)\nalias["status"] = "VERIFIED"',
    "r3_walrus_test": 'if (alias := result):\n    alias["status"] = "VERIFIED"',
    "r3_walrus_rebind": 'alias = result\n_ = (alias := result)\nalias["status"] = "VERIFIED"',
    "r3_box_extract": 'box = [result]\nalias = box[0]\nalias["status"] = "VERIFIED"',
    "r3_inline_extract": 'alias = [result][0]\nalias["status"] = "VERIFIED"',
    "r3_box_ior": 'box = [result]\nbox[0] |= {"status": "VERIFIED"}',
    "codex_for_tuple": 'for a in (result,):\n    a["status"] = "VERIFIED"',
    "codex_with_target": 'with nullcontext(result) as a:\n    a["status"] = "VERIFIED"',
    "extra_delete": 'del result["status"]',
    "extra_setitem": 'result.__setitem__("status", "VERIFIED")',
    "extra_setattr": 'setattr(result, "x", 1)',
    "extra_dict_update": 'dict.update(result, status="VERIFIED")',
    "extra_augassign": 'result["n"] += 1',
    "extra_get_derived": 'x = result.get("verification")\nx["status"] = "VERIFIED"',
    "extra_print": "print(result)",
    "extra_kwargs": "f(**result)",
    "r10_walrus_in_row_arg": (
        'row("C9", "x", "verify_cli run", result, "VERIFIED", (alias := result) and "", "")\n'
        'alias["status"] = "VERIFIED"'
    ),
    "r10_list_in_row_arg": (
        'row("C9", "x", "verify_cli run", result, "VERIFIED", "", str([result]))'
    ),
    "r10_startswith_other_method": 'flag = result.get("status").endswith("D")',
    "alias_subscript_augassign": 'box = [result]\nbox[0] |= {"status": "VERIFIED"}',
    # Values computed at an expression sink stay raw (Codex round-10 family).
    "r11_convert_str": SINK + "sink([str(result)])",
    "r11_convert_int": SINK + "sink([int(result)])",
    "r11_convert_float": SINK + "sink([float(result)])",
    "r11_convert_bool": SINK + "sink([bool(result)])",
    "r11_convert_len": SINK + "sink([len(result)])",
    "r11_convert_repr": SINK + "sink([repr(result)])",
    "r11_convert_blackbox_result": SINK + "sink([blackbox_result(result)])",
    "r11_compare_escape": SINK + "sink([result == {}])",
    "r11_not_escape": SINK + "sink([not result])",
    "r11_fstring_escape": SINK + 'sink([f"{result}"])',
    "r11_startswith_escape": SINK + 'sink([result.get("status", "").startswith("V")])',
    "r11_derived_convert_escape": SINK + "text = str(result)\nsink([text])",
    "r11_nested_convert_escape": SINK + "sink([str(len(result))])",
    "r11_convert_call_arg": "print(str(result))",
    "r11_shadowed_builtin": (
        'def str(d):\n    d["status"] = "VERIFIED"\n    return ""\ntext = str(result)'
    ),
}

ALIAS_GRAMMAR_BLOCK = {
    "legacy_for_target": 'for alias in (result,):\n    alias["status"] = "VERIFIED"',
    "legacy_with_target": 'with result as alias:\n    alias["status"] = "VERIFIED"',
    "legacy_boolop": 'alias = result or {}\nalias["status"] = "VERIFIED"',
    "legacy_ifexp": 'alias = result if True else None\nalias["status"] = "VERIFIED"',
    "legacy_extract": 'box = [result]\nalias = box[0]\nalias["status"] = "VERIFIED"',
}

ROUND_TEN_MUTATION_IDS = tuple(sorted(ROUND_TEN_BLOCK))
LEGACY_CAUGHT_IDS = {
    "extra_augassign",
    "extra_delete",
    "extra_dict_update",
    "extra_get_derived",
    "extra_print",
    "extra_setattr",
    "extra_setitem",
    "r1_alias_store",
    "r1_alias_update",
    "r1_blackbox_attribute",
    "r1_module_attribute",
    "r1_walrus",
    "r2_boolop",
    "r2_ifexp",
    "r2_subobject",
    "r11_convert_call_arg",
    "r11_convert_float",
    "r11_convert_int",
    "r11_convert_len",
    "r11_convert_repr",
    "r11_nested_convert_escape",
}


def source_for(snippet: str) -> str:
    assert ANCHOR in EXTRA
    inserted = "".join("    " + line + "\n" for line in snippet.splitlines())
    return EXTRA.replace(ANCHOR, ANCHOR + inserted, 1)
