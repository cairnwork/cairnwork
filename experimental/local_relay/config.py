"""Configuration for the default-off local relay."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FLAG = "CAIRNWORK_LOCAL_RELAY"
DEFAULT_CONTEXT_TOKENS = 32768
RETRY_CONTEXT_TOKENS = 65536
DEFAULT_MAX_STEPS = 200  # experiment values for the local worker loop
DEFAULT_MAX_WALL_SECONDS = 960.0
DEFAULT_LEDGER_PATH = "~/.local/state/cairnwork-local-relay/ledger.jsonl"
DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:18101/v1"
DEFAULT_HAIKU_PROXY_URL = "http://127.0.0.1:18301/v1"
HAIKU_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class RelayConfig:
    enabled: bool = False
    default_context_tokens: int = DEFAULT_CONTEXT_TOKENS
    retry_context_tokens: int = RETRY_CONTEXT_TOKENS
    max_steps: int = DEFAULT_MAX_STEPS
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS
    gate_model_id: str = "haiku"
    gate_effort: str = "medium"
    timeout_seconds: float = 60.0
    # Builds what the gate reviews from a candidate; default is the candidate's string form.
    plan_for: Callable[[Any], str] | None = None

    @classmethod
    def from_env(cls) -> RelayConfig:
        return cls(enabled=os.environ.get(FLAG) == "1")
