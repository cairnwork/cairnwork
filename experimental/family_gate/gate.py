"""One gate pass over the queued Issues, and the experiment's sidecar record.

Run under the Issue runner's ``issues.lock`` (``__main__``), before ``issue_runner.take``. For
every trusted queued Issue whose ``eia-task`` block core would take, it decides (``families``)
and appends one sidecar line per (Issue, task block, annotation, worker model). A REFUSED Issue
is moved from ``cairnwork:queue`` to ``cairnwork:family-hold`` (``HOLD``; create the label on
the target repository before enabling) and then gets one comment; a runner of another family
can put it back in the queue by hand. The gate never adds ``cairnwork:queue`` itself.

The hold is three GitHub calls, and a pass can stop between any two (#646). The hold is written
to the experiment's journal first, the queue label comes off before the hold label goes on, and
every step is safe to repeat: a stopped hold leaves the Issue out of the queue, so no ``take``
lists it, gate on or off, and the next gate pass finishes it from the journal.

``take`` sees only what the gate let through (``Held``): Issues it decided and did not refuse,
and Issues whose task block core refuses (core comments on those, once). Everything else this
pass lists stays untaken: untrusted when the gate looked, a GitHub error, a refusal whose hold
stopped halfway; an Issue queued after the gate's listing waits for the next pass too.

The sidecar lives in the experiment's own directory: never the intake state, never a receipt.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairnwork.intake import Intake
from cairnwork.issue_github import GitHub
from cairnwork.issue_runner import QUEUE
from cairnwork.issue_task import IssueRefused, parse_task_block
from experimental.family_gate.families import REFUSED, Verdict, decide, parse_annotation

SIDECAR_NAME = "records.jsonl"
HOLDS_NAME = "holds.json"  # holds started and not yet finished: Issue number -> its comment
# Only this experiment reads it: core's needs-fix means the task block itself is wrong (fd31).
HOLD = "cairnwork:family-hold"
DEFAULT_SIDECAR = Path.home() / ".local/state/cairnwork-family-gate"
ROLE = {"issue_author": "工單作者", "test_author": "驗收測試作者"}


class Held:
    """The runner's GitHub whose ``issues(QUEUE)`` is the gate's listing, only the let-through.

    ``take`` never re-lists the queue, and nothing the gate did not let through reaches it."""

    def __init__(self, github: GitHub, listed: list[dict[str, Any]], through: set[int]) -> None:
        self._github = github
        self._queue = [i for i in listed if int(i["number"]) in through]

    def issues(self, label: str) -> list[dict[str, Any]]:
        return list(self._queue) if label == QUEUE else self._github.issues(label)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._github, name)


def refusal_comment(verdict: Verdict, worker_model: str) -> str:
    """Only table family names and the operator's worker model; nothing echoed from the Issue."""
    key = "issue_author" if verdict.reason.endswith("ISSUE_AUTHOR") else "test_author"
    return (
        f"Cairnwork 家族閘門（實驗，experimental/family_gate）沒有接這張：`{verdict.reason}`。"
        f"{ROLE[key]}宣告的家族 `{verdict.families[key]}` 和寫程式的 worker（`{worker_model}`，"
        f"家族 `{verdict.families['worker']}`）同家。"
        "家族是 Issue 作者自填的實驗標記，不是安全保證。"
        f"換別家寫工單或測試並更新 `cairnwork-families` 區塊，移除 `{HOLD}` 再加上 `{QUEUE}`。"
    )


