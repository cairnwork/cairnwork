"""Round-thirteen syntax whitelist probes."""

SYNTAX_BLOCK = {
    "nested_store": 'def h():\n    result["status"] = "VERIFIED"',
    "nested_update": 'def h():\n    result.update(status="VERIFIED")',
    "nested_setdefault": 'def h():\n    result.setdefault("status", "VERIFIED")',
    "nested_delete": 'def h():\n    del result["status"]',
    "nested_augassign": 'def h():\n    result["n"] += 1',
    "nested_return_store": 'def h():\n    return result\nr = h()\nr["status"] = "VERIFIED"',
    "nested_subobject_store": (
        'sub = result["verification"]\ndef h():\n    sub["status"] = "VERIFIED"'
    ),
    "captured_sub": 'sub = result["verification"]\ndef h():\n    sub.update(status="VERIFIED")',
    "captured_d": 'd = result.get("details")\ndef h():\n    d.setdefault("x", 1)',
    "captured_s": 's = result.get("status")\ndef h():\n    result["status"] = s',
    "async_def": 'async def h():\n    result["status"] = "VERIFIED"',
    "nested_class_method": 'class C:\n    def h(self):\n        result["status"] = "VERIFIED"',
    "two_levels": 'def h():\n    def g():\n        result.update(status="VERIFIED")\n    g()',
    "default": "def _f(x=result):\n    return x",
    "kwonly_default": "def _f(*, x=result):\n    return x",
    "arg_annotation": "def _f(x: result):\n    return None",
    "return_annotation": "def _f() -> result:\n    return None",
    "class_kwargs": "class _C(**result):\n    pass",
    "class_keyword": "class _K(dict, x=result):\n    pass",
    "eq_dunder": """class _Eq:
    def __eq__(self, other):
        other["status"] = "VERIFIED"
        return True
assert result == _Eq()""",
    "ne_dunder": """class _Eq:
    def __ne__(self, other):
        other.update(status="VERIFIED")
        return True
assert result != _Eq()""",
    "gt_dunder": """class _Eq:
    def __gt__(self, other):
        other.setdefault("status", "VERIFIED")
        return True
assert result < _Eq()""",
    "contains_dunder": """class _Box:
    def __contains__(self, other):
        other["status"] = "VERIFIED"
        return True
assert result in _Box()""",
    "not_contains_dunder": """class _Box:
    def __contains__(self, other):
        other["status"] = "VERIFIED"
        return True
assert result not in _Box()""",
    "while_compare": """class _Eq:
    def __eq__(self, other):
        other["status"] = "VERIFIED"
        return False
while result == _Eq():
    break""",
    "derived_compare": """class _Eq:
    def __eq__(self, other):
        other["status"] = "VERIFIED"
        return True
assert result.get("verification") == _Eq()""",
    "chained_compare": """class _Eq:
    def __eq__(self, other):
        other["status"] = "VERIFIED"
        return True
assert "x" == result == _Eq()""",
    "derived_name_compare": """class _Eq:
    def __eq__(self, other):
        other["status"] = "VERIFIED"
        return True
d = result.get("details")
assert d == _Eq()""",
    "two_raw_compare": "m = object()\nassert result == m",
    "two_raw_expressions": 'status = result.get("status")\nassert result == status',
    "module_function_oracle": "def _g():\n    r = verify(1)\n    return r",
    "unpack_rebind": 'status2, reason2 = blackbox_result(result)\nstatus2 = "VERIFIED"',
    "global_raw": 'def _f():\n    global result\n    result["status"] = "VERIFIED"',
    "nonlocal_raw": 'def _f():\n    nonlocal result\n    result["status"] = "VERIFIED"',
}

SYNTAX_MUTATION_IDS = tuple(sorted(SYNTAX_BLOCK))
