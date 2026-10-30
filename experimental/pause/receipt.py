"""PAUSE receipts (#476 AC5): one JSON line per decision, sealed by its own sha256.

``build`` refuses an inconsistent receipt, ``verify`` lists what is wrong with a stored one
(a tampered field, or a plan whose sha256 is not the recorded one), and ``append`` keeps the
event order of a receipts file. With the flag off nothing here runs (amendment 1 point 2)."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from pathlib import Path
from typing import Any

from experimental.pause.schema import (
    FALLBACK_REASONS,
    GATE_VERDICTS,
    RECEIPT_FIELDS,
    RECEIPT_SCHEMA,
    TRIGGER_REASONS,
)

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMPUTED = ("schema", "plan_sha256", "receipt_sha256")  # build sets these, never ``values``


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def digest(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "receipt_sha256"}
    return sha256_text(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _problems(rec: dict) -> list[str]:
    if list(rec) != list(RECEIPT_FIELDS):
        return [f"fields differ from {RECEIPT_FIELDS}"]
    out = []
    if rec["schema"] != RECEIPT_SCHEMA:
        out.append("schema")
    seq = rec["event_seq"]
    if type(seq) is not int or seq < 0:
        out.append("event_seq must be an int >= 0")
    if type(rec["guard_rollbacks"]) is not int or rec["guard_rollbacks"] < 0:
        out.append("guard_rollbacks must be an int >= 0")
    for name in ("checkpoint_sha256", "plan_sha256"):
        value = rec[name]
        if value is not None and not (isinstance(value, str) and SHA256_RE.fullmatch(value)):
            out.append(f"{name} must be 64 lowercase hex digits")
    if rec["gate_verdict"] not in (None, *GATE_VERDICTS):
        out.append("gate_verdict")
    if rec["intervened"] is True:
        if rec["reason"] not in (None, *TRIGGER_REASONS):
            out.append("reason: an intervention carries no fallback code")
        if rec["plan_sha256"] is None or rec["gate_verdict"] != "ACCEPT":
            out.append("intervened needs a plan_sha256 the gate accepted")
        if rec["checkpoint_sha256"] is None:
            out.append("intervened needs a checkpoint_sha256")
    elif rec["intervened"] is False:
        if rec["reason"] not in FALLBACK_REASONS:
            out.append("reason: no intervention needs a fallback code")
    else:
        out.append("intervened must be a bool")
    return out


def build(*, task_id: str, intervened: bool, event_seq: int, **values: Any) -> dict:
    """The sealed receipt; ``plan`` (the text) becomes plan_sha256. ValueError if inconsistent."""
    plan = values.pop("plan", None)
    rec: dict[str, Any] = dict.fromkeys(RECEIPT_FIELDS)
    rec.update(schema=RECEIPT_SCHEMA, task_id=task_id, intervened=intervened, guard_rollbacks=0)
    rec.update(event_seq=event_seq, plan_sha256=None if plan is None else sha256_text(plan))
    unknown = set(values) - set(RECEIPT_FIELDS)
    if unknown:
        raise ValueError(f"unknown receipt fields: {sorted(unknown)}")
    computed = set(values) & set(COMPUTED)
    if computed:
        raise ValueError(f"build computes {sorted(computed)}; pass plan= instead")
    rec.update(values)
    rec["reason"] = None if rec["reason"] is None else str(rec["reason"])
    problems = _problems(rec)
    if problems:
        raise ValueError("; ".join(problems))
    rec["receipt_sha256"] = digest(rec)
    return rec


def verify(rec: dict, plan: str | None = None) -> list[str]:
    """What is wrong with a stored receipt ([] if nothing); with ``plan``, it must be the one
    whose sha256 the receipt records."""
    out = _problems(rec)
    if out:
        return out
    if rec["receipt_sha256"] != digest(rec):
        out.append("receipt_sha256 does not match the receipt")
    if plan is not None and rec["plan_sha256"] != sha256_text(plan):
        out.append("plan_sha256 does not match the plan")
    return out


def verify_log(rows: list[dict]) -> list[str]:
    """Every receipt verifies and event_seq strictly increases (AC8's single controller)."""
    out = [f"row {i}: {p}" for i, r in enumerate(rows) for p in verify(r)]
    if not out and any(b["event_seq"] <= a["event_seq"] for a, b in itertools.pairwise(rows)):
        out.append("event_seq is not strictly increasing")
    return out


def append(path: Path, rec: dict) -> None:
    """Append one verified receipt whose event_seq follows the file's last one."""
    problems = verify(rec)
    if problems:
        raise ValueError("; ".join(problems))
    if path.exists():
        last = path.read_text().splitlines()[-1:]
        if last and json.loads(last[0])["event_seq"] >= rec["event_seq"]:
            raise ValueError("event_seq does not follow the file's last receipt")
    with path.open("a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
