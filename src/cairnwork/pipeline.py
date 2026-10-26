"""One governed task, end to end: bind → isolated attempt → candidate → verify → Core.

``run_task`` is the bare core's main path. It owns no policy beyond the preserved behaviors: every
step that fails leaves the task FAILED (or not COMPLETED) with a reason, and only a Core verdict
that is verified for the exact candidate commit makes it COMPLETED.

Any exception after binding is recorded as FAILED with an ``INFRA_ERROR`` reason; a crash of this
process leaves the task DISPATCHED until restart recovery. A FAILED task keeps its card: to retry,
SUPERSEDE it (``takeover.supersede``), then submit anew; the takeover is refused while the old
attempt's processes are not shown gone (#244): a worker that raises ``unswept.WorkerMayLive``
gets its attempt marked in the FAILED move. A ``Worker`` edits its clone and must have exited
when ``run`` returns.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cairnwork import runlock, unswept
from cairnwork.account_check import base_stage, precheck
from cairnwork.blackbox import Declaration
from cairnwork.deferred_store import DeferredStore
from cairnwork.deferred_tests import (
    BaseUncomparable,
    TestCommitRejected,
    check_roots_on_base,
    lay_tests_over,
    never_seen,
)
from cairnwork.gitcmd import OID
from cairnwork.journal import AlreadyDispatched, Journal
from cairnwork.notary import Notary
from cairnwork.notary_verify import nonempty_fields
from cairnwork.ownership import OWNER, InvalidStatusTransition, OwnershipLedger, request_digest
from cairnwork.p2p import base_env_check, p2p_for
from cairnwork.p2p_selection import DEFAULT_BUDGET_SECONDS, DIRECT_MODE
from cairnwork.pipeline_judge import judge_candidate, tests_with_teeth
from cairnwork.protected_paths import path_refusal
from cairnwork.snapshot import snapshot_candidate
from cairnwork.unswept import WorkerMayLive
from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import (
    VerifierRejected,
    VerifierSpec,
    parse_verifier,
    protected_verifier_inputs,
)
from cairnwork.workspace import (
    CandidateRejected,
    WorkspaceError,
    create_attempt_clone,
    remove_attempt_clone,
)

FAULT_ENV = "CAIRNWORK_FAULT_AFTER"


class TaskRejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class TaskSpec:
    """One task. P2P is off by default here (a library/drill path, #174); Intake sets it."""

    idempotency_key: str
    card_hash: str
    title: str
    instructions: str
    repo: Path
    base_commit: str
    allowed_files: tuple[str, ...]
    verifiers: tuple[tuple[str, tuple[str, ...]], ...]
    allow_deletions: bool = False
    test_commit: str = ""  # deferred tests (deferred_tests.py); "" when none
    test_roots: tuple[str, ...] = ("tests",)  # state config: where verifiers and tests live
    import_paths: tuple[str, ...] = ()  # state config: dirs prepended to the import path (#82)
    test_store: DeferredStore | None = None  # private bundle store for test_commit (#119)
    p2p_mode: str = DIRECT_MODE  # state config p2p_selection (p2p.py); only with a test_commit
    p2p_budget: int = DEFAULT_BUDGET_SECONDS  # state config p2p_time_budget_seconds
    before_attempt: Callable[[], str] | None = None  # refusal code or ""; asked per attempt
    blackbox: Declaration | None = None  # function-level checks (#303, blackbox.py); verify only

    def request(self) -> dict:
        # #292: import_paths change what the verifiers import; #303: the declaration's commitment
        extra = nonempty_fields(
            test_commit=self.test_commit, import_paths=list(self.import_paths),
            blackbox_commitment=self.blackbox.commitment() if self.blackbox else "",
        )  # fmt: skip
        return extra | {
            "repo": str(self.repo),
            "title": self.title,
            "instructions": self.instructions,
            "base_commit": self.base_commit,
            "allowed_files": sorted(self.allowed_files),
            "verifiers": [[vid, list(argv)] for vid, argv in self.verifiers],
            "allow_deletions": self.allow_deletions,
        }


class Worker(Protocol):
    """Edits the clone at workdir and returns when done.

    A worker that starts processes must give each one the environment variable
    CAIRNWORK_ATTEMPT=<workdir.name> (confined.ATTEMPT_ENV), so restart recovery can find and
    stop them if Cairnwork dies mid-run (AgyWorker does). Optional ``run_as``: the worker account
    its processes run as, journaled at WORKER_STARTED for recovery and the takeover (#244).
    Optional ``isolated`` (AgyWorker). ``run`` may return the provenance fields it can vouch for
    (provenance.make); None means unknown."""

    def run(self, workdir: Path, task: TaskSpec, /) -> dict[str, str] | None: ...


@dataclass
class TaskResult:
    task_id: str
    attempt_id: str
    status: str
    reason: str = ""
    candidate_commit: str | None = None
    verified: bool = False
    logs: list[str] = field(default_factory=list)
    p2p: dict = field(default_factory=dict)  # the P2P selection rule used (p2p.py)
    fence: str | None = None  # WorkerMayLive's account ("" the owner's): FAILED marks it (#244)
    uncomparable: str = ""  # no evidence (#234): the code, journaled at FINISHED for the Issue


def check_task(task: TaskSpec, *, verifier_python: str | None = None) -> tuple[VerifierSpec, ...]:
    """Reject a task before anything is bound or run."""
    if not isinstance(task.base_commit, str) or not OID.fullmatch(task.base_commit):
        raise TaskRejected("BAD_BASE", "base_commit must be a 40-hex commit id")
    if task.test_commit and not OID.fullmatch(str(task.test_commit)):
        raise TaskRejected("BAD_TEST_COMMIT", "test_commit must be a 40-hex commit id")
    files = task.allowed_files
    if not isinstance(files, (tuple, list)) or not files or len(files) > 10:
        raise TaskRejected("BAD_SCOPE", "allowed_files must list 1 to 10 paths")
    blocked: list[str] = []
    for path in files:  # entries are checked before any set() so a malformed one cannot crash
        refusal = path_refusal(path)
        if refusal:
            if refusal[0] == "TASK_ALLOWED_FILES_INVALID":
                raise TaskRejected("BAD_SCOPE", f"{path!r} is not a clean repository path")
            blocked.append(path)
    if len(set(files)) != len(files):
        raise TaskRejected("BAD_SCOPE", "allowed_files has duplicates")
    protected = protected_verifier_inputs(
        task.allowed_files,
        task.test_roots,
        repo=task.repo,
        base_commit=task.base_commit,
        python=verifier_python,
        import_paths=task.import_paths,
    )
    if protected:
        raise TaskRejected("PROTECTED_VERIFIER_INPUT", ", ".join(protected))
    if blocked:
        raise TaskRejected("TASK_ALLOWED_FILES_PROTECTED", ", ".join(blocked))
    if task.blackbox and task.test_commit:  # the receipt binding cannot carry it yet (#292)
        raise TaskRejected("BAD_BLACKBOX", "blackbox checks with deferred tests: not yet")
    clash = {c.verifier_id for c in task.blackbox.checks} if task.blackbox else set()
    if clash & {verifier_id for verifier_id, _ in task.verifiers}:
        raise TaskRejected("BAD_BLACKBOX", f"verifier ids clash: {sorted(clash)}")
    try:
        return tuple(
            parse_verifier(vid, list(argv), task.test_roots) for vid, argv in task.verifiers
        )
    except VerifierRejected as exc:
        raise TaskRejected(exc.code, str(exc)) from exc


def run_task(
    task: TaskSpec,
    *,
    ledger: OwnershipLedger,
    notary: Notary,
    journal: Journal,
    worker: Worker,
    run_root: Path,
    verifier_account: VerifierAccount | None = None,
    verifier_python: str | None = None,
) -> TaskResult:
    """Run one task. A TaskSpec built directly has no P2P unless the caller sets p2p_mode (#174)."""
    specs = check_task(
        task, verifier_python=verifier_python or getattr(verifier_account, "python", None)
    )
    verifying = (verifier_account, verifier_python)
    with runlock.held(ledger.path, exclusive=False):
        bound = ledger.bind(task.idempotency_key, task.request(), OWNER, task.card_hash)
        record = bound.record
        if bound.duplicate:
            return TaskResult(record.task_id, "", record.status, reason="DUPLICATE_REQUEST")
        return _execute(task, specs, record, ledger, notary, journal, worker, run_root, verifying)


def run_bound_task(
    task: TaskSpec,
    task_id: str,
    *,
    ledger: OwnershipLedger,
    notary: Notary,
    journal: Journal,
    worker: Worker,
    run_root: Path,
    verifier_account: VerifierAccount | None = None,
    verifier_python: str | None = None,
) -> TaskResult:
    """Run a task bound earlier (intake.submit). Its request must be the one bound;
    a task that is no longer BOUND (cancelled, or already run) is not run again."""
    specs = check_task(
        task, verifier_python=verifier_python or getattr(verifier_account, "python", None)
    )
    verifying = (verifier_account, verifier_python)
    with runlock.held(ledger.path, exclusive=False):
        record = ledger.get(task_id=task_id)
        same = record.idempotency_key == task.idempotency_key and (
            record.request_digest == request_digest(task.request())
        )
        if not same or record.owner != OWNER or record.card_hash != task.card_hash:
            raise TaskRejected("REQUEST_MISMATCH", f"{task_id} was bound to another request")
        if record.status != "BOUND":
            return TaskResult(task_id, "", record.status, reason="NOT_BOUND")
        return _execute(task, specs, record, ledger, notary, journal, worker, run_root, verifying)


def _execute(task, specs, record, ledger, notary, journal, worker, run_root, verifying):
    """Dispatch a BOUND task: the journal entry comes first, so a crash is recoverable.

    The task was read BOUND before the journal write, so a cancel (or supersede) can land in
    between (#243). The ledger move then refuses (TRANSITIONS); the run returns NOT_BOUND with
    the ledger's status and closes its append-only DISPATCHED row with a FINISHED row whose
    detail is that status. Recovery never reads it (the task is not in flight, and the detail
    is not COMPLETED/FAILED); ``status`` shows phase FINISHED instead of a dangling DISPATCHED."""
    attempt_id = f"a{secrets.token_hex(8)}"
    steps = _Steps(journal, record.task_id, attempt_id)
    try:
        steps.reach("DISPATCHED", str(Path(run_root).resolve()))
    except AlreadyDispatched:  # another runner took this task first
        status = ledger.get(task_id=record.task_id).status
        return TaskResult(record.task_id, "", status, reason="NOT_BOUND")
    try:
        ledger.set_status(record.task_id, "DISPATCHED")
    except InvalidStatusTransition:  # lost the race after the read: the worker never ran
        status = ledger.get(task_id=record.task_id).status
        steps.reach("FINISHED", status)
        return TaskResult(record.task_id, attempt_id, status, reason="NOT_BOUND")
    result = TaskResult(record.task_id, attempt_id, "FAILED")
    try:
        _attempt(
            task,
            specs,
            record.request_digest,
            notary,
            worker,
            run_root,
            result,
            steps,
            verifying,
        )
    except Exception as exc:  # noqa: BLE001 - an infrastructure failure, never success
        result.verified, result.uncomparable = False, ""
        result.reason = f"INFRA_ERROR: {type(exc).__name__}: {exc}"
    finally:
        result.status = "COMPLETED" if result.verified else "FAILED"
        steps.reach("FINISHED", result.status, "" if result.verified else result.uncomparable)
        fence = None if result.verified or result.fence is None else (attempt_id, result.fence)
        ledger.set_status(record.task_id, result.status, fence=fence)
    return result


@dataclass(frozen=True)
class _Steps:
    """Journal each phase before doing it; CAIRNWORK_FAULT_AFTER=<phase> kills the process
    right after that journal write (crash drills only; a crash never yields success)."""

    journal: Journal
    task_id: str
    attempt_id: str

    def reach(self, phase: str, detail: str = "", uncomparable: str = "") -> None:
        self.journal.record(self.task_id, self.attempt_id, phase, detail, uncomparable)
        if os.environ.get(FAULT_ENV) == phase:
            os._exit(137)


def _attempt(task, specs, digest, notary, worker, run_root, result, steps, verifying) -> None:
    """verifying = (verifier account or None, verifier Python or None)."""
    gate = task.before_attempt or (  # trust re-check per attempt; a gh: card with no gate refuses
        lambda: "TRUST_CHANGED_BEFORE_DISPATCH" * task.card_hash.startswith("gh:")
    )
    if refusal := gate():
        result.reason = refusal
        return
    try:
        if task.test_commit:
            never_seen(task.repo, task.test_commit)
        check_roots_on_base(task.repo, task.base_commit, task.test_roots)
    except TestCommitRejected as exc:
        result.reason = str(exc)
        return
    try:  # before the worker: no worker quota spent, no test exposed (#234)
        precheck(*verifying)
        with base_stage(*verifying):  # and the unchanged base can run its tests at all
            base_env_check(task, run_root, result, verifying)
    except BaseUncomparable as exc:  # nothing has run, so nothing a worker could have broken
        result.reason = exc.comparison.worker_reason(isolated=True)
        result.uncomparable = exc.comparison.code
        return
    attempt = create_attempt_clone(task.repo, task.base_commit, run_root, result.attempt_id)
    steps.reach("WORKER_STARTED", unswept.RUN_AS + (getattr(worker, "run_as", "") or ""))
    try:
        try:
            provenance = worker.run(attempt.path, task)  # None from a worker with no record
        except Exception as exc:  # noqa: BLE001 - any worker failure is a failed attempt
            result.reason = f"WORKER_FAILED: {type(exc).__name__}: {exc}"
            if isinstance(exc, WorkerMayLive):  # its processes may outlive the run (#244)
                result.fence = exc.account
            return
        try:
            candidate = snapshot_candidate(
                task.repo,
                attempt,
                task.allowed_files,
                allow_deletions=task.allow_deletions,
                provenance=provenance,
            )
        except CandidateRejected as exc:
            result.reason = str(exc)
            return
        judged, test_files, plan = candidate.candidate_commit, (), None
        if task.test_commit:  # only now: the worker is done (deferred_tests.py, p2p.py)
            mode = "bundle" if task.test_store and task.test_store.required else "auto"
            steps.reach("TEST_SOURCE", mode)  # the mode of this run, read by cairnwork_attest
            try:
                with base_stage(*verifying):  # an account failure here: no evidence (#234)
                    test_files = tests_with_teeth(task, specs, run_root, result, verifying)
                    judged = lay_tests_over(
                        task.repo,
                        judged,
                        task.test_commit,
                        test_files,
                        result.attempt_id,
                        run_root,
                        provenance,
                    )
                    plan = p2p_for(task, specs, test_files, run_root, result, verifying)
            except TestCommitRejected as exc:
                result.reason = str(exc)
                return
            except BaseUncomparable as exc:  # FAILED either way; only the reason says which
                isolated = getattr(worker, "isolated", False)  # another uid (agy_run_as_user)
                why = getattr(worker, "not_isolated_because", "")
                result.reason = exc.comparison.worker_reason(isolated=isolated, why=why)
                result.uncomparable = exc.comparison.code if isolated is True else ""
                return
        result.candidate_commit = judged
        steps.reach("CANDIDATE", judged)
        judge_candidate(
            task, specs, digest, notary, run_root, result, test_files, verifying, steps.reach, plan
        )
    finally:
        try:
            remove_attempt_clone(run_root, result.attempt_id)
        except WorkspaceError as exc:  # keep the attempt's own outcome; note the leftover
            result.logs.append(f"cleanup failed: {exc}")