class Sidecar:
    def __init__(self, root: Path, repo: str) -> None:
        self.dir = root / repo.replace("/", "__")
        self.path = self.dir / SIDECAR_NAME
        self.holds_path = self.dir / HOLDS_NAME

    def _seen(self) -> set[tuple]:
        if not self.path.exists():
            return set()
        keys = set()
        for line in self.path.read_text().splitlines():
            row = json.loads(line)
            keys.add(_key(row))
        return keys

    def append(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Write the rows not recorded before; returns them. Raises OSError on failure."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        seen = self._seen()
        new = [r for r in rows if _key(r) not in seen]
        if new:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as out:
                out.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in new))
        return new

    def holds(self) -> dict[int, str]:
        """The journal of unfinished holds; a journal not of that shape raises ValueError."""
        if not self.holds_path.exists():
            return {}
        data = json.loads(self.holds_path.read_text())
        if not isinstance(data, dict) or not all(isinstance(t, str) for t in data.values()):
            raise ValueError(f"{HOLDS_NAME} is not a hold journal")
        return {int(number): text for number, text in data.items()}

    def set_holds(self, holds: dict[int, str]) -> None:
        """Replace the journal in one rename. Raises OSError on failure."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.dir / f".{HOLDS_NAME}.tmp"
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump({str(n): t for n, t in holds.items()}, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, self.holds_path)


def _key(row: dict[str, Any]) -> tuple:
    return (row["repo"], row["issue"], row["block_sha256"], row["annotation_sha256"],
            row["worker"]["model"])  # fmt: skip


def run_gate(
    intake: Intake, github: GitHub, sidecar: Sidecar, worker_model: str
) -> tuple[Held, dict[str, Any]]:
    """(the GitHub ``take`` gets, the pass summary). Only an Issue the gate let through reaches
    ``take``; the rest stay untaken: decided again, or their hold finished, on the next pass."""
    roots = tuple(intake.config()["test_roots"])
    started = sidecar.holds()  # an unreadable journal stops the gate before anything changes
    listed = github.issues(QUEUE)
    rows, refused, through, untrusted, errors = [], [], set(), [], []
    for issue in listed:
        number = int(issue["number"])
        try:
            if github.trust(number, QUEUE):
                untrusted.append(number)  # held; the gate says nothing about untrusted text
                continue
        except (RuntimeError, OSError) as exc:  # undecided: not taken this pass
            errors.append({"issue": number, "error": f"{type(exc).__name__}: {exc}"})
            continue
        body = str(issue.get("body") or "")
        try:
            block_sha = parse_task_block(body, roots).block_sha256
        except IssueRefused:
            through.add(number)  # core refuses the block itself, once; nothing is taken
            continue
        annotation = parse_annotation(body)
        verdict = decide(annotation, worker_model)
        rows.append(_row(github.repo, number, block_sha, annotation, verdict, worker_model))
        if verdict.verdict == REFUSED:
            refused.append((number, verdict))
        else:
            through.add(number)
    new = sidecar.append(rows)  # before any refusal: an unrecorded decision is not acted on
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    queued = {int(i["number"]) for i in listed}  # back in the queue: decided again above
    holds = {n: text for n, text in started.items() if n not in queued}
    holds |= {n: refusal_comment(v, worker_model) for n, v in refused}
    errors += _hold_all(github, sidecar, started, holds)
    summary = {"counts": counts, "recorded": len(new), "refused": [n for n, _ in refused],
               "untrusted": untrusted}  # fmt: skip
    return Held(github, listed, through), summary | ({"errors": errors} if errors else {})


def _hold_all(
    github: GitHub, sidecar: Sidecar, started: dict[int, str], holds: dict[int, str]
) -> list[dict[str, Any]]:
    """Finish every hold; the errors. The journal names a hold before its first label edit and
    drops it after its comment, so a stop anywhere leaves it to the next pass."""
    if holds != started:
        sidecar.set_holds(holds)  # OSError: nothing is held, and take does not run this pass
    errors, left = [], dict(holds)
    for number, text in sorted(holds.items()):
        if error := _hold(github, number, text):
            errors.append({"issue": number, "error": error})
        else:
            del left[number]
    if left != holds:
        try:
            sidecar.set_holds(left)
        except OSError as exc:  # the held Issues stay held; the next pass comments once more
            errors.append({"error": f"HOLD_JOURNAL: {type(exc).__name__}: {exc}"})
    return errors


def _hold(github: GitHub, number: int, text: str) -> str:
    """Out of the queue, held, told; "" on success. Each step is safe to repeat (removing an
    absent label and adding a present one change nothing), and once the first one is done no
    ``take`` lists the Issue. A stop after the comment repeats it on the next pass."""
    try:
        _edit(github, number, "--remove-label", QUEUE)
        _edit(github, number, "--add-label", HOLD)
        github.comment(number, text)
    except (RuntimeError, OSError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def _edit(github: GitHub, number: int, flag: str, label: str) -> None:
    # One label per call through the runner's gh wrapper: its relabel adds before it removes,
    # which here would leave a held Issue in the queue (#646).
    github._run("issue", "edit", str(number), "-R", github.repo, flag, label)


def _row(repo, number, block_sha, annotation, verdict: Verdict, worker_model) -> dict[str, Any]:
    return {
        "at": datetime.now(UTC).isoformat(),
        "repo": repo,
        "issue": number,
        "block_sha256": block_sha,
        "annotation_sha256": annotation.sha256 if annotation else "",
        "issue_author": _side(annotation, verdict, "issue_author"),
        "test_author": _side(annotation, verdict, "test_author"),
        "worker": {"model": worker_model, "family": verdict.families["worker"]},
        "verdict": verdict.verdict,
        "reason": verdict.reason,
    }


def _side(annotation, verdict: Verdict, key: str) -> dict[str, Any] | None:
    declared = annotation.declared.get(key) if annotation else None
    if declared is None:
        return None
    return {"declared": declared, "family": verdict.families[key]}
