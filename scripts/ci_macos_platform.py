"""Which test files are platform-specific, for the macOS job of a pull request (Issue #1684).

A pull request's macOS shards run only these files; the Linux jobs run everything else, three
times. The set is computed from the tree, so a new test file that uses the macOS backend or a
platform check is selected with no list to maintain. The check is a superset on purpose: any
file that mentions a platform signal anywhere in its text runs on macOS (failing closed), and
tests/test_ci_macos_platform_guard.py checks the result against pytest's own collected marks.
"""

from __future__ import annotations

import re
from pathlib import Path

SIGNAL = re.compile(
    r"mark\.sandbox|mark\.verifier_account|verifier_account|VerifierAccount"
    r"|seatbelt|sandbox-exec|sandbox_exec|cairnwork_sandbox"
    r"|REQUIRE_SANDBOX|REQUIRE_VERIFIER"
    r"|sys\.platform|platform\.system|platform\.uname|os\.uname|darwin|skipif",
    re.IGNORECASE,
)


def is_platform_specific(path: Path) -> bool:
    """True when the file's text carries any platform signal (an unreadable file counts as one)."""
    try:
        return SIGNAL.search(path.read_text(encoding="utf-8", errors="replace")) is not None
    except OSError:
        return True


def platform_files(root: Path, paths: list[str]) -> list[str]:
    """The files of ``paths`` (repo-relative) that run on macOS in a pull request."""
    return [p for p in paths if is_platform_specific(root / p)]
