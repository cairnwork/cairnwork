"""Configuration for the default-off local relay."""

from __future__ import annotations

import os
from dataclasses import dataclass

FLAG = "CAIRNWORK_LOCAL_RELAY"
DEFAULT_CONTEXT_TOKENS = 32768
RETRY_CONTEXT_TOKENS = 65536
DEFAULT_LEDGER_PATH = "~/.local/state/cairnwork-local-relay/ledger.jsonl"
DEFAULT_LOCAL_ENDPOINT = "http://127.0.0.1:18101/v1"


@dataclass(frozen=True)
class RelayConfig:
    enabled: bool = False
    default_context_tokens: int = DEFAULT_CONTEXT_TOKENS
    retry_context_tokens: int = RETRY_CONTEXT_TOKENS
    gate_model_id: str = "haiku"
    gate_effort: str = "medium"
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> RelayConfig:
        return cls(enabled=os.environ.get(FLAG) == "1")
