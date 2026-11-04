"""Who added and who confirmed a case (#772 AC1b). A display name is recorded but never compared.

- Claude Code agent: ``session_id`` comes from the harness env ``CLAUDE_CODE_SESSION_ID`` (never a
  flag) and ``model_family`` is ``anthropic``; a flag naming another family, or a human login while
  that variable is set, is refused.
- Other agent (Agy, GPT lane, Codex): ``--dispatch-id`` plus the lane's ``--model-family`` (one of
  FAMILIES, spelled exactly) and its output or transcript file (``--source-file``).
- Human: ``--github-login`` with ``--source-ref`` (a GitHub comment URL) and the saved body.
``lineage`` is the ancestor session ids, root first; exactly ``root`` means none.

Ids, logins and lineage items are normalised (NFKC, invisible characters removed, stripped,
casefolded) on write and checked again on read, and must then be plain ``[a-z0-9._:@/-]``. Source
files must be non-empty; their digests are recomputed on every read. Human and non-Claude
identities and every lineage are self-declared and not verified: one local user owns every
input, so this records honest attribution and catches mix-ups, it does not stop that user.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ENV_SESSION = "CLAUDE_CODE_SESSION_ID"
FAMILIES = ("anthropic", "google", "openai", "deepseek", "xai", "meta", "mistral", "alibaba")
REQUIRED = ("kind", "session_id", "lineage", "model_family", "source_ref", "source_sha256")
_ID = re.compile(r"[a-z0-9._:@/-]+")
_COMMENT_URL = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(issues|pull)/\d+"
    r"#(issuecomment|discussion_r|pullrequestreview)-?\d+"
)


class IdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


def norm_id(raw: str) -> str:
    """NFKC, drop invisible (format) characters, strip, casefold; then plain characters only."""
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf").strip().casefold()
    if not _ID.fullmatch(text):
        raise IdentityError("IDENTITY_INVALID", f"id {raw!r} has characters outside [a-z0-9._:@/-]")
    return text


def file_sha(path: str | None) -> str | None:
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise IdentityError("IDENTITY_INVALID", f"source file unreadable: {path!r}") from exc
    if not data.strip():
        raise IdentityError("IDENTITY_INVALID", f"source file is empty: {path!r}")
    return hashlib.sha256(data).hexdigest()


def _lineage(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    if raw == "root":
        return []
    items = [norm_id(p) for p in raw.split(",") if p.strip()]
    if "root" in items:
        raise IdentityError("IDENTITY_INVALID", "only exactly 'root' means no ancestors")
    return items or None


def _family(raw: str | None) -> str | None:
    if raw is not None and raw not in FAMILIES:
        raise IdentityError("IDENTITY_INVALID", f"model family {raw!r} is not one of {FAMILIES}")
    return raw


def resolve_actor(env: Mapping[str, str], opts: Mapping[str, str | None]) -> dict[str, Any]:
    family = _family(opts.get("model_family"))
    src = opts.get("source_file")
    opts = {**opts, "source_file": str(Path(src).resolve()) if src else None}
    actor: dict[str, Any] = {"display": opts.get("display_name")}
    if opts.get("github_login"):
        if env.get(ENV_SESSION):
            raise IdentityError("IDENTITY_INVALID", f"{ENV_SESSION} is set: not a human")
        ref = opts.get("source_ref") or ""
        if not _COMMENT_URL.fullmatch(ref):
            raise IdentityError("IDENTITY_INVALID", "--source-ref must be a GitHub comment URL")
        actor.update(kind="human", session_id=norm_id(str(opts["github_login"])),
                     model_family="human", source_ref=ref, lineage=[],
                     source_file=opts.get("source_file"),
                     source_sha256=file_sha(opts.get("source_file")))  # fmt: skip
    elif env.get(ENV_SESSION):
        if family not in (None, "anthropic"):
            raise IdentityError("IDENTITY_INVALID", f"{ENV_SESSION} is set, family is anthropic")
        session = norm_id(env[ENV_SESSION])
        actor.update(kind="agent", session_id=session, model_family="anthropic",
                     source_ref=f"env:{ENV_SESSION}", lineage=_lineage(opts.get("lineage")),
                     source_sha256=hashlib.sha256(session.encode()).hexdigest())  # fmt: skip
    elif opts.get("dispatch_id"):
        actor.update(kind="agent", session_id=norm_id(str(opts["dispatch_id"])),
                     model_family=family, lineage=_lineage(opts.get("lineage")),
                     source_ref=opts.get("source_ref") or opts.get("source_file"),
                     source_file=opts.get("source_file"),
                     source_sha256=file_sha(opts.get("source_file")))  # fmt: skip
    else:
        raise IdentityError("IDENTITY_MISSING", f"no {ENV_SESSION}, --dispatch-id or login")
    return {k: v for k, v in actor.items() if v is not None}


def _valid_id(value: Any) -> bool:
    try:
        return isinstance(value, str) and norm_id(value) == value
    except IdentityError:
        return False


def _source_holds(actor: dict[str, Any]) -> bool:
    if actor["source_ref"] == f"env:{ENV_SESSION}":
        expected = hashlib.sha256(actor["session_id"].encode()).hexdigest()
        return actor["kind"] == "agent" and actor["source_sha256"] == expected
    try:
        return file_sha(actor.get("source_file")) == actor["source_sha256"]
    except IdentityError:
        return False


def incomplete(actor: Any) -> bool:
    """True unless every part is present, normalised, allowed and its source digest re-checks."""
    if not isinstance(actor, dict) or any(not actor.get(k) for k in REQUIRED if k != "lineage"):
        return True
    lineage = actor.get("lineage")
    if not isinstance(lineage, list) or not all(_valid_id(s) and s != "root" for s in lineage):
        return True
    human = actor["kind"] == "human"
    if human != (actor["model_family"] == "human") or actor["kind"] not in ("human", "agent"):
        return True
    if not human and actor["model_family"] not in FAMILIES:
        return True
    if human and not _COMMENT_URL.fullmatch(str(actor["source_ref"])):
        return True
    return not _valid_id(actor["session_id"]) or not _source_holds(actor)


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
    try:
        if file_sha(str(check["evidence_ref"])) != check["evidence_sha256"]:
            return "NO_CHECK_EVIDENCE"
    except IdentityError:
        return "NO_CHECK_EVIDENCE"
    return independence_problem(case.get("adder"), event.get("confirmer"))
