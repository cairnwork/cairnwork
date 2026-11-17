"""Map the trusted pin guard result to a fixed commit status."""

from __future__ import annotations

import os
import sys

OUTCOMES = {
    "success": ("success", "No unapproved guarded pin changes"),
    "unapproved": ("failure", "Guarded pin changes need approval"),
    "error": ("error", "Pin guard could not run or failed closed"),
}


def decide(job_result: str, gate_rc: str) -> str:
    if job_result != "success":
        return "error"
    return {"0": "success", "1": "unapproved", "2": "error"}.get(gate_rc, "error")


def main() -> int:
    state, description = OUTCOMES[
        decide(os.environ.get("PIN_GUARD_JOB_RESULT", ""), os.environ.get("PIN_GUARD_RC", ""))
    ]
    lines = f"state={state}\ndescription={description}\n"
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(lines)
    print(lines, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
