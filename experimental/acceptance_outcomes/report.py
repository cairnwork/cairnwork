"""Report (AC5, AC6): per Cairnwork version, counts, tracking status, overturn rate with coverage.

A rate is printed only with numerator, denominator and coverage; no closed case prints
``unknown``. Untracked calls sit outside the rate's denominator, so adding them never lowers it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from experimental.acceptance_outcomes.ledger import PRE_CORE, VERDICTS
from experimental.acceptance_outcomes.store import read_ledger

CLOSED = ("no_defect_found", "overturned")  # `unknown` is its own count, never a "clean" close
STATUSES = ("untracked", "tracking", "no_defect_found", "unknown", "overturned")


def status_of(events: list[dict[str, Any]]) -> str:
    """Tracking status from one call's events. A confirmation beats everything."""
    if any(e["type"] == "confirmation" for e in events):
        return "overturned"
    last = max((i for i, e in enumerate(events) if e["type"] == "closed"), default=None)
    if last is not None:  # the window must precede the last close (ledger append order)
        done = events[last].get("checks_done") and any(
            e["type"] == "observed" for e in events[:last]
        )
        return "no_defect_found" if done else "unknown"
    return "tracking" if events else "untracked"


def _rate(num: int, den: int, closed: int, total: int) -> dict[str, Any]:
    text = "unknown" if den == 0 else f"{num}/{den} = {num / den:.3f}"
    cov = f"{closed}/{total}"
    return {"num": num, "den": den, "rate": None if den == 0 else num / den, "text": text,
            "coverage": cov, "coverage_value": None if total == 0 else closed / total}  # fmt: skip


def _version_block(calls: list[dict[str, Any]], events: dict[str, list[dict[str, Any]]]) -> dict:
    counts = {v: 0 for v in (*VERDICTS, PRE_CORE)}
    status: dict[str, list[str]] = {s: [] for s in STATUSES}
    by_call: dict[str, str] = {}
    for c in calls:
        counts[c["verdict"]] += 1
        by_call[c["call_id"]] = status_of(events.get(c["call_id"], []))
        status[by_call[c["call_id"]]].append(c["call_id"])
    ids = {v: [c["call_id"] for c in calls if c["verdict"] == v] for v in counts}
    # #809: lost bindings remain visible, but are not eligible for any measured rate.
    lost = [c for c in calls if c.get("notary_lost")]
    lost_ids = {v: [c["call_id"] for c in lost if c["verdict"] == v] for v in counts}
    rate_ids = {v: [i for i in ids[v] if i not in set(lost_ids[v])] for v in counts}
    block: dict[str, Any] = {
        "total": len(calls),
        "reached_core": sum(1 for c in calls if not c["no_notary_row"]),
        "unchecked_binding": sum(1 for c in calls if c.get("notary_lost")),
        "counts": counts,
        "cases": {
            "all": [c["call_id"] for c in calls],
            **ids,
            **status,
            **({"notary_lost": lost_ids} if lost else {}),
        },
    }
    block["VERIFIED"] = _verified(rate_ids["VERIFIED"], by_call, events)
    block["refused_but_correct"] = _refused(rate_ids["FAILED"] + rate_ids["REFUSED"], by_call)
    block["pre_core_refused_but_correct"] = _refused(rate_ids[PRE_CORE], by_call)
    return block


def _refused(ids: list[str], by_call: dict[str, str]) -> dict[str, Any]:
    closed = [i for i in ids if by_call[i] in CLOSED]
    rbc = [i for i in closed if by_call[i] == "overturned"]
    return {**_rate(len(rbc), len(closed), len(closed), len(ids)), "cases": rbc}


def _verified(ids: list[str], by_call: dict[str, str], events: dict) -> dict[str, Any]:
    tracked = [i for i in ids if by_call[i] != "untracked"]
    closed = [i for i in ids if by_call[i] in CLOSED]
    unknown = [i for i in ids if by_call[i] == "unknown"]
    over = [i for i in ids if by_call[i] == "overturned"]
    kinds: dict[str, set[tuple[str, str]]] = {}
    for i in over:
        fids = {e["finding_id"]: e["root"] for e in events[i] if e["type"] == "finding"}
        for e in events[i]:
            if e["type"] == "confirmation":  # one root cause counts once
                kinds.setdefault(e["outcome"], set()).add((i, fids[e["finding_id"]]))
    return {
        "tracked": len(tracked),
        "closed": len(closed),
        "overturned": len(over),
        "unknown": len(unknown),
        "overturned_by_kind": {k: len(v) for k, v in sorted(kinds.items())},
        **_rate(len(over), len(closed), len(closed), len(ids)),
        "cases": {"tracked": tracked, "closed": closed, "unknown": unknown, "overturned": over},
    }


def build_report(path: Path) -> dict[str, Any]:
    """Per-version report; raises OutcomesError if the chain check fails (no partial numbers)."""
    rows = read_ledger(path)
    events: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["type"] != "call":
            events.setdefault(r["call_id"], []).append(r)
    versions: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["type"] == "call":
            versions.setdefault(r["version"], []).append(r)
    return {v: _version_block(c, events) for v, c in sorted(versions.items())}


def format_report(report: dict[str, Any], *, cases: bool = False) -> str:
    out: list[str] = []
    for version, b in report.items():
        out.append(f"version {version}: calls {b['total']}, reached Core {b['reached_core']}")
        out.append("  " + ", ".join(f"{k} {n}" for k, n in b["counts"].items()))
        v, unchecked = b["VERIFIED"], f", unchecked binding {b['unchecked_binding']}"
        kinds = ", ".join(f"{k} {n}" for k, n in v["overturned_by_kind"].items()) or "none"
        out.append(f"  VERIFIED: tracked {v['tracked']}, closed {v['closed']}, "
                   f"unknown {v['unknown']}, overturned {v['overturned']} ({kinds})")  # fmt: skip
        outside = ", notary-lost outside rate" if b["unchecked_binding"] else ""
        out.append(f"  VERIFIED overturn rate: {v['text']}, coverage {v['coverage']}"
                   f"{unchecked}{outside}")  # fmt: skip
        r = b["refused_but_correct"]
        out.append(
            f"  FAILED/REFUSED refused_but_correct: {r['text']}, "
            f"coverage {r['coverage']}{unchecked}{outside}"
        )
        p = b["pre_core_refused_but_correct"]
        out.append(f"  PRE_CORE refused_but_correct: {p['text']}, coverage {p['coverage']}")
        if cases:
            for name, ids in b["cases"].items():
                if isinstance(ids, dict):
                    for verdict, case_ids in ids.items():
                        out.append(f"  cases {name} {verdict}: {' '.join(case_ids) or '-'}")
                else:
                    out.append(f"  cases {name}: {' '.join(ids) or '-'}")
    return "\n".join(out)
