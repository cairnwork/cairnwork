"""The canary's search probes (#88 robustness; review of PR 251, 7), through the real tool layer:
a positive control (search finds a checkout line), refusals of a search through a symlink out of
the checkout (looking for the secret's prefix, so a leak would match), and the check that
search's Seatbelt profile denies every write: grep itself cannot write, so the wrapper's
search-probe action runs, under the same profile (readonly_profile) as search, list and read,
mkdir in the checkout and in the run's tmp and an open of /dev/null for writing (Codex review of
PR 251, should-fix 2: the devices are closed too).
"""

from __future__ import annotations

import os
from pathlib import Path

from experimental.local_worker.tools import REFUSED_EXIT, WorkerTools


def control(tools: WorkerTools) -> dict:
    found = tools.search("canary-public", "a.txt")
    ok = found.exit_code == 0 and found.output == "a.txt:1:canary-public\n"
    return {"probe": "search a checkout file", "pass": ok}


# The symlinks out of the checkout a search must refuse (canary.build_fixture plants them); each
# row is required by name (canary_check.REQUIRED_PROBES; Codex review of PR 251, should-fix 1).
REFUSAL_PATHS = ("links/link_dir/secret.txt", "links/link_private")
REFUSAL_PROBES = frozenset(f"wrapper search {path}" for path in REFUSAL_PATHS)


def refusals(tools: WorkerTools, pattern: str, secret: str) -> list[dict]:
    rows = []
    for path in REFUSAL_PATHS:
        call = tools.search(pattern, path)
        ok = call.exit_code == REFUSED_EXIT and secret not in call.output
        rows.append({"probe": f"wrapper search {path}", "pass": ok, "exit_code": call.exit_code})
    return rows


PROBE_WRITES = 3  # mkdir in the checkout, mkdir in tmp, /dev/null opened for writing


def write_probe(tools: WorkerTools, run_root: Path) -> dict:
    """It must start (not refused: a probe path that exists already is), have all three writes
    denied by Seatbelt, and leave neither directory (removed if it did)."""
    call = tools.search_probe()
    left = []
    for path in (run_root / "checkout" / "search-probe", run_root / "tmp" / "search-probe"):
        if os.path.lexists(path):
            left.append(str(path))
            path.rmdir() if path.is_dir() and not path.is_symlink() else path.unlink()
    denied = call.output.count("Operation not permitted")  # each write's own error, once
    ok = call.exit_code not in (0, REFUSED_EXIT, None) and denied == PROBE_WRITES and not left
    row = {"probe": "search profile denies writes", "pass": ok, "exit_code": call.exit_code}
    return {**row, "denied_writes": denied}
