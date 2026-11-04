"""Who added and who confirmed a case (#772 AC1b). A display name is recorded but never compared.

- Claude Code agent: ``session_id`` comes from the harness env ``CLAUDE_CODE_SESSION_ID`` (never a
  flag) and ``model_family`` is ``anthropic``; a flag naming another family is refused.
- Other agent (Agy, GPT lane, Codex): ``--dispatch-id`` plus the lane's ``--model-family`` and its
  output or transcript file (``--source-file``, sha256 kept).
- Human: ``--github-login`` with ``--source-ref`` (URL of their comment) and the saved body.
``lineage`` is the ancestor session ids, root first (coord ``--parent`` chain, plus the session a
fork or copy was taken from); ``root`` means none. Parts not given are left out and an identity
missing a part never confirms. One local user owns every input: this records honest attribution
and catches mix-ups, it does not stop that user from lying.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ENV_SESSION = "CLAUDE_CODE_SESSION_ID"
REQUIRED = ("kind", "session_id", "lineage", "model_family", "source_ref", "source_sha256")


class IdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _file_sha(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise IdentityError("IDENTITY_INVALID", f"source file unreadable: {path}") from exc


def _lineage(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [] if raw == "root" else [p for p in raw.split(",") if p]


def resolve_actor(env: Mapping[str, str], opts: Mapping[str, str | None]) -> dict[str, Any]:
    family = opts.get("model_family")
    actor: dict[str, Any] = {"display": opts.get("display_name"), "lineage": None}
    if opts.get("github_login"):
        actor.update(kind="human", session_id=opts["github_login"], model_family="human",
                     source_ref=opts.get("source_ref"), lineage=[])  # fmt: skip
        actor["source_sha256"] = _file_sha(opts.get("source_file"))
        return {k: v for k, v in actor.items() if v is not None}
    if env.get(ENV_SESSION):
        if family not in (None, "anthropic"):
            raise IdentityError("IDENTITY_INVALID", f"{ENV_SESSION} is set, family is anthropic")
        session = env[ENV_SESSION]
        actor.update(kind="agent", session_id=session, model_family="anthropic",
                     source_ref=f"env:{ENV_SESSION}",
                     source_sha256=hashlib.sha256(session.encode()).hexdigest())  # fmt: skip
    elif opts.get("dispatch_id"):
        actor.update(kind="agent", session_id=opts["dispatch_id"], model_family=family,
                     source_ref=opts.get("source_ref") or opts.get("source_file"),
                     source_sha256=_file_sha(opts.get("source_file")))  # fmt: skip
    else:
        raise IdentityError("IDENTITY_MISSING", f"no {ENV_SESSION}, --dispatch-id or login")
    actor["lineage"] = _lineage(opts.get("lineage"))
    return {k: v for k, v in actor.items() if v is not None}


def incomplete(actor: Any) -> bool:
    if not isinstance(actor, dict) or any(not actor.get(k) for k in REQUIRED if k != "lineage"):
        return True
    lineage = actor.get("lineage")
    return not isinstance(lineage, list) or not all(isinstance(s, str) for s in lineage)


def independence_problem(adder: Any, confirmer: Any) -> str | None:
    """None when the confirmer may confirm the adder's case; otherwise the refusal code."""
    if incomplete(adder) or incomplete(confirmer):
        return "IDENTITY_INCOMPLETE"
    if confirmer["session_id"] == adder["session_id"]:
        return "SAME_SESSION"
    if adder["session_id"] in confirmer["lineage"] or confirmer["session_id"] in adder["lineage"]:
        return "SAME_LINEAGE"
    if confirmer["kind"] != "human" and confirmer["model_family"] == adder["model_family"]:
        return "SAME_FAMILY"
    return None


CHECK_KEYS = ("method", "evidence_ref", "evidence_sha256")


def confirmation_problem(case: dict[str, Any], event: dict[str, Any]) -> str | None:
    """Checked on every read, so a hand-written confirmation line counts only if it would pass."""
    if event.get("conclusion") not in ("confirmed", "rejected"):
        return "CONFIRM_INVALID"
    check = event.get("check")
    if not isinstance(check, dict) or any(not check.get(k) for k in CHECK_KEYS):
        return "NO_CHECK_EVIDENCE"
    return independence_problem(case.get("adder"), event.get("confirmer"))
