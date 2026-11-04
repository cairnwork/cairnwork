"""Check the worker's change before it finishes, with the rules the Core applies afterwards.

Why: the Core refuses a candidate whose ``git diff --check`` finds whitespace errors
(DIFF_CHECK_FAILED, workspace.py) or that changes a file under a test root
(PROTECTED_VERIFIER_INPUT, pipeline.py), and the agent only learns that after the episode is
over. This module tells it first, as a readable list, so it can fix its own change.

Report only, never a rewrite: nothing here writes the checkout, so a line the agent did not add
cannot be touched and no file type (Markdown hard line breaks) needs an exception. git itself
says which added lines are wrong (the wrapper's ``check`` query, ``git diff --check`` of the
staged tree against HEAD, which looks at added lines only); the header grammar and the known
kinds are the Core's own (cairnwork.diffcheck), and test-root protection is the Core's
``protected_verifier_inputs``. A query that fails, is capped or does not parse gives no report:
the Core still decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cairnwork.diffcheck import _HEADER, _known_kind
from cairnwork.issue_task import DEFAULT_TEST_ROOTS
from cairnwork.textclean import replace_hidden
from cairnwork.verifier_spec import protected_verifier_inputs
from experimental.local_worker.gitstate import parse_digest
from experimental.local_worker.tools import ToolDeadline

MAX_LISTED = 20
MAX_ENTRY_CHARS = 120


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str  # one of diffcheck's known kinds


@dataclass(frozen=True)
class Report:
    whitespace: tuple[Finding, ...] = ()
    protected: tuple[str, ...] = ()

    @property
    def problems(self) -> bool:
        return bool(self.whitespace or self.protected)

    def render(self) -> str:
        rows = [f"{f.path}:{f.line}: {f.kind}" for f in self.whitespace]
        rows += [f"{p}: under a test root; the Core refuses a change to it" for p in self.protected]
        shown = [replace_hidden(r, "")[:MAX_ENTRY_CHARS] for r in rows[:MAX_LISTED]]
        more = [f"(+{len(rows) - MAX_LISTED} more)"] if len(rows) > MAX_LISTED else []
        head = "Not finished: the Core would refuse this change. Nothing was changed for you."
        tail = "Fix these (whitespace on lines you added; undo test-root changes), then finish."
        return "\n".join([head, *shown, *more, tail])


def parse_check(raw: bytes) -> tuple[Finding, ...] | None:
    """The ``check`` query's output as findings; None when it is not strict UTF-8."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    found = []
    for line in text.split("\n"):  # a candidate line is "+content": never a header
        m = _HEADER.fullmatch(line)
        if m and _known_kind(m.group(3)):
            found.append(Finding(m.group(1), int(m.group(2)), m.group(3)))
    return tuple(found)


def protected_paths(raw_digest: bytes, roots: tuple[str, ...]) -> tuple[str, ...] | None:
    """Changed paths the Core would refuse under ``roots``; None when the digest did not parse."""
    records = parse_digest(raw_digest)
    if records is None:
        return None
    changed = tuple(r.split(" ", 4)[4] for r in records)
    # Only the top-level roots: a nested root (pkg/tests) or a protected input above the roots is
    # missed. That only under-reports; the Core still decides.
    under = tuple(p for p in changed if "/" in p and p.split("/", 1)[0] in roots)
    return protected_verifier_inputs(under, roots) if under else ()


def precheck(tools: Any, roots: tuple[str, ...] = DEFAULT_TEST_ROOTS) -> Report:
    """Run the two queries; a query that did not complete adds nothing to the report."""
    check = tools._call("check", "check", [], {})  # git diff --check, staged tree vs HEAD
    digest = tools.digest()
    whitespace = parse_check(check.raw) if _whole(check) else None
    protected = protected_paths(digest.raw, roots) if _whole(digest) else None
    return Report(whitespace or (), protected or ())


def _whole(call: Any) -> bool:
    return call.exit_code == 0 and call.capture_complete


class PrecheckMixin:
    """The AgentLoop's use of the report; ``settings``, ``tools``, ``limits`` and
    ``_step_deadline`` are the loop's."""

    settings: Any
    tools: Any
    limits: Any
    clock: Any
    _step_deadline: Any
    _prechecked = False  # the one reply (settings.precheck_roots) was used

    def _precheck_reply(
        self, log: Any, args: dict, messages: list[dict], call_id: str, deadline: float
    ) -> bool:
        """Once per run, a DONE finish with problems gets the report as its tool reply.
        A query that fails or runs out of time adds nothing: the Core still decides. A
        WorkerIsolationError from a query still aborts the run, as everywhere in the loop.
        No report when it would take the last turn or the last of the time: the finish stands."""
        roots = self.settings.precheck_roots
        if roots is None or self._prechecked or args["status"] != "DONE":
            return False
        if log.index + 1 >= self.limits.max_steps or self.clock() >= deadline:
            return False
        self._prechecked = True
        self.tools.turn_index = log.index
        self._step_deadline()
        try:
            report = precheck(self.tools, roots)
        except ToolDeadline:
            return False
        if not report.problems:
            return False
        log.action_type, log.tool_args = "finish_precheck", args
        messages.append({"role": "tool", "tool_call_id": call_id, "content": report.render()})
        return True
