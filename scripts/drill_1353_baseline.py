#!/usr/bin/env python3
"""Regenerate the measured Issue #1353 baseline under docs/drills only."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = (ROOT / "docs/drills").resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def output_dir(value: str) -> Path:
    target = Path(value)
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    if target != DEST and DEST not in target.parents:
        raise SystemExit("--out must resolve beneath docs/drills/")
    return target


def totals(rows: list[dict]) -> dict:
    tamper = [
        row for row in rows if re.fullmatch(r"C\d+", row["class_id"]) and row["class_id"] != "C10"
    ]
    return {
        "N_tamper": len(tamper),
        "N_honest": sum(row["class_id"] == "HONEST" for row in rows),
        "NOT_RUN": sum(row["classification"] == "NOT_RUN" for row in rows),
        "VERIFIED-wrong": sum(row["classification"] == "VERIFIED_WRONG" for row in tamper),
        "spec_mismatch_count": sum(not row["matches_spec"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/drills/")
    args = parser.parse_args()
    from test_1039_1353_witnesses import run_baseline_probes

    rows = sorted(run_baseline_probes(), key=lambda row: (row["class_id"], row["probe"]))
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
