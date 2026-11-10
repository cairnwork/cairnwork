"""Record-only diagnostics for failed verifier runs (#1105).

Evidence is best effort: it is never an input to observations, comparisons, or verdicts. Log
redaction reuses the P2P masker, then replaces the current user's home prefix; it is not exhaustive.

Deferred tests stay hidden (the pipeline deletes base logs for the same reason): when the task has a
test commit, a verifier whose targets may reach a deferred file keeps no log tail, and a failed node
id under a deferred file is only counted. Anything that cannot be matched for sure counts as hidden.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from cairnwork.p2p_probe_log import mask_secrets

MAX_NODEIDS = 20
MAX_LINES = 200
MAX_BYTES = 16 * 1024


def capture(runs, *, home: str, deferred_paths=(), hidden: bool = False) -> list[dict]:
    """Build bounded evidence for each nonpassing verifier; any caller failure must be ignored."""
    evidence = []
    for run in runs:
        if run.observation.passed:
            continue
        failed = sorted({node for node, outcome in run.outcomes.items() if outcome == "failed"})
        shown = [n for n in failed if not hidden or _reaches(n, deferred_paths) is False]
        withheld = hidden and any(_reaches(t, deferred_paths) is not False for t in _targets(run))
        tail, truncated, unavailable = "", False, False
        if not withheld:
            try:
                tail, truncated = _tail(Path(run.log_path), home)
            except Exception:  # noqa: BLE001 - evidence is best effort and cannot affect a verdict
                unavailable = True
        evidence.append(
            {
                "id": run.spec.verifier_id,
                "failed_nodeids": shown[:MAX_NODEIDS],
                "failed_nodeids_truncated": len(shown) > MAX_NODEIDS,
                "failed_hidden_count": len(failed) - len(shown),
                "log_tail": tail,
                "log_tail_truncated": truncated,
                "log_tail_withheld": withheld,
                "log_tail_unavailable": unavailable,
            }
        )
    return evidence


def capture_for_task(runs, task, deferred_paths) -> list[dict]:
    """Attach diagnostics; with a test commit its tests are hidden. Never raises."""
    try:
        hidden = bool(task.test_commit)
        home = str(Path.home().resolve())
        return capture(runs, home=home, deferred_paths=deferred_paths, hidden=hidden)
    except Exception as exc:  # noqa: BLE001 - record-only collection must never affect judgment
        return [
            {"id": "", "log_tail_unavailable": True, "failure_evidence_error": type(exc).__name__}
        ]


def _targets(run) -> tuple[str, ...]:
    targets = tuple(str(t) for t in run.spec.targets)
    return targets or ("",)  # no targets: the whole suite, deferred files included


def _tail(path: Path, home: str) -> tuple[str, bool]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    redacted = mask_secrets(raw).replace(home, "<home>") if home else mask_secrets(raw)
    lines = redacted.splitlines(keepends=True)
    encoded = "".join(lines[-MAX_LINES:]).encode("utf-8")
    cut = len(encoded) > MAX_BYTES
    return encoded[-MAX_BYTES:].decode("utf-8", errors="ignore"), len(lines) > MAX_LINES or cut


def _reaches(value: str, paths) -> bool | None:
    """Whether a target or node id may reach a deferred file; None when that cannot be decided."""
    if not paths:
        return None
    text = value.split("::", 1)[0].rstrip("/").lower()  # case-insensitive file systems
    if not text or text.startswith(("-", "/")) or text in (".", "pytest"):
        return None
    if any(mark in text for mark in ("*", "?", "[", " ", "\\")):
        return None
    path = PurePosixPath(text)
    if ".." in path.parts or "." in path.parts:
        return None
    deferred = tuple(PurePosixPath(str(p).rstrip("/").lower()) for p in paths)
    return any(path == item or path in item.parents or item in path.parents for item in deferred)
