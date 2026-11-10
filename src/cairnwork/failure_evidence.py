"""Record-only diagnostics for failed verifier runs (#1105).

Evidence is best effort: it is never an input to observations, comparisons, or verdicts. Log
redaction reuses the P2P masker, then replaces the current user's home prefix; it is not exhaustive.

Deferred tests stay hidden (the pipeline deletes base logs for the same reason): when the task has a
test commit, an entry keeps only how many tests failed. A deferred conftest or helper can print into
any test's output, and the test commit may change files beyond the ones the pipeline names, so no
log tail and no node id is kept; the full log stays on the run host.
"""

from __future__ import annotations

from pathlib import Path

from cairnwork.p2p_probe_log import mask_secrets

MAX_NODEIDS = 20
MAX_LINES = 200
MAX_BYTES = 16 * 1024


def capture(runs, *, home: str, hidden: bool = False) -> list[dict]:
    """Build bounded evidence for each nonpassing verifier; any caller failure must be ignored."""
    evidence = []
    for run in runs:
        if run.observation.passed:
            continue
        failed = sorted({node for node, outcome in run.outcomes.items() if outcome == "failed"})
        shown = [] if hidden else failed
        tail, truncated, unavailable = "", False, False
        if not hidden:
            try:
                tail, truncated = _tail(Path(run.log_path), home)
            except Exception:  # noqa: BLE001 - evidence is best effort and cannot affect a verdict
                unavailable = True
        evidence.append(
            {
                "id": run.spec.verifier_id,
                "failed_nodeids": shown[:MAX_NODEIDS],
                "failed_nodeids_truncated": len(shown) > MAX_NODEIDS,
                "failed_count": len(failed),
                "log_tail": tail,
                "log_tail_truncated": truncated,
                "log_tail_withheld": hidden,
                "log_tail_unavailable": unavailable,
            }
        )
    return evidence


def capture_for_task(runs, task) -> list[dict]:
    """Attach diagnostics; with a test commit its tests are hidden. Never raises."""
    try:
        hidden = bool(task.test_commit)
        return capture(runs, home=str(Path.home().resolve()), hidden=hidden)
    except Exception as exc:  # noqa: BLE001 - record-only collection must never affect judgment
        return [
            {"id": "", "log_tail_unavailable": True, "failure_evidence_error": type(exc).__name__}
        ]


def _tail(path: Path, home: str) -> tuple[str, bool]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    redacted = mask_secrets(raw).replace(home, "<home>") if home else mask_secrets(raw)
    lines = redacted.splitlines(keepends=True)
    encoded = "".join(lines[-MAX_LINES:]).encode("utf-8")
    cut = len(encoded) > MAX_BYTES
    return encoded[-MAX_BYTES:].decode("utf-8", errors="ignore"), len(lines) > MAX_LINES or cut
