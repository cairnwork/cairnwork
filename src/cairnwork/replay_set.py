"""Record-only inputs needed to identify a verifier replay (#1105)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cairnwork import __version__
from cairnwork.gitcmd import run_git, run_git_bytes
from cairnwork.p2p_probe_log import mask_secrets


def capture(task, candidate_commit: str, *, repo: Path) -> dict:
    """Return a bounded replay description; recording failures never affect the verdict."""
    try:
        patch = run_git_bytes(repo, "diff", "--binary", f"{task.base_commit}..{candidate_commit}")
        source = Path(__file__).parent
        try:
            core_commit = run_git(source, "rev-parse", "HEAD").strip()
        except Exception:  # noqa: BLE001 - an installed package may not be in a checkout
            core_commit = None
        home = str(Path.home())
        verifiers = [
            [verifier_id, [_safe_arg(arg, home) for arg in argv]]
            for verifier_id, argv in task.verifiers
        ]
        return {
            "base": task.base_commit,
            "candidate_commit": candidate_commit,
            "candidate_patch_sha256": hashlib.sha256(patch).hexdigest(),
            "test_commit": task.test_commit or None,
            "verifiers": verifiers,
            "core_build": {"version": __version__, "commit": core_commit},
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics are best effort by contract
        return {"replay_set_error": type(exc).__name__}


def for_status(status: str, task, candidate: str) -> dict:
    """Only a refusing verdict (FAILED, UNCOMPARABLE) keeps a replay set."""
    if status not in {"FAILED", "UNCOMPARABLE"}:
        return {}
    return capture(task, candidate, repo=task.repo)


def _safe_arg(value: str, home: str) -> str:
    return mask_secrets(value).replace(home, "<home>") if home else mask_secrets(value)
