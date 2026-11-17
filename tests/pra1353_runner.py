"""Fresh-process runner for the pinned #1353 witness recipes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _bootstrap():
    root = Path(__file__).resolve().parents[1]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["GIT_CONFIG_COUNT"] = "2"
    os.environ["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    os.environ["GIT_CONFIG_VALUE_0"] = "false"
    os.environ["GIT_CONFIG_KEY_1"] = "core.hooksPath"
    os.environ["GIT_CONFIG_VALUE_1"] = "/dev/null"
    sys.path[:0] = [str(root / "src"), str(root), str(root / "tests")]
    from pra1353_derive import derive
    from pra1353_extra import extra_rows
    from pra1353_probe import core_rows
    from pra1353_recipes import FIELDS, RECIPES

    import cairnwork  # noqa: F401

    rows = core_rows() + extra_rows()
    if len(rows) != len(RECIPES):
        raise RuntimeError(f"recipe/row count mismatch: {len(RECIPES)} != {len(rows)}")
    recipes = [dict(zip(FIELDS, values, strict=True)) for values in RECIPES]
    rebuilt = [
        derive(row["raw_return"], recipe, sys.platform)
        for row, recipe in zip(rows, recipes, strict=True)
    ]
    loaded = sorted(
        name
        for name, module in sys.modules.items()
        if getattr(module, "__file__", None)
        and _inside(Path(module.__file__ or ""), root / "tests")
    )
    return {"rows": rebuilt, "loaded_test_modules": loaded}


def _inside(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def main():
    print(json.dumps(_bootstrap(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
