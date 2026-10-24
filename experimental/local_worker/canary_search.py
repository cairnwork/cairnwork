"""The canary's search probes (#88 robustness; review of PR 251, 7), through the real tool layer:
a positive control (search finds a checkout line), refusals of a search through a symlink out of
the checkout (looking for the secret's prefix, so a leak would match), and the check that
search's Seatbelt profile denies every write: grep itself cannot write, so the wrapper's
search-probe action runs touch on the checkout and the run's tmp under the same profile
(search_profile) as search does.
"""

from __future__ import annotations

import os
from pathlib import Path

from experimental.local_worker.tools import REFUSED_EXIT, WorkerTools


def control(tools: WorkerTools) -> dict:
    found = tools.search("canary-public", "a.txt")
    ok = found.exit_code == 0 and found.output == "a.txt:1:canary-public\n"
    return {"probe": "search a checkout file", "pass": ok}


def refusals(tools: WorkerTools, pattern: str, secret: str) -> list[dict]:
    rows = []
    for path in ("links/link_dir/secret.txt", "links/link_private"):
        call = tools.search(pattern, path)
        ok = call.exit_code == REFUSED_EXIT and secret not in call.output
        rows.append({"probe": f"wrapper search {path}", "pass": ok, "exit_code": call.exit_code})
    return rows


def write_probe(tools: WorkerTools, run_root: Path) -> dict:
    """It must start (not refused), have both writes denied by Seatbelt, and leave neither file
    (removed if it did)."""
    call = tools.search_probe()
    left = []
    for path in (run_root / "checkout" / "search-probe", run_root / "tmp" / "search-probe"):
        if os.path.lexists(path) and not path.is_dir():  # checkout and tmp: the Owner's real dirs
            left.append(str(path))
            path.unlink()
    denied = call.output.count("Operation not permitted")  # touch's own error, once per file
    ok = call.exit_code not in (0, REFUSED_EXIT, None) and denied == 2 and not left
    row = {"probe": "search profile denies writes", "pass": ok, "exit_code": call.exit_code}
    return {**row, "denied_writes": denied}
