"""Bounded wording for failing visible tests (#1186); text only, never read for a verdict.

The assertion text is written by the code under test, so it is cut, cleaned and withheld whenever
the run holds a private overlay test file: one pytest run that includes any such file gets no
assertion summary at all, only ``deferred: N failed``. The causes already in ``why`` stay.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.node_file import file_part, name_part
from cairnwork.reasons import _DELIMITERS
from cairnwork.textclean import replace_hidden

_SEP = "::"  # between the parts of a node id (node_file splits the file off, #1272)
MAX_TEST_BYTES = 2048  # of one test's failure text taken from the log
MAX_LINE = 160
MAX_NODE = 120
MAX_FAILED = 5
_READ_CAP = 2 << 20
_UNAVAILABLE = "assertion detail unavailable"
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_HEADER = re.compile(r"^_{2,} (.+?) _{2,}$")


def _clean(value, limit: int) -> str:
    """ANSI sequences, control and Unicode format characters (bidi, zero width) gone, the record
    delimiters neutralised, whitespace collapsed, cut to ``limit``."""
    text = replace_hidden(_ANSI.sub("", str(value)), " ")
    text = _DELIMITERS.sub("?", " ".join(text.split()))
    return text[:limit].strip()


def _node(node) -> str:
    node = str(node)
    parts = [_clean(file_part(node), MAX_NODE)]
    parts += [_clean(part, MAX_NODE) for part in name_part(node).split(_SEP) if part]
    return _SEP.join(parts)[:MAX_NODE].lstrip(":")


def assertion_summary(value) -> str:
    """The first pytest ``E `` row of a test's failure text, cleaned; never a source line."""
    if not isinstance(value, str):
        return _UNAVAILABLE
    row = next(
        (line for line in value[:MAX_TEST_BYTES].splitlines() if line.startswith("E ")), None
    )
    if row is None:
        return _UNAVAILABLE
    return "assertion: " + _clean(row, MAX_LINE - len("assertion: "))


def _file(node) -> str:
    return os.path.normpath(file_part(str(node))).lower()


def _append(why, extra):
    return f"{why}; {extra}" if why and extra else why or extra


def _sections(log: str) -> dict[str, list[str]]:
    """pytest's failure sections of a log by the test's name (``Class.test`` or ``test``)."""
    found: dict[str, list[str]] = {}
    name = None
    for line in log.splitlines():
        match = _HEADER.match(line)
        if match:
            name = match.group(1)
            found.setdefault(name, [])
        elif line.startswith("=") and name:
            name = None
        elif name:
            found[name].append(line)
    return found


def _log_detail(sections, node) -> str | None:
    parts = name_part(str(node)).split(_SEP)
    block = sections.get(".".join(parts[-2:])) or sections.get(parts[-1] if parts else "")
    return "\n".join(block)[:MAX_TEST_BYTES] if block is not None else None


def format_failure(
    why, outcomes, log_path, overlay_files, assertions, *, run_files=(), max_failed=MAX_FAILED
):
    """``why`` plus bounded failure detail. Any overlay file in the run: only a failed count."""
    why = why or ""
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    overlay = {os.path.normpath(str(path)).lower() for path in overlay_files or ()}
    failed = [node for node, outcome in outcomes.items() if outcome == "failed"]
    # A test commit can hold data files only, and the base and p2p runs lay the whole commit over
    # the checkout: any overlay at all means the run may read it, whatever pytest collected.
    if overlay:
        secret = [node for node in failed if _file(node) in overlay]
        return _append(why, f"deferred: {len(secret)} failed") if secret else why
    if not isinstance(assertions, dict):
        assertions = {}  # a malformed record is an unrecorded one
    sections = {}
    if isinstance(log_path, Path):
        try:
            with log_path.open("rb") as stream:
                sections = _sections(stream.read(_READ_CAP).decode("utf-8", "replace"))
        except OSError:
            pass
    segments = []
    for node in failed[:max_failed]:
        detail = assertions.get(node)
        detail = detail if isinstance(detail, str) else _log_detail(sections, node)
        summary = assertion_summary(detail)
        if summary != _UNAVAILABLE:
            segments.append(f"{_node(node)} {summary}"[:MAX_LINE])
    if len(failed) > max_failed:
        segments.append(f"+{len(failed) - max_failed} more")
    return _append(why, "; ".join(segments)) if segments else why


def with_failure_detail(why, ctx, spec, verdict, log_path) -> str:
    """The wiring of verifiers._run_one_in: ``why`` plus detail, never an exception, never a
    verdict. An unreadable test commit means the overlay files are unknown: nothing is added."""
    try:
        overlay: tuple[str, ...] = ()
        test_commit = getattr(ctx, "test_commit", None)
        if test_commit:
            base = getattr(ctx, "base_commit", None)
            names = run_git(ctx.repo, "diff", "--name-only", "-z", str(base), test_commit)
            overlay = tuple(name for name in names.split("\0") if name)
        files = (*getattr(spec, "targets", ()), *getattr(verdict, "collected", ()))
        return format_failure(
            why, verdict.outcomes, log_path, overlay, None, run_files=tuple(files)
        )
    except (GitCommandError, OSError, AttributeError, TypeError, ValueError):
        return why
