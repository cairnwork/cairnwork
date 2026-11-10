"""Record-only diagnostics for failed verifier runs.

Evidence is best effort: it is never an input to observations, comparisons, or verdicts. Log
redaction reuses the P2P masker, then replaces the current user's home prefix; it is not exhaustive.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from cairnwork.p2p_probe_log import mask_secrets

MAX_NODEIDS = 20
MAX_LINES = 200
MAX_BYTES = 16 * 1024


def capture(runs, *, home: str, deferred_paths=(), bundle: bool = False) -> list[dict]:
    """Build bounded evidence for each nonpassing verifier; any caller failure must be ignored."""
    evidence = []
    for run in runs:
        if run.observation.passed:
            continue
        nodes = sorted({node for node, outcome in run.outcomes.items() if outcome == "failed"})
        withheld = bundle and _targets_deferred(run.spec.targets, deferred_paths) is not False
        tail, truncated, unavailable = "", False, False
        if not withheld:
            try:
                raw = Path(run.log_path).read_text(encoding="utf-8", errors="replace")
                redacted = mask_secrets(raw).replace(home, "<home>") if home else mask_secrets(raw)
                lines = redacted.splitlines(keepends=True)
                bounded = "".join(lines[-MAX_LINES:])
                encoded = bounded.encode("utf-8")
                byte_truncated = len(encoded) > MAX_BYTES
                if byte_truncated:
                    encoded = encoded[-MAX_BYTES:]
                    bounded = encoded.decode("utf-8", errors="ignore")
                tail = bounded
                truncated = len(lines) > MAX_LINES or byte_truncated
            except Exception:  # noqa: BLE001 - evidence is best effort and cannot affect a verdict
                unavailable = True
        evidence.append(
            {
                "id": run.spec.verifier_id,
                "failed_nodeids": nodes[:MAX_NODEIDS],
                "failed_nodeids_truncated": len(nodes) > MAX_NODEIDS,
                "log_tail": tail,
                "log_tail_truncated": truncated,
                "log_tail_withheld": withheld,
                "log_tail_unavailable": unavailable,
            }
        )
    return evidence


def capture_safely(runs, *, home: str, deferred_paths=(), bundle: bool = False) -> list[dict]:
    """Return explicit unavailable entries if any optional capture step raises."""
    try:
        return capture(runs, home=home, deferred_paths=deferred_paths, bundle=bundle)
    except Exception as exc:  # noqa: BLE001 - record-only collection must never affect judgment
        return _unavailable(runs, exc)


def capture_for_task(runs, task, deferred_paths) -> list[dict]:
    """Attach diagnostics using the task's bundle policy and this process's home prefix."""
    try:
        return capture_safely(
            runs,
            home=str(Path.home().resolve()),
            deferred_paths=deferred_paths,
            bundle=bool(task.test_store and task.test_store.required),
        )
    except Exception as exc:  # noqa: BLE001 - even environment lookup is optional diagnostics
        return _unavailable(runs, exc)


def _unavailable(runs, exc) -> list[dict]:
    """Build empty markers, falling back to one generic marker for malformed run records."""
    try:
        return [
            {
                "id": run.spec.verifier_id,
                "failed_nodeids": sorted(
                    node for node, outcome in run.outcomes.items() if outcome == "failed"
                )[:MAX_NODEIDS],
                "failed_nodeids_truncated": sum(
                    outcome == "failed" for outcome in run.outcomes.values()
                )
                > MAX_NODEIDS,
                "log_tail": "",
                "log_tail_truncated": False,
                "log_tail_withheld": False,
                "log_tail_unavailable": True,
                "failure_evidence_error": type(exc).__name__,
            }
            for run in runs
            if not run.observation.passed
        ]
    except Exception:  # noqa: BLE001 - malformed evidence cannot block the original judgment
        return [{"id": "", "failed_nodeids": [], "log_tail": "", "log_tail_unavailable": True}]


def _targets_deferred(targets, paths) -> bool | None:
    """Whether verifier targets overlap deferred files; None means membership is uncertain."""
    if not paths or not targets:
        return None
    deferred = tuple(PurePosixPath(path) for path in paths)
    for target in targets:
        value = str(target).split("::", 1)[0]
        if (
            not value
            or value.startswith("-")
            or value in (".", "pytest")
            or any(mark in value for mark in ("*", "?", "[", " "))
        ):
            return None
        path = PurePosixPath(value.rstrip("/"))
        if any(path == item or path in item.parents or item in path.parents for item in deferred):
            return True
    return False
