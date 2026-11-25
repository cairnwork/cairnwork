"""Verify a candidate commit made elsewhere (another agent, CI) with the checks Cairnwork
applies to its own worker's output: one commit on the base that touches only the allowed
files (no deletion, symlink or submodule), the deferred tests laid over it, the verifiers
in the sandbox, and a Core verdict bound to the exact commit.

No worker runs and nothing is bound in the ownership ledger, so the deployer's in-flight count never
sees it. The run holds the shared run lock, as any run does, so ``recover`` cannot mistake its
verifier processes for a dead run's. Like ``submit`` it is refused while the state is drained
(checked again once the run lock is held). Every check runs in a scratch clone of the state's
``mirror`` (or of --repo-path when there is none) that this run alone owns; the external candidate
is fetched into the scratch clone only, never into the shared mirror or the caller's repository. The
Core record goes to the state's notary under a one-off ``verify-`` task id.

A run marks itself with a locked file in ``<state>/verify-runs/``; the kernel drops the lock when
the run dies, so ``cleanup`` (also at the start of every run) tells a dead run's leftovers
(clones, baseline dirs, orphan processes) from a live run's.
"""

from __future__ import annotations

import fcntl
import os
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from cairnwork import candidate_env, protection, replay_set, runlock, verify_scope
from cairnwork.account_check import base_stage, precheck
from cairnwork.blackbox import Declaration
from cairnwork.confined import ATTEMPT_ENV, confirm_gone, sweep_marked
from cairnwork.deferred_store import USED_DIR, consume
from cairnwork.deferred_tests import (
    BaseUncomparable,
    TestCommitRejected,
    author_reason,
    lay_tests_over,
    never_seen,
)
from cairnwork.gitcmd import CLONE_ISOLATED, OID, GitCommandError, run_git
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.notary_verify import binding_hash
from cairnwork.ownership import request_digest
from cairnwork.p2p import base_env_check, p2p_for
from cairnwork.pipeline import TaskRejected, TaskResult, TaskSpec, tests_with_teeth
from cairnwork.pipeline_judge import judge_candidate
from cairnwork.recovery import _remove_leftovers
from cairnwork.textclean import shown
from cairnwork.verify_candidate_checks import (
    check_before_clone,
    check_task_for_verify,
    require_core_ids,
    validate_import_paths,
)
from cairnwork.verify_cleanup import (
    clear_account,
    leftovers,
    path_absent,
    read_start_mark,
    write_start_mark,
)
from cairnwork.verify_context import VerifyContext, verifier_interpreter
from cairnwork.verify_parts import binding_of, remove_clone
from cairnwork.workspace import (
    ATTEMPT_ID,
    Attempt,
    CandidateRejected,
    WorkspaceError,
    check_candidate_commit,
    create_attempt_clone,
    remove_attempt_clone,
    unless_interrupted,
)

_judge_for_verify = partial(judge_candidate, control=True, env_skip_downgrade=True)


