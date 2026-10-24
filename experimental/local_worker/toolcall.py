"""One logged call of the local worker's tools (#88): the ToolCall record, its error category and
the clipping of its output for the model (moved here from tools.py, which re-exports them)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

REFUSED_EXIT = 64


def _clip(data: bytes, limit: int) -> tuple[str, bool]:
    """Head and tail of ``data`` within ``limit`` bytes (test output ends with the summary)."""
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    half = limit // 2
    dropped = len(data) - 2 * half
    head = data[:half].decode("utf-8", errors="replace")
    tail = data[-half:].decode("utf-8", errors="replace")
    return f"{head}\n[... {dropped} bytes truncated ...]\n{tail}", True


def error_category(action_type: str, exit_code: int | None, raw: bytes) -> str:
    if exit_code is None:
        return "TIMEOUT"
    if exit_code == 0:
        return "NONE"
    if exit_code == REFUSED_EXIT:
        return "REFUSED"
    if action_type == "search" and exit_code == 1:
        return "NO_MATCH"  # grep found no line: not an error
    if action_type == "run_test":
        if b"SyntaxError" in raw:
            return "SYNTAX_ERROR"
        return {1: "TEST_FAILED", 5: "NO_TESTS"}.get(exit_code, "TOOL_ERROR")
    return "TOOL_ERROR"


@dataclass(frozen=True)
class ToolCall:
    turn_index: int
    action_type: str
    tool_args: dict
    exit_code: int | None
    output: str
    stdout_bytes: int
    stdout_truncated: bool
    error_category: str
    duration_ms: float
    output_sha256: str
    exec_identity: str
    isolation: str
    started_at: str
    raw: bytes = field(default=b"", repr=False, compare=False)
    # False when stdout or stderr was capped (tools.CAPTURE_LIMIT): ``raw`` is then not the whole
    # output, and a git query's result is not observed (Claude review of #239, NB5).
    capture_complete: bool = True

    def record(self) -> dict:
        rec = asdict(self)
        rec.pop("raw")
        rec.pop("output")
        rec["role"] = "tool_result"
        return rec

    def as_turn(
        self, tokens_prompt: int | None, tokens_completion: int | None, tokens_cached: int | None
    ) -> dict:
        turn = self.record()
        turn.update(
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            tokens_cached=tokens_cached,
        )
        return turn
