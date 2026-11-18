"""cw#1454 B1: product code under src/ must not be able to rewrite witness rows."""

from __future__ import annotations

import pra1353_consume as consume
from test_1039_1353_witnesses import PINS, broker_report  # noqa: F401

# Each rebind fires only inside the runner process (it keys on the runner's own names), so a
# run that still shares a process with product code changes rows, and a clean one does not.
PATCH = (
    "\nimport sys as _s\n"
    "_m = _s.modules.get('__main__')\n"
    "if hasattr(_m, '_bootstrap'):\n"
    "    import json as _j\n"
    "    _p = _s.modules.get('pra1353_probe')\n"
    "    _d = _s.modules.get('pra1353_derive')\n"
    "    _p.verify = lambda *a, **k: {'status': 'REFUSED', 'reason': 'forged'}\n"
    "    _d.blackbox_classification = lambda *a: 'NOT_RUN'\n"
    "    _r = _m._match_recipe\n"
    "    _m._match_recipe = lambda row, recipes: {**_r(row, recipes), 'expected': 'forged'}\n"
    "    _o = _j.dumps\n"
    "    _j.dumps = lambda v, *a, **k: _o(\n"
    "        {**v, 'rows': [{**r, 'matches_spec': True} for r in v['rows']]}\n"
    "        if isinstance(v, dict) and 'rows' in v\n"
    "        else v,\n"
    "        *a,\n"
    "        **k,\n"
    "    )\n"
)


def _rows(report):
    return {r["seq"]: r for r in report["rows"]}


def test_b1_product_init_cannot_change_rows_of_other_routes(monkeypatch, broker_report):  # noqa: F811
    honest = _rows(broker_report)
    real = consume._tracked_source_bytes()
    init = "src/cairnwork/__init__.py"
    assert init in real
    tampered = {**real, init: real[init] + PATCH.encode()}
    monkeypatch.setattr(consume, "_tracked_source_bytes", lambda repo=None: tampered)
    try:
        report = consume.launch(PINS)
    except Exception:  # noqa: BLE001 - fail closed is the acceptable outcome
        return
    forged = _rows(report)
    changed = sorted(s for s in honest if honest[s] != forged.get(s))
    assert not changed, f"B1 CONFIRMED: product import rewrote rows (seq): {changed}"


def test_b1_runner_process_never_loads_product_modules(broker_report):  # noqa: F811
    runner = {item["name"] for item in broker_report["modules"]}
    assert not {n for n in runner if n.split(".")[0].startswith("cairnwork")}
    launcher_children = [
        {item["name"] for item in child["modules"]}
        for child in broker_report["child_reports"]
        if any(item["name"] == "pra1353_launch" for item in child["modules"])
    ]
    assert launcher_children
    assert all("cairnwork" in names for names in launcher_children)