def verify_candidate(
    intake: Intake,
    *,
    repo: Path, base: str, candidate: str,
    allowed_files: tuple[str, ...],
    verifiers: tuple[tuple[str, tuple[str, ...]], ...],
    test_commit: str = "", import_paths: tuple[str, ...] = (),
    blackbox: Declaration | None = None, require_protection: str = "",  # #303 (protection.py)
) -> dict[str, Any]:  # fmt: skip
    """``verified`` is true only for a Core verdict bound to what was judged: the candidate, or
    with deferred tests the tree that ran and its ``binding`` (#229). ``status`` is VERIFIED,
    FAILED, REFUSED or UNCOMPARABLE (#234: the base could not be compared; ``uncomparable`` says
    why, Core is never asked). The account precheck precedes burn; ``tests_consumed``: used up.
    Raises TaskRejected for malformed input and IntakeRefused when drained or mirror unusable.
    ``import_paths`` (#283) are directories prepended to the verifiers' import path for a
    src-layout repo; BAD_IMPORT_PATH when malformed or inside a test root. Config's are not read.
    Environment failures before the test commit is burned are explicit refusals. The shadow rules
    see only the listed files the candidate changed (#866)."""
    if not isinstance(base, str) or not OID.fullmatch(base):
        raise TaskRejected("BAD_BASE", "base_commit must be a 40-hex commit id")
    test_roots = tuple(intake.config()["test_roots"])
    validate_import_paths(repo, base, import_paths, test_roots)
    require_core_ids(verifiers)
    task = TaskSpec(
        f"verify-{secrets.token_hex(8)}", "verify", "verify", "", repo, base, allowed_files,
        verifiers, test_commit=test_commit, p2p_budget=intake.config()["p2p_time_budget_seconds"],
        blackbox=blackbox, require_protection=require_protection, test_store=intake.test_store(),
        p2p_mode=intake.config()["p2p_selection"], test_roots=test_roots, import_paths=import_paths,
        origin_url=intake.config().get("origin_url") or None,
    )  # fmt: skip
    account, python = intake.verifier_account(), intake.config()["verifier_python"]
    specs = check_task_for_verify(
        verify_scope.narrowed(task, candidate), python or getattr(account, "python", None)
    )
    if not OID.fullmatch(candidate):
        raise TaskRejected("BAD_CANDIDATE", "candidate must be a 40-hex commit id")
    _require_accepting(intake)
    source = _source(intake, repo)
    intake.prepare()
    cleanup_kept = cleanup(intake).get("kept", [])
    result = TaskResult(task.idempotency_key, f"a{secrets.token_hex(8)}", "FAILED")
    notary = intake.notary
    ctx = VerifyContext.of(task, intake.run_root, "", account, python).started(result.attempt_id)
    status, binding, uncomparable = "FAILED", None, None
    with runlock.held(intake.state / "ownership.sqlite", exclusive=False):
        _require_accepting(intake)  # a drain that came in while we waited for the lock
        with _marked(intake, result.attempt_id):
            try:
                precheck(account, python)  # before _burn: an unusable account consumes no tests
                binding = _judge(intake, task, source, specs, candidate, notary, result, ctx)
            except (CandidateRejected, TestCommitRejected, WorkspaceError) as exc:
                result.reason, status = author_reason(exc, bool(task.test_commit)), "REFUSED"
            except BaseUncomparable as exc:  # before Core: nothing notarised, nothing to sign
                result.reason, status = f"UNCOMPARABLE: {exc}", "UNCOMPARABLE"
                found, rule = exc.comparison, candidate_env.uncomparable_rule(exc.comparison.code)
                uncomparable = {"code": found.code, "detail": found.detail, "rule": rule}
            except OSError as exc:
                result.reason, status = f"INFRA_ERROR: {type(exc).__name__}: {exc}", "FAILED"
    judged = result.candidate_commit or candidate
    subject = binding["head"] if binding else judged  # a bound row names the head (#229)
    verdict = notary.verdict(result.task_id, result.attempt_id, expected_candidate=subject)
    return {
        **_status_fields(result.verified, status, result.protection),
        "verified": result.verified,
        "reason": result.reason,
        "core_status": verdict.status if verdict else "",
        "candidate_commit": candidate,
        "judged_commit": judged,  # with tests: a commit of the scratch clone only, gone now
        "executed_tree": binding["executed_tree"] if binding else "",
        "binding": binding,
        "task_id": result.task_id,
        "attempt_id": result.attempt_id,
        "logs": result.logs,
        "p2p": result.p2p,
        "uncomparable": uncomparable,  # {code, detail, rule} when status is UNCOMPARABLE (#234)
        "protection": result.protection,  # protection.py; "" when Core was never asked
        "tests_consumed": tests_consumed(intake, test_commit),
        "verifier_mode": "account" if account is not None else "sandbox_only",
        "cleanup_kept": cleanup_kept,  # dead attempts that could not be cleaned (#1802)
    } | replay_set.record_only(result, status, task, candidate)  # never judged (#1105)


def _status_fields(verified: bool, status: str, level: str) -> dict[str, str]:
    """``status``, and ``verdict``: VERIFIED with the protection level beside it, so a same-process
    pass does not read like a blackbox one. Any other status, or no level (Core was never asked),
    gives ``verdict`` equal to ``status``."""
    word = "VERIFIED" if verified else status
    return {"status": word, "verdict": protection.verdict_text(verified, status, level)}


def _require_accepting(intake: Intake) -> None:
    if not intake.accepting():
        raise IntakeRefused("VERIFY_NOT_ACCEPTING", "Cairnwork takes no new work (drained)")


def _source(intake: Intake, repo: Path) -> Path:
    """The repository the worker path would use as task.repo: the state's mirror when it
    names one, else the caller's. It is only read."""
    mirror = intake.config()["mirror"]
    if mirror and not Path(mirror).is_dir():
        raise IntakeRefused("BAD_CONFIG", f"mirror {mirror} does not exist")
    return Path(mirror) if mirror else repo


