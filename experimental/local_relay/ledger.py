"""Append-only JSONL cost ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from experimental.local_relay.diagnostics import redact_deep


class Ledger(Protocol):
    def append(self, row: dict[str, Any]) -> None: ...


class JsonlLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redact_deep(row), sort_keys=True) + "\n")
