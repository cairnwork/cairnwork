"""Secret and host identity scanning for PAUSE requests (#476 step 2)."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(r"(?i)(?:sk-ant-|sk-|ghp_|github_pat_|AKIA|xox[bp]-)[A-Za-z0-9_\-]{12,}"),
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("assigned_secret", re.compile(r"(?i)\b(?:key|token|secret)\s*=\s*[A-Za-z0-9+/=_-]{40,}")),
    ("host_path", re.compile(r"/(?:Users/[^/\s]+|home/[^/\s]+)/")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)


def scan(text: str) -> list[str]:
    """Return matched categories only; never include the matched text."""
    return [name for name, pattern in _PATTERNS if pattern.search(text)]


class Scanner:
    """Injectable wrapper for secret scanning."""

    def scan(self, text: str) -> list[str]:
        return scan(text)
