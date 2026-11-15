"""Command-line interface for experimental committed_sys_path.

Requires CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH=1 environment variable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from experimental.committed_sys_path import check_commit


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for committed_sys_path CLI."""
    parser = argparse.ArgumentParser(
        prog="committed_sys_path",
        description="Verify candidate search-path modifications point only to base committed dirs.",
    )
    parser.add_argument("--repo", type=str, default=".", help="Path to repository.")
    parser.add_argument("--base", type=str, required=True, help="Base commit SHA or reference.")
    parser.add_argument(
        "--candidate", type=str, required=True, help="Candidate commit SHA or reference."
    )
    parser.add_argument(
        "--include-tests", action="store_true", help="Include test files in search-path checks."
    )
    parser.add_argument(
        "--allowlist-prefix",
        action="append",
        default=[],
        dest="allowlist_prefixes",
        help="Prefixes to allow without checking (e.g. experimental/).",
    )
    return parser


def _sanitize_log(value: object) -> str:
    """Neutralize control characters and workflow commands before logging."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace("::", ": :").replace("##[", "# #[")


def main(argv: list[str] | None = None) -> int:
    """Entry point for committed_sys_path CLI."""
    if os.environ.get("CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH") != "1":
        sys.stderr.write(
            "committed_sys_path is experimental and disabled by default. "
            "Set CAIRNWORK_EXPERIMENTAL_COMMITTED_SYS_PATH=1 to enable.\n"
        )
        return 2

    parser = build_parser()
    args = parser.parse_args(argv)

    verdict = check_commit(
        repo=Path(args.repo),
        base=args.base,
        candidate=args.candidate,
        include_tests=args.include_tests,
        allowlist_prefixes=tuple(args.allowlist_prefixes),
    )

    sys.stdout.write(
        f"Verdict: {_sanitize_log(verdict.verdict)} ({_sanitize_log(verdict.reason)})\n"
    )
    if verdict.findings:
        for f in verdict.findings:
            sys.stdout.write(f"  - {_sanitize_log(f)}\n")

    if verdict.verdict == "ACCEPT":
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
