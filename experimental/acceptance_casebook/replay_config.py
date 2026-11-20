"""Redacted state-config provenance for replay records."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def metadata(path: Path | None, runner: Any = None) -> dict[str, str | None]:
    """Presence and the digest of the bytes a runner copied for this case, never config values.

    No digest unless a runner reports one: re-hashing the source path at record time could not
    prove those were the bytes this run used.
    """
    if path is None:
        return {"state_config": "none", "state_config_sha256": None}
    return {
        "state_config": "given",
        "state_config_sha256": getattr(runner, "state_config_sha256", None),
    }
