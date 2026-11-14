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
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)")
_SUMMARY = re.compile(r"(?:\d+ [a-z]+(?:, )?)+(?: in [\d.]+s(?: \([\d:]+\))?)?")
_MAX_LINE = 200

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
    """Counts from the single pytest summary line.

    None when there is no summary line or more than one (a second line can be candidate-planted,
    or two runs were concatenated): both are not a pass.
    """
    found = []
    for raw in text.splitlines():
        line = raw.strip().strip("=").strip()
        if len(line) <= _MAX_LINE and line and _SUMMARY.fullmatch(line):
            found.append(line)
    if len(found) != 1:
        return None
    counts: dict[str, int] = {}
    for n, word in _COUNT.findall(found[0]):
        key = (
            "error"
            if word.startswith("error")
            else word.rstrip("s")
            if word.startswith("warning")
            else word
        )
        counts[key] = counts.get(key, 0) + int(n)
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
    sandbox_counts = plain_counts(sandbox_log) or {}
    n_sandbox = len(sandbox) or sandbox_counts.get("failed", 0) + sandbox_counts.get("error", 0)
    if not n_sandbox:
        return Result(NOT_APPLICABLE, "sandbox run shows no failed or error results")
    clean = counts.get("passed", 0) > 0 and not any(
        counts.get(k, 0) for k in ("failed", "error", "skipped", "deselected")
    )
    if failing_units(plain_output) or not clean:
        return Result(
            FAILED,
            "real failures, skips or deselected tests remain outside the sandbox",
            n_sandbox,
            False,
        )
    if not PS_DENIAL.search(signature_text if signature_text is not None else sandbox_log):
        return Result(
            FAILED,
            "sandbox-only failures without a ps denial are not explained",
            n_sandbox,
            True,
        )
    return Result(
        UNCOMPARABLE, "outside run clean; sandbox failures carry a ps denial", n_sandbox, True
    )
