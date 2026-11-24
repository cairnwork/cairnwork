"""Summarise the rows of tests/measure_986_ac8.py against the #986 AC8 thresholds.

Usage: measure_986_summary.py ROWS.jsonl [SUMMARY.json]. Prints a markdown table. Exit 0 always:
the verdict is in the table (this is a measurement, not a gate)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def summarise(rows: list[dict]) -> dict:
    on = {(r["i"], r["scenario"]): r for r in rows if r["arm"] == "on"}
    off = {(r["i"], r["scenario"]): r for r in rows if r["arm"] == "off"}
    pairs = sorted(on.keys() & off.keys())
    added = [on[k]["seconds"] - off[k]["seconds"] for k in pairs]
    total_on = sum(on[k]["seconds"] for k in pairs)
    total_off = sum(off[k]["seconds"] for k in pairs)
    probes = [r["probe"] for r in on.values() if r["scenario"] == "bad"]
    return {
        "pairs": len(pairs),
        "collection_success": (sum(p == "OK" for p in probes) / len(probes)) if probes else None,
        "added_p95_s": round(_p95(added), 3),
        "added_max_s": round(max(added, default=0.0), 3),
        "total_on_s": round(total_on, 3),
        "total_off_s": round(total_off, 3),
        "end_to_end_increase": round(total_on / total_off - 1, 4) if total_off else None,
        # a bad candidate accepted with the step on although the off arm refused it
        "new_false_passes": sum(
            on[k]["scenario"] == "bad" and on[k]["status"] == "COMPLETED" and not _ok(off[k])
            for k in pairs
        ),
        "bad_not_refused_on": sum(
            r["scenario"] == "bad" and not r["refused_conftest"] for r in on.values()
        ),
        # a good candidate refused or made uncomparable with the step on
        "false_blocks_on": sum(
            r["scenario"] == "good" and r["status"] != "COMPLETED" for r in on.values()
        ),
        "false_blocks_off": sum(
            r["scenario"] == "good" and r["status"] != "COMPLETED" for r in off.values()
        ),
    }


def _ok(row: dict) -> bool:
    return row["status"] == "COMPLETED"


def table(s: dict) -> str:
    gate = [
        ("Collection success", s["collection_success"], ">= 0.90"),
        ("Added P95 (s)", s["added_p95_s"], "<= 30"),
        ("End-to-end increase", s["end_to_end_increase"], "<= 0.20"),
        ("New false passes", s["new_false_passes"], "0"),
    ]
    lines = ["| Item | Measured | AC8 threshold |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in gate]
    lines += ["", "```json", json.dumps(s, indent=2), "```"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    rows = [json.loads(x) for x in Path(argv[1]).read_text().splitlines() if x.strip()]
    result = summarise(rows)
    if len(argv) > 2:
        Path(argv[2]).write_text(json.dumps(result, indent=2) + "\n")
    print(table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
