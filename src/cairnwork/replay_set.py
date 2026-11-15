"""Record-only inputs needed to identify a verifier replay (#1105)."""

from __future__ import annotations

import hashlib
import re
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
        home = str(Path.home().resolve())
        verifiers = [[verifier_id, _safe_argv(argv, home)] for verifier_id, argv in task.verifiers]
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


def for_status(status: str, task, candidate: str, verified: bool = False) -> dict:
    """Only a refusing verdict (FAILED, UNCOMPARABLE) keeps a replay set."""
    if verified or status not in {"FAILED", "UNCOMPARABLE"}:
        return {}
    return capture(task, candidate, repo=task.repo)


_SECRET_FLAG = re.compile(
    r"^--?(?:[a-z0-9_-]*(?:token|password|passwd|secret|api-?key|apikey|credential)[a-z0-9_-]*|key|p)$",
    re.IGNORECASE,
)


def _safe_argv(argv, home: str) -> list[str]:
    """Mask each element, and the value element that follows a bare secret-named flag."""
    out: list[str] = []
    for index, arg in enumerate(argv):
        after_flag = index > 0 and bool(_SECRET_FLAG.match(str(argv[index - 1])))
        out.append("<redacted>" if after_flag else _safe_arg(str(arg), home))
    return out


def _safe_arg(value: str, home: str) -> str:
    masked = mask_secrets(value)
    return masked.replace(home, "<home>") if len(home) > 1 else masked
