"""Bounded, credential-redacted local worker failure details and private debug logs."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_STDERR_CHARS = 2000
_SECRET_FLAG = re.compile(r"(?i)^--?(?:api[-_]?key|token|password|secret|authorization)$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(\bbearer\s+)[^\s,;\"']+"),
    re.compile(
        r"(?i)([\"']?\b(?:api[_-]?key|access[_-]?key|token|password|passwd|secret|"
        r"private[_-]?key|authorization)[\"']?\s*[=:]\s*[\"']?)[^\s,;\"']+"
    ),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,}"
        r"|xox[abprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        r"\.[A-Za-z0-9_-]+)\b"
    ),
)


def _scrub(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(
            lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", result
        )
    return result


def redact(value: str) -> str:
    """Remove common credential forms before persisting worker output or argv."""
    return _scrub(value)[:MAX_STDERR_CHARS]


def redacted_tail(value: str, max_lines: int, max_chars: int) -> str:
    """Redact the WHOLE text first, then keep its last lines and chars (failures end the text)."""
    return "\n".join(_scrub(value).splitlines()[-max_lines:])[-max_chars:]


def episode_failure(result: Any) -> str:
    """Describe a failed RunResult using process and episode boundary evidence."""
    record = result.record if isinstance(getattr(result, "record", None), dict) else {}
    details = record.get("_local_relay_diagnostics", {})
    fields = [f"process_exit={details.get('process_exit_code', result.code)}"]
    verification = record.get("verification", {})
    termination = record.get("termination", {})
    status = details.get("episode_status") or (
        verification.get("exit_reason") if isinstance(verification, dict) else None
    )
    stop = details.get("stop_reason") or (
        termination.get("abort_cause") or termination.get("limit_hit")
        if isinstance(termination, dict)
        else None
    )
    if status:
        fields.append(f"episode_status={status}")
    if stop:
        fields.append(f"stop_reason={stop}")
    stderr = details.get("stderr")
    if stderr:
        fields.append(f"stderr={redact(str(stderr))}")
    return "local worker did not produce a completed episode (" + "; ".join(fields) + ")"


def append_debug(path: Path | None, event: dict[str, Any]) -> None:
    """Append one redacted JSON record to a mode-0600 log; do nothing without a ledger path."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        payload = json.dumps(_redact_event(event), sort_keys=True, default=str) + "\n"
        with os.fdopen(fd, "a", encoding="utf-8", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
    finally:
        os.close(fd)


def _redact_event(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        result = []
        redact_next = False
        for item in value:
            if redact_next:
                result.append("[REDACTED]")
                redact_next = False
            else:
                result.append(_redact_event(item))
                redact_next = isinstance(item, str) and bool(_SECRET_FLAG.fullmatch(item))
        return result
    if isinstance(value, dict):
        return {key: _redact_event(item) for key, item in value.items()}
    return value
