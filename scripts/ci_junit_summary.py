"""Per-file time and outcome table from a pytest JUnit XML, as markdown (CI job summaries, #1492).

Usage: python3 scripts/ci_junit_summary.py JUNIT_XML [TITLE] [TOP_N]   (writes markdown to stdout)

Needs ``-o junit_family=xunit1`` so that every testcase carries its ``file``. It only reads the XML
pytest wrote; it never fails a job (an unreadable file prints a one-line note).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def summarize(xml_path: Path) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """(per-file stats, totals); stats keys: seconds, passed, failed, skipped."""
    files: dict[str, dict[str, float]] = defaultdict(
        lambda: {"seconds": 0.0, "passed": 0, "failed": 0, "skipped": 0}
    )
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        name = case.get("file") or (case.get("classname") or "?").replace(".", "/") + ".py"
        stats = files[name]
        stats["seconds"] += float(case.get("time") or 0)
        if case.find("failure") is not None or case.find("error") is not None:
            stats["failed"] += 1
        elif case.find("skipped") is not None:
            stats["skipped"] += 1
        else:
            stats["passed"] += 1
    totals = {
        key: sum(s[key] for s in files.values())
        for key in ("seconds", "passed", "failed", "skipped")
    }
    return dict(files), totals


def _cell(text: str) -> str:
    """A file name safe inside a markdown table cell and a code span."""
    return text.replace("\\", "/").replace("|", "\\|").replace("`", "'").replace("\n", " ")


def render(xml_path: Path, title: str, top: int) -> str:
    try:
        files, totals = summarize(xml_path)
    except (OSError, ET.ParseError, ValueError, LookupError) as exc:
        return f"### {title}\n\nno JUnit summary: {type(exc).__name__}\n"
    lines = [
        f"### {title}",
        "",
        f"{len(files)} files, {int(totals['passed'])} passed, {int(totals['failed'])} failed, "
        f"{int(totals['skipped'])} skipped, {totals['seconds']:.0f} s of test time "
        "(CPU, not wall).",
        "",
    ]
    failed = sorted(f for f, s in files.items() if s["failed"])
    if failed:
        lines += ["Files with failures: " + ", ".join(f"`{_cell(f)}`" for f in failed), ""]
    lines += ["| seconds | file | passed | failed | skipped |", "|---:|---|---:|---:|---:|"]
    for name, s in sorted(files.items(), key=lambda kv: -kv[1]["seconds"])[:top]:
        lines.append(
            f"| {s['seconds']:.0f} | `{_cell(name)}` | {int(s['passed'])} | {int(s['failed'])} "
            f"| {int(s['skipped'])} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: ci_junit_summary.py JUNIT_XML [TITLE] [TOP_N]")
    title = sys.argv[2] if len(sys.argv) > 2 else "pytest time per file"
    top = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    print(render(Path(sys.argv[1]), title, top))
