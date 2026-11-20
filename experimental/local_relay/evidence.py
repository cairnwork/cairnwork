"""Failure evidence for the relay: why Core refused, what the secret scan hit, why the gate
gave no verdict. Diagnostics only: nothing here changes a verdict or an acceptance result.

Everything stored is redacted and bounded; the secret scan records categories and line
numbers, never the matched text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from experimental.local_relay.diagnostics import redact
from experimental.pause.secrets_scan import Scanner

MAX_REASON = 600
MAX_LOG_LINES = 40
MAX_LOG_CHARS = 4000
MAX_SCAN_LINES = 3000
MAX_HIT_LINES = 8


def summarize_result(result: Any, keep_logs: bool = True) -> tuple[dict[str, Any] | None, str]:
    """The part of a ``verify_candidate`` result that explains a refusal or failure."""
    if not isinstance(result, dict):
        return None, ""
    logs = result.get("logs")
    lines = [str(item) for item in logs] if isinstance(logs, list) else []
    tail = redact("\n".join(lines[-MAX_LOG_LINES:]))[-MAX_LOG_CHARS:]
    summary: dict[str, Any] = {
        "status": result.get("status"),
        "core_status": result.get("core_status"),
        "reason": redact(str(result.get("reason") or ""))[:MAX_REASON],
        "uncomparable": result.get("uncomparable"),
        "tests_consumed": result.get("tests_consumed"),
        "logs_tail": tail if keep_logs else None,
    }
    return {k: v for k, v in summary.items() if v not in (None, "")}, tail


class RecordingVerify:
    """Wrap a ``verify`` callable for CoreJudge and keep a summary of its last result."""

    def __init__(
        self, inner: Callable[..., Any], log_sink: Callable[[str], None] | None = None
    ) -> None:
        """With ``log_sink`` (hidden tests in use) the verifier log tail goes only to the sink
        (the 0600 debug log); it is left out of the summary that reaches the ledger."""
        self.inner = inner
        self.log_sink = log_sink
        self.last: dict[str, Any] | None = None

    def __call__(self, intake: Any, **kwargs: Any) -> Any:
        self.last = None
        result = self.inner(intake, **kwargs)
        self.last, tail = summarize_result(result, keep_logs=self.log_sink is None)
        if self.log_sink is not None and tail:
            self.log_sink(tail)
        return result


class RecordingScanner:
    """Scanner that remembers which categories hit and on which lines (never the text)."""

    def __init__(self, inner: Any | None = None) -> None:
        self.inner = inner or Scanner()
        self.last: dict[str, Any] | None = None

    def reset(self) -> None:
        self.last = None

    def scan(self, text: str) -> list[str]:
        try:
            hits = self.inner.scan(text)
        except Exception as exc:  # noqa: BLE001 - recorded, then the caller fails closed
            self.last = {"scanner_error": type(exc).__name__}
            raise
        if hits:
            lines = []
            for number, line in enumerate(text.splitlines()[:MAX_SCAN_LINES], 1):
                if self.inner.scan(line):
                    lines.append(number)
                if len(lines) >= MAX_HIT_LINES:
                    break
            self.last = {
                "categories": sorted({str(hit) for hit in hits}),
                "hit_lines": lines,
                "text_chars": len(text),
            }
        return hits


def bounded_json(value: Any, limit: int = 6000) -> str:
    return json.dumps(value, sort_keys=True, default=str)[:limit]
