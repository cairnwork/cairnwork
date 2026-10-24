"""read_file pages (#88 robustness, L0 dry run 3): sliced in Python from what the wrapper already
printed, so the wrapper (and its frozen sha256) is unchanged. Paging is off by default.

Paging: read_file takes an optional line range (start_line, end_line: 1-based, inclusive) in
every mode, and the loop serves exactly those lines of the wrapper's output. With
Limits.read_page_lines on, a read shows at most that many lines (a read without a range: the
first page), and with read_page_bytes at most that many bytes of whole lines (at least one line,
clipped if it alone is larger). A paged or ranged observation ends with a note naming the lines
shown and, unless the file ended, the start_line that continues. A read with no range and paging
off is passed through unchanged, as is a failed read or one whose capture was capped. The turn
keeps the wrapper's stdout_bytes and output_sha256 (the whole file); stdout_truncated is true
when the model was shown less than the whole output.
"""

from __future__ import annotations

from dataclasses import replace

from experimental.local_worker.toolcall import ToolCall, _clip


def page(
    call: ToolCall,
    start: int | None,
    end: int | None,
    page_lines: int | None,
    page_bytes: int | None,
    max_bytes: int,
) -> ToolCall:
    """The observation of one read_file: its range or page of the wrapper's output (above)."""
    if start is None and end is None and page_lines is None:
        return call
    if call.exit_code != 0 or not call.capture_complete:
        return call
    lines = call.raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    n, first = len(lines), start or 1
    if first > n:
        note = f"[start_line {first} is past the end: the file has {n} lines]"
        return replace(call, output=note, stdout_truncated=n > 0)
    last = n if end is None else min(end, n)
    if page_lines is not None:
        last = min(last, first + page_lines - 1)
    shown = lines[first - 1 : last]
    if page_bytes is not None:
        size, keep = 0, 0
        for line in shown:
            size += len(line.encode())
            if keep and size > page_bytes:
                break
            keep += 1
        shown, last = shown[:keep], first + keep - 1
    cap = max_bytes if page_bytes is None else min(max_bytes, page_bytes)
    body, clipped = _clip("".join(shown).encode(), cap)
    more = f"call read_file with start_line={last + 1} to continue" if last < n else "end of file"
    sep = "" if not body or body.endswith("\n") else "\n"
    output = f"{body}{sep}[lines {first}-{last} of {n}; {more}]"
    return replace(call, output=output, stdout_truncated=clipped or first > 1 or last < n)
