"""Offline CLI.

  python -m experimental.counterfactual_ps.cli SANDBOX_LOG PLAIN_OUTPUT
      [--signature-log F] [--guard-tripped]

Reads two existing files and prints one JSON line. It never runs a candidate. Default off: needs
CAIRNWORK_EXPERIMENTAL_COUNTERFACTUAL_PS=1.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from . import compare

ENV = "CAIRNWORK_EXPERIMENTAL_COUNTERFACTUAL_PS"


def main(argv: list[str] | None = None) -> int:
    if os.environ.get(ENV) != "1":
        print(f"disabled: set {ENV}=1 (experimental, diagnostic only)", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("sandbox_log")
    ap.add_argument("plain_output")
    ap.add_argument("--signature-log")
    ap.add_argument("--guard-tripped", action="store_true")
    a = ap.parse_args(argv)
    sig = Path(a.signature_log).read_text(errors="replace") if a.signature_log else None
    res = compare(
        Path(a.sandbox_log).read_text(errors="replace"),
        Path(a.plain_output).read_text(errors="replace"),
        guard_tripped=a.guard_tripped,
        signature_text=sig,
    )
    print(json.dumps(dataclasses.asdict(res), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
