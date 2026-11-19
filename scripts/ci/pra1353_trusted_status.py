"""Publish the trusted #1353 gate result as a commit status (report-only).

Input is the single string the analysis job saved: only "0", "1" and "3" are accepted, every
other value (missing, empty, padded, multi-line, unknown, oversized) is an error. Descriptions are
fixed strings, so no PR-controlled text reaches the status. Nothing here reads PR content.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CONTEXT = "pra1353 gate (trusted)"
ACCEPTED = {
    "0": ("success", "The #1353 witness gate passed on main's gate"),
    "1": ("failure", "The #1353 witness gate failed on this PR's source"),
    "3": ("failure", "The #1353 gate refused the dependencies as untrusted"),
}
INVALID = ("failure", "The #1353 gate result was missing or invalid")
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
MAX_BYTES = 16


def decide(raw: object) -> tuple[str, str, bool]:
    """Return (state, description, valid) for the saved gate_rc value."""
    if isinstance(raw, str) and raw in ACCEPTED:
        state, description = ACCEPTED[raw]
        return state, description, True
    return (*INVALID, False)


def read_rc(path: Path) -> str | None:
    """Read at most MAX_BYTES + 1 bytes of the saved value; None when missing or unreadable."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        return data.decode("ascii") if len(data) <= MAX_BYTES else None
    except (OSError, UnicodeDecodeError):
        return None


def post(repo: str, sha: str, state: str, description: str, url: str) -> int:
    if not SHA.fullmatch(sha) or not REPO.fullmatch(repo):
        print("bad repository or head sha")
        return 1
    cmd = ["gh", "api", f"repos/{repo}/statuses/{sha}", "-f", f"state={state}"]
    cmd += ["-f", f"context={CONTEXT}", "-f", f"description={description}"]
    cmd += ["-f", f"target_url={url}"]
    return subprocess.run(cmd, check=False, timeout=60).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rc-file", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    state, description, valid = decide(read_rc(args.rc_file))
    posted = post(args.repo, args.sha, state, description, args.run_url)
    print(f"state={state}")
    return 0 if valid and posted == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
