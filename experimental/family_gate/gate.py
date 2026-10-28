"""One gate pass over the queued Issues, and the experiment's sidecar record.

Run under the Issue runner's ``issues.lock`` (``__main__``), before ``issue_runner.take``. For
every trusted queued Issue whose ``eia-task`` block core would take, it decides (``families``)
and appends one sidecar line per (Issue, task block, annotation, worker model). A REFUSED Issue
gets one comment and is then moved from ``cairnwork:queue`` to ``cairnwork:family-hold``
(``HOLD``; create the label on the target repository before enabling); a runner of another
family can put it back in the queue by hand. The gate never adds ``cairnwork:queue`` itself.

``take`` sees only what the gate let through (``Held``): Issues it decided and did not refuse,
and Issues whose task block core refuses (core comments on those, once). Everything else this
pass lists stays queued untaken: untrusted when the gate looked, a GitHub error, a refusal not
yet applied; an Issue queued after the gate's listing waits for the next pass too.

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


def _key(row: dict[str, Any]) -> tuple:
    return (row["repo"], row["issue"], row["block_sha256"], row["annotation_sha256"],
            row["worker"]["model"])  # fmt: skip


def run_gate(
    intake: Intake, github: GitHub, sidecar: Sidecar, worker_model: str
) -> tuple[Held, dict[str, Any]]:
    """(the GitHub ``take`` gets, the pass summary). Only an Issue the gate let through reaches
    ``take``; the rest stay queued untaken and are decided again on the next pass."""
    roots = tuple(intake.config()["test_roots"])
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
    for number, verdict in refused:
        if error := _refuse(github, number, verdict, worker_model):
            errors.append({"issue": number, "error": error})
    summary = {"counts": counts, "recorded": len(new), "refused": [n for n, _ in refused],
               "untrusted": untrusted}  # fmt: skip
    return Held(github, listed, through), summary | ({"errors": errors} if errors else {})


def _refuse(github: GitHub, number: int, verdict: Verdict, worker_model: str) -> str:
    """Comment, then hold; "" on success. Either failure leaves the Issue queued (never taken
    this pass) and the next pass refuses it again, so a failed hold can repeat the comment."""
    try:
        github.comment(number, refusal_comment(verdict, worker_model))
        github.relabel(number, HOLD, QUEUE)
    except (RuntimeError, OSError) as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


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
