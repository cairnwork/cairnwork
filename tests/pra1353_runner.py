"""Fresh-process runner for the pinned #1353 witness recipes."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Any


def _bootstrap(import_roots):
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["GIT_NO_REPLACE_OBJECTS"] = "1"
    os.environ["GIT_CONFIG_COUNT"] = "2"
    os.environ["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    os.environ["GIT_CONFIG_VALUE_0"] = "false"
    os.environ["GIT_CONFIG_KEY_1"] = "core.hooksPath"
    os.environ["GIT_CONFIG_VALUE_1"] = "/dev/null"
    stdlib, platstdlib, dynload, src, tests, purelib, platlib = import_roots
    sys.path[:] = [stdlib, platstdlib, dynload, src, tests, purelib, platlib]
    os.environ["PRA1353_IMPORT_ROOTS"] = json.dumps(sys.path)
    package_spec = importlib.util.spec_from_file_location(
        "tests", os.path.join(tests, "__init__.py"), submodule_search_locations=[tests]
    )
    if package_spec is None or package_spec.loader is None:
        raise RuntimeError(_neutralize("sealed tests package initializer is unavailable"))
    package = importlib.util.module_from_spec(package_spec)
    sys.modules["tests"] = package
    package_spec.loader.exec_module(package)
    from pra1353_derive import derive
    from pra1353_extra import extra_rows
    from pra1353_probe import core_rows
    from pra1353_recipes import FIELDS, REASONS, RECIPES, ROUTES

    rows = core_rows() + extra_rows()
    recipes: list[dict[str, Any]] = [dict(zip(FIELDS, values, strict=True)) for values in RECIPES]
    for recipe in recipes:
        recipe["reason"] = REASONS.get(recipe["seq"], "")
        recipe["route"] = ROUTES[recipe.pop("route_id")]
    by_key = {(recipe["class_id"], recipe["probe"]): recipe for recipe in recipes}
    if len(by_key) != len(recipes) or len(rows) != len(recipes):
        raise RuntimeError("recipe/row identity mismatch")
    rebuilt = [derive(row["raw_return"], _match_recipe(row, by_key), sys.platform) for row in rows]
    modules = [
        {
            "name": name,
            "file": getattr(module, "__file__", None),
            "origin": getattr(getattr(module, "__spec__", None), "origin", None),
            "loader": _loader_name(getattr(getattr(module, "__spec__", None), "loader", None)),
        }
        for name, module in sorted(sys.modules.items())
        if isinstance(module, types.ModuleType) and type(module).__name__ != "Lib"
    ]
    child_dir = Path(os.environ["PRA1353_CHILD_REPORT_DIR"])
    child_reports = []
    for report_path in sorted(child_dir.glob("*.json")):
        try:
            child_reports.append(json.loads(report_path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("child module-origin report unreadable") from exc
    if not child_reports:
        raise RuntimeError("child module-origin report missing")
    return {"rows": rebuilt, "modules": modules, "child_reports": child_reports}


def _loader_name(loader):
    # Builtin and frozen specs carry the importer class itself, not an instance.
    return loader.__name__ if isinstance(loader, type) else type(loader).__name__


def main():
    if len(sys.argv) != 8:
        raise RuntimeError(_neutralize("expected seven explicit import roots"))
    print(json.dumps(_bootstrap(sys.argv[1:]), sort_keys=True))
    return 0


def _match_recipe(row, recipes):
    key = (row.get("class_id"), row.get("probe"))
    recipe = recipes.get(key)
    if recipe is None:
        raise RuntimeError(_neutralize(f"unknown witness row identity: {key!r}"))
    return recipe


def _neutralize(value):
    return "\n".join(
        "| " + line if line.lstrip().startswith(("::", "##[")) else line
        for line in str(value).splitlines()
    )


if __name__ == "__main__":
    raise SystemExit(main())