@contextmanager
def _scratch(intake: Intake, source: Path, repo: Path, task: TaskSpec, candidate: str, name: str):
    """A clone of source for this run alone. The candidate comes from outside, so it is
    fetched into this clone only, never into the shared mirror (objects there would later
    make a test commit look already seen). Its origin is source's origin, so a deferred
    test commit is fetched from the same place as on the worker path."""
    path = intake.state / "verify-scratch" / name
    path.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        run_git(path.parent, *CLONE_ISOLATED, str(source), name)
        try:
            run_git(
                path,
                "remote",
                "set-url",
                "origin",
                _absolute(run_git(source, "remote", "get-url", "origin").strip(), source),
            )
        except GitCommandError:
            run_git(path, "remote", "remove", "origin")  # source has none: neither do we
        run_git(path, "fetch", "--quiet", "--no-tags", str(repo), task.base_commit, candidate)
    except GitCommandError as exc:
        shutil.rmtree(path, ignore_errors=True)
        raise WorkspaceError(f"cannot prepare a scratch clone: {shown(exc)}") from exc
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _check_parent(repo: Path, base: str, candidate: str) -> None:
    """In the caller's repository, read-only and before anything is fetched anywhere: the
    candidate must be one commit directly on the base. A candidate built on a hidden test
    commit is refused here, so it never brings that commit into a store Cairnwork checks."""
    try:
        parents = run_git(repo, "rev-list", "--parents", "-n", "1", candidate).split()[1:]
    except GitCommandError as exc:
        raise WorkspaceError(
            f"cannot read {shown(candidate)} in {shown(repo)}: {shown(exc)}"
        ) from exc
    if parents != [base]:
        found = ", ".join(map(shown, parents))
        raise CandidateRejected(
            "NOT_ONE_COMMIT_ON_BASE", f"parents of {shown(candidate)} are {found}"
        )


def _absolute(url: str, source: Path) -> str:
    """A relative origin (../upstream.git) means relative to the source repository; the
    scratch clone lives elsewhere, so give it the absolute path."""
    if "://" in url or os.path.isabs(url) or ":" in url.split("/")[0]:
        return url  # a URL, an absolute path, or scp-style host:path
    return str((source / url).resolve())


def _burn(intake: Intake, test_commit: str, *, record: bool) -> None:
    """The deferred tests stay hidden from whoever supplies candidates, so a test commit is
    single-use across verify and worker runs (deferred_store.consume); record=False only checks."""
    consume(intake.state / USED_DIR, test_commit, record=record)


def tests_consumed(intake: Intake, test_commit: str) -> bool:
    """Is ``test_commit`` now marked used, so a retry needs a new one (#234)? Unknown: True."""
    if not OID.fullmatch(test_commit):
        return False
    try:  # only a marker known to be missing means not consumed
        _used(intake, test_commit).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _used(intake: Intake, test_commit: str) -> Path:
    return intake.state / USED_DIR / test_commit  # checked to be 40 hex digits


def _judge(intake, task, source, specs, candidate, notary, result, ctx) -> dict | None:
    _check_parent(task.repo, task.base_commit, candidate)
    if task.test_commit:
        never_seen(source, task.test_commit)  # the object store the worker path checks
        _burn(intake, task.test_commit, record=False)
        if task.test_store:  # #332: an unimported commit is refused before it is burned
            task.test_store.require_imported(task.test_commit)
    with _scratch(intake, source, task.repo, task, candidate, result.attempt_id) as scratch:
        task, ctx = replace(task, repo=scratch), replace(ctx, repo=scratch)
        return _judge_in(intake, task, specs, candidate, notary, result, ctx, source=source)


def _base_env(task, result, ctx, source) -> BaseUncomparable | None:
    """Without deferred tests no base run follows, so check the base as a dispatch does (#259),
    before any candidate code runs (an account failure here is no evidence either way). A failure
    only counts when the candidate then fails too: a candidate that passes its verifiers, for
    example one that fixes a base that crashed at import, keeps its own evidence."""
    try:
        with base_stage(ctx.account, ctx.python):
            base_env_check(task, result, ctx, without_tests=True, key_repo=source)
    except BaseUncomparable as exc:
        return exc
    return None


