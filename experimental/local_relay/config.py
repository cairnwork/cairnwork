"""Configuration for the default-off local relay."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FLAG = "CAIRNWORK_LOCAL_RELAY"
MASK_SECRET_LINES_FLAG = "RELAY_MASK_SECRET_LINES"
MASKED_ACCEPT_PASSES_FLAG = "RELAY_MASKED_ACCEPT_PASSES"
DEFAULT_CONTEXT_TOKENS = 32768
RETRY_CONTEXT_TOKENS = 65536
DEFAULT_LEDGER_PATH = "~/.local/state/cairnwork-local-relay/ledger.jsonl"
DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:18101/v1"
DEFAULT_HAIKU_PROXY_URL = "http://127.0.0.1:18301/v1"
HAIKU_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class RelayConfig:
    enabled: bool = False
    default_context_tokens: int = DEFAULT_CONTEXT_TOKENS
    retry_context_tokens: int = RETRY_CONTEXT_TOKENS
    gate_model_id: str = "haiku"
    gate_effort: str = "medium"
    timeout_seconds: float = 60.0
    # Builds what the gate reviews from a candidate; default is the candidate's string form.
    plan_for: Callable[[Any], str] | None = None
    mask_secret_lines: bool = False  # last two: existing positional callers stay unchanged
    masked_accept_passes: bool = False  # default: an ACCEPT of a withheld plan is MASKED_ACCEPT

    @classmethod
    def from_env(cls) -> RelayConfig:
        return cls(
            enabled=os.environ.get(FLAG) == "1",
            mask_secret_lines=os.environ.get(MASK_SECRET_LINES_FLAG) == "1",
            masked_accept_passes=os.environ.get(MASKED_ACCEPT_PASSES_FLAG) == "1",
        )
