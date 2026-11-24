"""Summarise the rows of tests/measure_986_ac8.py against the #986 AC8 thresholds.

Usage: measure_986_summary.py ROWS.jsonl [SUMMARY.json] [--min-n N] [--expect-n N].
Prints a markdown table and exits 0; exits 1 and prints INVALID when the rows cannot support the
numbers (no rows, a missing or repeated case, a pair count other than --expect-n, fewer than
--min-n pairs, default 20). Time is taken from the good candidate only, and only from pairs that
completed in both arms; pair 0 is a warm-up, left out."""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

MIN_N = 20
MIN_TIMED_FRACTION = 0.4  # of the good pairs after the warm-up that must have a usable time


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def _invalid(rows: list[dict], min_n: int, expect_n: int | None) -> list[str]:
    if not rows:
        return ["no rows"]
    if expect_n is not None and expect_n <= 0:
        return ["--expect-n must be positive"]
    n = max(r["i"] for r in rows) + 1 if expect_n is None else expect_n
    want = {(i, a, s) for i in range(n) for a in ("on", "off") for s in ("bad", "good")}
    keys = Counter((r["i"], r["arm"], r["scenario"]) for r in rows)
    reasons = []
    if n < min_n:
        reasons.append(f"{n} pairs, need at least {min_n}")
    if set(keys) != want:
        reasons.append(f"{len(want - set(keys))} of {len(want)} expected cases missing")
    if any(v > 1 for v in keys.values()):
        reasons.append("a case appears more than once")
    if any(not math.isfinite(r["seconds"]) or r["seconds"] <= 0 for r in rows):
        reasons.append("a time is not a finite positive number")
    return reasons


def summarise(rows: list[dict], min_n: int = MIN_N, expect_n: int | None = None) -> dict:
    invalid = _invalid(rows, min_n, expect_n)
    on = {(r["i"], r["scenario"]): r for r in rows if r["arm"] == "on"}
    off = {(r["i"], r["scenario"]): r for r in rows if r["arm"] == "off"}
    both = sorted(on.keys() & off.keys())
    timed = [
        k
        for k in both
        if k[1] == "good"
        and on[k]["status"] == off[k]["status"] == "COMPLETED"  # an early end is not a time
        and (k[0] > 0 or len(both) <= 2)
    ]
    added = [on[k]["seconds"] - off[k]["seconds"] for k in timed]
    total_on = sum(on[k]["seconds"] for k in timed)
    total_off = sum(off[k]["seconds"] for k in timed)
    eligible = [k for k in both if k[1] == "good" and (k[0] > 0 or len(both) <= 2)]
    floor = max(1, math.ceil(MIN_TIMED_FRACTION * len(eligible)))
    if len(timed) < floor:
        invalid.append(f"{len(timed)} timed good pairs, need at least {floor}")
    on_rows = list(on.values())
    ok = [r["probe"] == "OK" for r in on_rows]
    return {
        "invalid": invalid,
        "pairs": len(both),
        "timed_pairs": len(timed),
        "collection_success": (sum(ok) / len(ok)) if ok else None,
        "fallback_reasons": dict(Counter(r["probe"] for r in on_rows if r["probe"] != "OK")),
        "added_p95_s": round(_p95(added), 3),
        "added_max_s": round(max(added, default=0.0), 3),
        "total_on_s": round(total_on, 3),
        "total_off_s": round(total_off, 3),
        "end_to_end_increase": round(total_on / total_off - 1, 4) if total_off else None,
        # a bad candidate accepted with the step on although the off arm refused it
        "new_false_passes": sum(
            k[1] == "bad" and on[k]["status"] == "COMPLETED" and off[k]["status"] != "COMPLETED"
            for k in both
        ),
        "bad_not_refused_on": sum(
            r["scenario"] == "bad" and not r["refused_conftest"] for r in on_rows
        ),
        "false_blocks_on": sum(
            r["scenario"] == "good" and r["status"] != "COMPLETED" for r in on_rows
        ),
        "false_blocks_off": sum(
            r["scenario"] == "good" and r["status"] != "COMPLETED" for r in off.values()
        ),
        "errors": sum(r["status"] == "ERROR" for r in rows),
    }


def table(s: dict) -> str:
    if s["invalid"]:
        lines = ["**INVALID: no AC8 numbers.** " + "; ".join(s["invalid"]), ""]
    else:
        lines = []
    gate = [
        ("Collection success", s["collection_success"], ">= 0.90"),
        ("Added P95 per good candidate (s)", s["added_p95_s"], "<= 30"),
        ("End-to-end increase (good candidate)", s["end_to_end_increase"], "<= 0.20"),
        ("New false passes", s["new_false_passes"], "0"),
        ("False blocks (step on)", s["false_blocks_on"], "0"),
        ("Added max (s)", s["added_max_s"], "-"),
        ("Error rows", s["errors"], "0"),
    ]
    lines += [
        f"Timed good pairs: {s['timed_pairs']} (at least {MIN_TIMED_FRACTION:.0%} required)",
        "",
    ]
    lines += ["| Item | Measured | AC8 threshold |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in gate]
    lines += ["", "```json", json.dumps(s, indent=2), "```"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = argv[1:]
    min_n = MIN_N
    if "--min-n" in args:
        at = args.index("--min-n")
        min_n = int(args[at + 1])
        del args[at : at + 2]
    expect_n = None
    if "--expect-n" in args:
        at = args.index("--expect-n")
        expect_n = int(args[at + 1])
        del args[at : at + 2]
    path = Path(args[0]) if args else None
    text = path.read_text() if path is not None and path.exists() else ""
    rows = [json.loads(x) for x in text.splitlines() if x.strip()]
    result = summarise(rows, min_n, expect_n)
    if len(args) > 1:
        Path(args[1]).write_text(json.dumps(result, indent=2) + "\n")
    print(table(result))
    return 1 if result["invalid"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
