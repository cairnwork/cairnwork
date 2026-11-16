#!/usr/bin/env python3
"""Regenerate the measured Issue #1353 baseline under docs/drills only."""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = (ROOT / "docs/drills").resolve()


def bootstrap_tests():
    """Make the local test helpers importable the way pytest's rootdir conftest does."""
    for path in (ROOT / "tests", ROOT):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def output_dir(value):
    target = Path(value)
    target = target if target.is_absolute() else ROOT / target
    target = target.resolve()
    if target != DEST and DEST not in target.parents:
        raise SystemExit("--out must resolve beneath docs/drills/")
    return target


def totals(rows):
    tamper = [
        r
        for r in rows
        if re.fullmatch(r"C\d+", r["class_id"])
        and r["class_id"] != "C10"
        and r["classification"] not in {"HONEST_VERIFIED", "HONEST_FAILED"}
    ]
    return {
        "N_tamper": len(tamper),
        "N_honest": sum(r["class_id"] == "HONEST" for r in rows),
        "NOT_RUN": sum(r["classification"] == "NOT_RUN" for r in rows),
        "VERIFIED-wrong": sum(r["classification"] == "VERIFIED_WRONG" for r in tamper),
        "spec_mismatch_count": sum(not r["matches_spec"] for r in rows),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/drills/")
    args = parser.parse_args()
    bootstrap_tests()
    core = importlib.import_module("_w1353_probe")
    extra = importlib.import_module("_w1353_extra")
    rows = sorted(core.core_rows() + extra.extra_rows(), key=lambda r: (r["class_id"], r["probe"]))
    data = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "platform": sys.platform,
        "probes": rows,
        "totals": totals(rows),
    }
    folder = output_dir(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"1353-baseline-{sys.platform}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data["totals"], sort_keys=True))
    print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
