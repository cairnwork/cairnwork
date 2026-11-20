"""Which test files one macOS CI shard runs (ci.yml, job test-macos-shard).

Usage: python3 scripts/ci_macos_shard.py 1|2   (prints one path per line)

Two shards, balanced by the measured cost of each test file (scripts/ci_macos_weights.json): the
heaviest files first, each onto the shard with the smaller load so far; a shard starts with the
serial work it already carries (the sealed #1353 gate on shard 1, the serial verifier-account tests
on shard 2). A file the table does not know costs the table's default, so a new test file lands in
exactly one shard with no list to maintain. The witness file is run by the gate, never by pytest.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SHARDS = ("1", "2")
WITNESS_FILE = "tests/test_1039_1353_witnesses.py"
WEIGHTS = Path(__file__).with_name("ci_macos_weights.json")


def test_files(root: Path) -> list[str]:
    # pytest's default python_files (pyproject sets none): test_*.py and *_test.py
    found = {
        p.relative_to(root).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for p in (root / "tests").rglob(pattern)
    }
    return sorted(found - {WITNESS_FILE})


def xdist_group_of(root: Path) -> dict[str, str]:
    """File base name -> its xdist group, from tests/xdist_groups.py (the single source)."""
    spec = importlib.util.spec_from_file_location(
        "xdist_groups", root / "tests" / "xdist_groups.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load tests/xdist_groups.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.XDIST_GROUP_OF)


def assign(
    paths: list[str], table: dict, groups: dict[str, str] | None = None
) -> dict[str, list[str]]:
    """Shard name -> its files: heaviest unit first onto the lighter shard (ties: by path, shard 1).

    A unit is one file, or all the files of one xdist group (tests/xdist_groups.py): those share
    state at run time, so they stay together on one shard as they stay on one worker.
    """
    default, files = float(table["default"]), table["files"]
    groups = groups or {}
    units: dict[str, list[str]] = {}
    for path in paths:
        units.setdefault(groups.get(Path(path).name, path), []).append(path)

    def cost(unit: str) -> float:
        return sum(float(files.get(p, default)) for p in units[unit])

    load = {name: float(table["start"][name]) for name in SHARDS}
    out: dict[str, list[str]] = {name: [] for name in SHARDS}
    for unit in sorted(units, key=lambda u: (-cost(u), u)):
        name = min(SHARDS, key=lambda n: (load[n], n))
        out[name].extend(units[unit])
        load[name] += cost(unit)
    return {name: sorted(members) for name, members in out.items()}


def loads(shards: dict[str, list[str]], table: dict) -> dict[str, float]:
    default, files = float(table["default"]), table["files"]
    return {
        n: float(table["start"][n]) + sum(float(files.get(p, default)) for p in ps)
        for n, ps in shards.items()
    }


def files_for(shard: str, root: Path) -> list[str]:
    table = json.loads(WEIGHTS.read_text())
    return assign(test_files(root), table, xdist_group_of(root))[shard]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SHARDS:
        sys.exit(f"usage: ci_macos_shard.py {'|'.join(SHARDS)}")
    chosen = files_for(sys.argv[1], Path(__file__).resolve().parent.parent)
    if not chosen:
        sys.exit("ci_macos_shard.py: no test files for this shard")
    print("\n".join(chosen))
