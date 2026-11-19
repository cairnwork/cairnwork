"""Record-only inputs identifying a verifier replay."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from cairnwork import __version__
from cairnwork.gitcmd import run_git_bytes

_MAX_CHANGES = 256 * 1024


def _tree(repo: Path, commit: str) -> dict[str, tuple[str, str, str]]:
    raw = run_git_bytes(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", commit)
    result = {}
    for entry in raw.split(b"\0"):
        if entry:
            header, path = entry.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            result[os.fsdecode(path)] = mode, kind, oid
    return result


def capture(task, candidate: str, *, repo: Path) -> dict:
    """Return bounded tree changes; failures are diagnostic only."""
    try:
        old, new = _tree(repo, task.base_commit), _tree(repo, candidate)
        digest, size = hashlib.sha256(b"["), 2
        changes: list[dict[str, object]] | None = []
        for path in sorted(old.keys() | new.keys()):
            before, after = old.get(path), new.get(path)
            if before == after:
                continue
            item = {
                "path": path,
                "old": list(before) if before else None,
                "new": list(after) if after else None,
            }
            blob = json.dumps(item, ensure_ascii=True, separators=(",", ":")).encode()
            digest.update((b"," if size > 2 else b"") + blob)
            size += len(blob) + 1
            if changes is not None:  # past the cap only the hash keeps growing
                changes.append(item)
                if size > _MAX_CHANGES:
                    changes = None
        digest.update(b"]")
        patch: dict[str, object] = {"sha256": digest.hexdigest()}
        if changes is not None:
            patch["changes"] = changes
        return {
            "base": task.base_commit,
            "candidate_commit": candidate,
            "patch": patch,
            "test_commit": task.test_commit or None,
            "verifiers": [[name, list(argv)] for name, argv in task.verifiers],
            "core_build": {"version": __version__, "commit": "unknown"},
        }
    except Exception as exc:  # noqa: BLE001 - replay recording must not affect verdict
        return {"replay_set_error": type(exc).__name__}


def for_status(status: str, task, candidate: str, verified: bool = False) -> dict:
    """Attach replay inputs only to unverified FAILED or UNCOMPARABLE results."""
    if verified or status not in {"FAILED", "UNCOMPARABLE"}:
        return {}
    try:
        return capture(task, candidate, repo=task.repo)
    except Exception as exc:  # noqa: BLE001 - replay recording is best effort
        return {"replay_set_error": type(exc).__name__}


def record_only(result, status: str, task, candidate: str) -> dict:
    """The verify output's record-only fields (#1105): never in a hash, receipt or verdict."""
    return {
        "failure_evidence": result.failure_evidence,
        "replay_set": for_status(status, task, candidate, result.verified),
    }
