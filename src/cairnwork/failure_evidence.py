"""Record-only diagnostics for failed verifier runs (#1105).

Evidence is best effort: it is never an input to observations, comparisons, or verdicts. Every
string here is candidate-controlled text after best-effort masking (``mask_secrets``, a ``<home>``
swap): treat it as untrusted. Node ids are masked and cut to ``MAX_ID`` characters.

A log tail or node id is kept only when the run used the dedicated verifier account: without it a
candidate can read the launching account's plaintext secrets and print them (or put them in a
parametrize id), and the masker misses bare tokens (docs/SECURITY.md: never hand a raw verifier
log to a worker or a model); only the count is kept then.

Deferred tests stay hidden (the pipeline deletes base logs for the same reason): with a test commit
an entry keeps only how many tests failed, no tail and no node id, since a deferred conftest can
print into any test's output and the test commit may change files the pipeline does not name. The
count comes from outcomes the candidate can forge: a small integer, accepted.
"""

from __future__ import annotations

import re
from pathlib import Path

from cairnwork.p2p_probe_log import mask_secrets

MAX_NODEIDS = 20
MAX_LINES = 200
MAX_BYTES = 16 * 1024
MAX_ID = 200
MAX_LINE = 512  # the masker is superlinear: cut each kept line before masking it
# mask over a bounded window longer than what is shown, so a cut does not hide half a secret
# from the masker
PRE_MASK = 4
_OPEN_URL = re.compile(r"://[^ \t\n@]*$")  # a URL tail the masker never saw end: linear cost
WINDOW = 4 * MAX_BYTES  # read only the end of a log (64 KiB)


def capture(runs, *, home: str, hidden: bool = False, account: bool = False) -> list[dict]:
    """Build bounded evidence for each nonpassing verifier; any caller failure must be ignored."""
    evidence = []
    for run in runs:
        if run.observation.passed:
            continue
        failed = sorted(  # setup/teardown errors are "failed" outcomes; collectors are apart
            {node for node, outcome in run.outcomes.items() if outcome == "failed"}
            | set(run.collect_errors)
        )
        withheld = hidden or not account  # ids are candidate text too (parametrize ids)
        # mask only what will be shown, after cutting count and length: the masker is superlinear
        kept = [] if withheld else failed[:MAX_NODEIDS]
        pairs = [_cut_open_url(mask_secrets(node[: MAX_ID * PRE_MASK])[:MAX_ID]) for node in kept]
        shown = [text for text, _ in pairs]
        tail, truncated, unavailable = "", False, False
        if not withheld:
            try:
                tail, truncated = _tail(Path(run.log_path), home)
            except Exception:  # noqa: BLE001 - evidence is best effort and cannot affect a verdict
                unavailable = True
        evidence.append(
            {
                "id": run.spec.verifier_id,
                "failed_nodeids": shown,
                "failed_nodeids_truncated": not withheld
                and (
                    len(failed) > MAX_NODEIDS
                    or any(len(node) > MAX_ID for node in kept)
                    or any(was_cut for _, was_cut in pairs)
                ),
                "failed_count": len(failed),
                "log_tail": tail,
                "log_tail_truncated": truncated,
                "log_tail_withheld": withheld,
                "log_tail_unavailable": unavailable,
            }
        )
    return evidence


def capture_for_task(runs, task, ctx) -> list[dict]:
    """Attach diagnostics; with a test commit its tests are hidden. Never raises."""
    try:
        hidden, account = bool(task.test_commit), ctx.account is not None
        home = str(Path.home().resolve())
        return capture(runs, home=home, hidden=hidden, account=account)
    except Exception as exc:  # noqa: BLE001 - record-only collection must never affect judgment
        return [
            {"id": "", "log_tail_unavailable": True, "failure_evidence_error": type(exc).__name__}
        ]


def _tail(path: Path, home: str) -> tuple[str, bool]:
    with path.open("rb") as log:
        size = log.seek(0, 2)
        log.seek(max(0, size - WINDOW))
        raw = log.read(WINDOW).decode("utf-8", errors="replace")
    if size > WINDOW:  # a cut first line could hide half a secret from the masker: drop it
        raw = raw.partition("\n")[2]
    # split on "\n" only: other line breaks stay inside a line, where the masker sees them
    pieces = raw.split("\n")
    lines = [piece + "\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])
    kept = lines[-MAX_LINES:]
    pairs = [_cut_open_url(mask_secrets(line[: MAX_LINE * PRE_MASK])[:MAX_LINE]) for line in kept]
    redacted = [text.replace(home, "<home>") if home else text for text, _ in pairs]
    encoded = "".join(redacted).encode("utf-8")
    cut = (
        size > WINDOW
        or len(lines) > MAX_LINES
        or any(len(line) > MAX_LINE for line in kept)
        or any(was_cut for _, was_cut in pairs)
        or len(encoded) > MAX_BYTES
    )
    return encoded[-MAX_BYTES:].decode("utf-8", errors="ignore"), cut


def _cut_open_url(text: str) -> tuple[str, bool]:
    """Replace a URL tail with no end in sight: the masker needs the ``@`` or a space to see it."""
    found = _OPEN_URL.search(text)
    if found is None:
        return text, False
    return text[: found.start()] + "<cut>" + text[found.end() :], True
