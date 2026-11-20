"""Debug only: keep a copy of the candidate tree before acceptance (CW_DEBUG_KEEP_CANDIDATE_DIR)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ENV_VAR = "CW_DEBUG_KEEP_CANDIDATE_DIR"


def keep_candidate(result: Any) -> None:
    """Copy the candidate checkout, base.txt and candidate.patch; never raises, off by default."""
    root = os.environ.get(ENV_VAR)
    if not root or getattr(result, "checkout", None) is None:
        return
    try:
        checkout = Path(result.checkout)
        base = getattr(result, "base_sha", None)
        name = f"{checkout.parent.name}-{checkout.name}-{time.strftime('%Y%m%dT%H%M%S')}"
        dest = Path(root) / name
        dest.mkdir(parents=True, exist_ok=False)
        shutil.copytree(checkout, dest / "tree", symlinks=True)
        (dest / "base.txt").write_text(f"{base}\n")
        if isinstance(base, str):
            patch = subprocess.run(
                ["git", "-C", str(checkout), "diff", "--no-ext-diff", "--no-textconv", base],
                capture_output=True,
                timeout=60,
                check=False,
            )
            (dest / "candidate.patch").write_bytes(patch.stdout)
    except Exception as exc:  # debug aid must never change the flow
        print(f"{ENV_VAR}: keep failed: {exc!r}", file=sys.stderr)
