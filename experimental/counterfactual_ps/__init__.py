"""cw#1068 counterfactual comparison for sandbox-caused ``ps`` denials (experimental, default off).

Pure functions only: no subprocess, no file or network access, no sandbox handling.
The caller supplies two texts:
  sandbox_log   verifier output of the scored run (inside the sandbox)
  plain_output  pytest output of the same candidate and tests, re-run OUTSIDE the
                sandbox by a separate isolated job
The verdict can only downgrade FAILED to UNCOMPARABLE; it never produces VERIFIED and
is not wired into any verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PS_DENIAL = re.compile(
    r"PermissionError:.*'(?:/bin/)?ps'"
    r"|/bin/ps: Operation not permitted"
    r"|\bps: Operation not permitted"
)
_UNIT = re.compile(r"^(FAILED|SUBFAILED)(\([^)]*\))? (\S+)", re.M)
_COUNT = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
_KINDS = r"(?:passed|failed|errors?|skipped)"
_SUMMARY = re.compile(
    rf"^=+ .*\b\d+ {_KINDS}\b.* =+$|^\d+ {_KINDS}\b.* in [\d.]+s",
    re.M,
)

FAILED, UNCOMPARABLE, ERROR, NOT_APPLICABLE = "FAILED", "UNCOMPARABLE", "ERROR", "N/A"


@dataclass(frozen=True)
class Result:
    verdict: str
    reason: str
    sandbox_units: int = 0
    plain_clean: bool = False


def failing_units(text: str) -> set[tuple[str, str, str]]:
    return {(k, tag or "", node) for k, tag, node in _UNIT.findall(text)}


def plain_counts(text: str) -> dict[str, int] | None:
    """Counts from the last pytest summary line; None if there is no summary (not a pass)."""
    lines = [m.group(0) for m in _SUMMARY.finditer(text)]
    if not lines:
        return None
    counts: dict[str, int] = {}
    for n, word in _COUNT.findall(lines[-1]):
        counts["error" if word.startswith("error") else word] = int(n)
    return counts


def compare(
    sandbox_log: str,
    plain_output: str,
    *,
    guard_tripped: bool = False,
    signature_text: str | None = None,
) -> Result:
    """Diagnostic verdict: FAILED stays FAILED unless the outside run is fully clean
    AND the sandbox run shows a ps denial."""
    counts = plain_counts(plain_output)
    if guard_tripped:
        return Result(ERROR, "wall or memory guard fired during the outside run")
    if counts is None:
        return Result(ERROR, "outside run has no pytest summary line")
    sandbox = failing_units(sandbox_log)
    if not sandbox:
        return Result(NOT_APPLICABLE, "sandbox run has no failing units")
    clean = counts.get("passed", 0) > 0 and not any(
        counts.get(k, 0) for k in ("failed", "error", "skipped")
    )
    if failing_units(plain_output) or not clean:
        return Result(
            FAILED, "real failures (or skips) remain outside the sandbox", len(sandbox), False
        )
    if not PS_DENIAL.search(signature_text if signature_text is not None else sandbox_log):
        return Result(
            FAILED,
            "sandbox-only failures without a ps denial are not explained",
            len(sandbox),
            True,
        )
    return Result(
        UNCOMPARABLE, "outside run clean; sandbox failures carry a ps denial", len(sandbox), True
    )
