"""When the secret scan trips, the gate gets a summary that holds none of the original text.

Experiment only (``RELAY_MASK_SECRET_LINES=1``). Nothing from the plan or the test output is
copied: only integers and rule category names (no file or test names either). If even that
summary trips the scanner, nothing is sent.
"""

from __future__ import annotations

import re

from experimental.pause.secrets_scan import Scanner

_MARKER = re.compile(r"-{3,}\s*BEGIN\b")  # any PEM-looking header, well formed or not
_CATEGORY = re.compile(r"[a-z0-9_]{1,40}")
_DIFF_FILE = re.compile(r"^diff --git a/(\S+) b/", re.MULTILINE)
_NEW_FILE = re.compile(r"^New file (\S+):$", re.MULTILINE)
_COUNT = re.compile(r"\b(\d{1,6}) (passed|failed|errors?|skipped)\b")


def scan_categories(text: str) -> list[str]:
    """Rule names that match anywhere in ``text``; a broken scanner counts as a hit."""
    try:
        scanner = Scanner()
        found = set(scanner.scan(text))  # the scanner joins keys broken over several lines
        for line in text.splitlines():
            found.update(scanner.scan(line))
    except Exception:  # noqa: BLE001 - never echo the error: it may hold the scanned text
        return ["scanner_error"]
    if _MARKER.search(text):
        found.add("pem_marker")
    return sorted(found)


def _summary(categories: list[str], plan: str, raw: str) -> str:
    files = list(dict.fromkeys([*_DIFF_FILE.findall(plan), *_NEW_FILE.findall(plan)]))
    added = sum(1 for ln in plan.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in plan.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    counts: dict[str, int] = {}
    for number, word in _COUNT.findall(raw):
        counts[word.rstrip("s") if word.startswith("error") else word] = int(number)
    lines = [
        "[gate plan withheld: the secret scan matched it; this summary holds no original text]",
        "scan categories: " + (", ".join(c for c in categories if _CATEGORY.fullmatch(c)) or "-"),
        f"files changed: {len(files)}",
        f"diff lines: +{added} -{removed}",
        "test counts: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"),
    ]
    return "\n".join(lines) + "\n"


def guard_plan(plan: str, raw: str) -> tuple[str | None, list[str]]:
    """(text to send, scan categories). Clean plan: unchanged. Otherwise a summary, or None."""
    categories = scan_categories(plan)
    if not categories:
        return plan, []
    summary = _summary(categories, plan, raw)
    if scan_categories(summary):  # the residual scan: the summary itself must be clean
        return None, categories
    return summary, categories