def _judge_in(intake, task, specs, candidate, notary, result, ctx, *, source) -> dict | None:
    run_root = ctx.run_root
    check_before_clone(task, candidate, verifier_interpreter(ctx))  # base roots, then #488
    clone = create_attempt_clone(task.repo, candidate, run_root, result.attempt_id)
    with unless_interrupted(partial(remove_clone, remove_attempt_clone, run_root, result)):
        attempt = Attempt(clone.attempt_id, task.base_commit, clone.path, clone.refs_before)
        check_candidate_commit(
            task.repo, attempt, candidate, task.allowed_files, python=verifier_interpreter(ctx)
        )
        env = None if task.test_commit else _base_env(task, result, ctx, source)
        judged, test_files, binding = candidate, (), None
        if task.test_commit:
            _burn(intake, task.test_commit, record=True)  # before the tests are fetched
            mode = "bundle" if task.test_store and task.test_store.required else "auto"
            # the worker path's journal row; cairnwork-attest reads it by (task_id, attempt_id)
            intake.journal.record(result.task_id, result.attempt_id, "TEST_SOURCE", mode)
            # base runs: an account failure is no evidence (#234)
            with base_stage(ctx.account, ctx.python):
                test_files = tests_with_teeth(task, specs, result, ctx, candidate)
            judged = lay_tests_over(
                task.repo, judged, task.test_commit, test_files, result.attempt_id, run_root
            )
        with base_stage(ctx.account, ctx.python):
            plan = p2p_for(task, specs, test_files, result, ctx)
        binding = (
            binding_of(task, candidate, test_files, judged, plan) if task.test_commit else None
        )
        result.candidate_commit = judged
        digest = request_digest(task.request() | {"candidate": candidate})
        if binding:  # what a receiver can recompute, unlike the scratch paths in the request
            digest = binding_hash(binding)
        _judge_for_verify(task, specs, digest, notary, result, test_files, ctx, lambda _: None,
                          plan, binding, env)  # fmt: skip
        return binding


def _gap() -> None:
    """Between opening a marker and locking it; tests widen it to show the race is closed."""


def _same_file(fd: int, path: Path) -> bool:
    try:
        now, held = os.stat(path, follow_symlinks=False), os.fstat(fd)
    except FileNotFoundError:
        return False
    return (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino)


@contextmanager
def _marked(intake: Intake, attempt_id: str) -> Iterator[None]:
    """Hold a locked marker file for the duration of a run. The lock is taken before the marker
    counts, and only if the path still names the file we locked (a cleaner may have removed it)."""
    path = intake.state / "verify-runs" / attempt_id
    path.parent.mkdir(mode=0o700, exist_ok=True)
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        _gap()
        fcntl.flock(fd, fcntl.LOCK_EX)
        if _same_file(fd, path):
            break
        os.close(fd)
    write_start_mark(fd)
    try:  # an interrupt keeps the marker: the next run's cleanup then sweeps this attempt
        with unless_interrupted(partial(path.unlink, missing_ok=True)):
            yield
    finally:
        os.close(fd)


def cleanup(intake: Intake) -> dict[str, Any]:
    """Remove what dead runs left behind. An attempt is cleaned only once its processes are shown
    gone and its scratch, clones and marker are gone; a step that cannot be done keeps it."""
    markers = intake.state / "verify-runs"
    out: dict[str, Any] = {"cleaned": []}
    try:
        found = sorted(markers.iterdir()) if markers.is_dir() else []
    except OSError:
        return out | {"kept": [markers.name]}  # cannot be read: reported, not trusted as empty
    for marker in found:
        if marker.is_symlink() or not ATTEMPT_ID.fullmatch(marker.name):
            continue
        try:
            fd = os.open(marker, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        except OSError:  # unreadable, or not a file: kept, retried later
            out.setdefault("kept", []).append(marker.name)
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _gap()
            if not _same_file(fd, marker):
                continue  # replaced by a run that started meanwhile
            look = partial(sweep_marked, ATTEMPT_ENV, marker.name, dispatched=read_start_mark(fd))
            swept = confirm_gone(look(), look)
            _remove_leftovers(intake.run_root, marker.name)
            shutil.rmtree(work := intake.state / "verify-scratch" / marker.name, ignore_errors=True)
            left = leftovers(intake.run_root, marker.name) + clear_account(intake, marker.name)
            if left:  # a clone or index that would not go is named, not dropped
                out.setdefault("leftovers", {})[marker.name] = left
            done = swept.gone and path_absent(work) and not left
            if done:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    done = False  # a marker that stays is retried, it must not abort the run
            out.setdefault("cleaned" if done else "kept", []).append(marker.name)
        except BlockingIOError:
            pass  # alive: it holds the lock
        finally:
            os.close(fd)
    return out
