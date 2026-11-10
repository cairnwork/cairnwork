"""Publish a signed attestation bundle as a git note."""

from __future__ import annotations

import base64
import binascii
import json
import os
import random
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cairnwork.gitcmd import _SAFE_OPTIONS, GitCommandError, config_hooks_off, ssh_batch_mode
from cairnwork_attest.bundle import BundleError, read_bundle
from cairnwork_attest.record import AttestError, parse_strict_json

_OID = re.compile(r"^[0-9a-f]{40}$")
_REMOTE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_RETRYABLE = (
    "fetch first",
    "non-fast-forward",
    "cannot lock ref",
    "failed to update ref",
    "lock failure",
)
_RETRY = object()
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a publish attempt."""

    ok: bool
    code: str
    detail: str | None
    candidate: str
    notes_ref: str
    remote: str
    attempts: int
    blob: str | None
    notes_tip: str | None


_CONFIG_VAR = re.compile(r"GIT_CONFIG_(?:COUNT|KEY_[0-9]+|VALUE_[0-9]+)")


def _git(repo: Path, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[str]:
    """Run git with prompts disabled and C-locale messages (stderr is matched).

    Network fetch/push runs ssh in BatchMode unless the operator configured core.sshCommand
    (gitcmd.ssh_batch_mode, #759).

    gitcmd's options (no repository or configuration-declared hooks, no fsmonitor, no replace
    refs) and the caller's GIT_* variables dropped, except GIT_CONFIG_COUNT/KEY_n/VALUE_n: the
    post-verified hook's notes push gets its credential helper that way (docs/ATTESTATION.md).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if (not k.startswith("GIT_") or _CONFIG_VAR.fullmatch(k))
        and k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")  # no password prompt program (#759)
    }
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "LANGUAGE": "C"})
    if args and args[0] in ("fetch", "push"):
        ssh_batch_mode(env, repo)
    try:
        hooks_off = config_hooks_off(repo, env)
    except GitCommandError as exc:  # a hook name -c cannot carry: git is not run
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))
    proc = subprocess.run(
        ["git", *_SAFE_OPTIONS, *hooks_off, *args],
        cwd=repo,
        env=env,
        capture_output=True,
        check=False,
        input=input,
    )
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


# userinfo in a URL (https://user:token@host, https://token@host) that git may echo on stderr;
# greedy up to the last @ before the first / so an unencoded @ in a password is hidden too
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s]*@")


def _redact(text: str) -> str:
    """Hide credentials embedded in remote URLs before git output reaches a result."""
    return _URL_USERINFO.sub(r"\1***@", text)


class _GitFailure(Exception):
    def __init__(self, code: str, detail: str) -> None:
        detail = _redact(detail)
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(
    code: str,
    detail: str | None,
    candidate: str,
    notes_ref: str,
    remote: str,
    attempts: int,
    blob: str | None = None,
) -> PublishResult:
    """Build a failure result."""
    return PublishResult(
        ok=False,
        code=code,
        detail=detail,
        candidate=candidate,
        notes_ref=notes_ref,
        remote=remote,
        attempts=attempts,
        blob=blob,
        notes_tip=None,
    )


def _check_args(
    run: Runner, repo: Path, candidate: str, remote: str, notes_ref: str, attempts: int
) -> str | None:
    """Return a USAGE detail for unsafe or malformed arguments, else None."""
    if not _OID.fullmatch(candidate):
        return f"malformed candidate: {candidate!r} (must be 40-hex)"
    if not _REMOTE.fullmatch(remote):
        return f"remote must be a configured remote name: {_redact(remote)!r}"
    if not notes_ref.startswith("refs/notes/") or notes_ref == "refs/notes/":
        return f"notes ref must be under refs/notes/: {notes_ref!r}"
    if run(repo, "check-ref-format", notes_ref).returncode != 0:
        return f"invalid notes ref: {notes_ref!r}"
    if attempts < 1:
        return f"max attempts must be >= 1: {attempts}"
    return None


def _read_and_check_bundle(bundle: Path, candidate: str) -> bytes:
    """Validate the bundle and return the exact bytes that were validated."""
    envelope = read_bundle(bundle)
    try:
        raw = bundle.read_bytes()
        again = parse_strict_json(
            raw.decode("utf-8"),
            error_cls=BundleError,
            malformed_code="BUNDLE_MALFORMED",
            constant_code="BUNDLE_MALFORMED",
        )
        raw_payload = base64.b64decode(envelope["payload"], validate=True)
        statement = json.loads(raw_payload)
        subject = statement["subject"][0]["digest"]["gitCommit"]
    except (
        OSError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        binascii.Error,
        RecursionError,
    ) as exc:
        raise BundleError(f"statement shape: {exc}", code="STATEMENT_SHAPE") from exc
    if again != envelope:
        raise BundleError("bundle changed while being read", code="BUNDLE_CHANGED")
    if subject != candidate:
        raise BundleError(
            f"bundle subject gitCommit does not match candidate {candidate}",
            code="CANDIDATE_MISMATCH",
        )
    return raw


def _store_blob(run: Runner, repo: Path, raw: bytes) -> str:
    """Store the validated bytes as a git blob (no filters) and return the oid."""
    proc = run(repo, "hash-object", "-w", "--no-filters", "--stdin", input=raw)
    if proc.returncode != 0:
        raise _GitFailure("BLOB_STORE_FAILED", proc.stderr.strip())
    return proc.stdout.strip()


def _rev_parse(run: Runner, repo: Path, ref: str) -> str:
    proc = run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if proc.returncode != 0 or not _OID.fullmatch(proc.stdout.strip()):
        raise _GitFailure("REV_PARSE_FAILED", proc.stderr.strip() or f"cannot resolve {ref}")
    return proc.stdout.strip()


def _fetch_notes(
    run: Runner, repo: Path, remote: str, notes_ref: str, private_ref: str
) -> str | None:
    """Fetch remote notes into a private ref and return its validated commit oid."""
    proc = run(repo, "fetch", "--no-tags", "--", remote, f"+{notes_ref}:{private_ref}")
    if proc.returncode == 0:
        return _rev_parse(run, repo, private_ref)
    if "couldn't find remote ref" not in proc.stderr:
        raise _GitFailure("FETCH_FAILED", proc.stderr.strip())
    exists = run(repo, "rev-parse", "--verify", "--quiet", private_ref).returncode == 0
    if exists:
        deleted = run(repo, "update-ref", "-d", "--", private_ref)
        if deleted.returncode != 0:
            raise _GitFailure("LOCAL_REF_RESET_FAILED", deleted.stderr.strip())
    return None


def _existing_note(run: Runner, repo: Path, notes_tip: str | None, candidate: str) -> str | None:
    """Read a candidate note blob from the fetched remote notes commit."""
    if notes_tip is None:
        return None
    paths = (candidate, f"{candidate[:2]}/{candidate[2:]}")
    for path in paths:
        proc = run(repo, "ls-tree", notes_tip, "--", path)
        if proc.returncode != 0:
            raise _GitFailure("NOTES_LIST_FAILED", proc.stderr.strip())
        if not proc.stdout.strip():
            continue
        fields = proc.stdout.split()
        if len(fields) >= 3 and fields[1] == "blob" and _OID.fullmatch(fields[2]):
            return fields[2]
        raise _GitFailure("NOTES_LIST_FAILED", "malformed note tree entry")
    return None


def _try_push(
    run: Runner,
    repo: Path,
    remote: str,
    notes_ref: str,
    working_ref: str,
    candidate: str,
    blob: str,
) -> str | None | object:
    """Add and push one captured note commit, or return _RETRY if another push won."""
    added = run(repo, "notes", f"--ref={working_ref}", "add", "-C", blob, "--", candidate)
    if added.returncode != 0:
        raise _GitFailure("NOTE_ADD_FAILED", added.stderr.strip())
    tip = _rev_parse(run, repo, working_ref)
    proc = run(repo, "push", "--", remote, f"{tip}:{notes_ref}")
    if proc.returncode == 0:
        return tip
    if "rejected" in proc.stderr and any(m in proc.stderr for m in _RETRYABLE):
        return _RETRY
    raise _GitFailure("PUSH_FAILED", proc.stderr.strip())


def _backoff(attempt: int) -> None:
    """Jittered wait so racing publishers do not retry in lockstep."""
    time.sleep(random.uniform(0.05, 0.25) * attempt)  # noqa: S311 - jitter, not crypto


def publish_bundle(
    bundle: Path,
    repo: Path,
    candidate: str,
    *,
    remote: str = "origin",
    notes_ref: str = "refs/notes/cairnwork",
    max_attempts: int = 5,
    run: Runner | None = None,
) -> PublishResult:
    """Publish a signed bundle as a git note on ``notes_ref`` and push it to ``remote``."""
    git = run or _git
    usage = _check_args(git, repo, candidate, remote, notes_ref, max_attempts)
    if usage is not None:
        return _fail("USAGE", usage, candidate, notes_ref, _redact(remote), 0)
    try:
        raw = _read_and_check_bundle(bundle, candidate)
    except AttestError as exc:
        return _fail(exc.code, exc.message, candidate, notes_ref, remote, 0)
    if git(repo, "cat-file", "-e", f"{candidate}^{{commit}}").returncode != 0:
        detail = f"commit {candidate} not in repo"
        return _fail("CANDIDATE_NOT_FOUND", detail, candidate, notes_ref, remote, 0)
    blob: str | None = None
    attempt = 0
    working_ref = f"{notes_ref}-publish-{os.getpid()}-{random.getrandbits(64):016x}"
    result: PublishResult | None = None
    try:
        blob = _store_blob(git, repo, raw)
        for attempt in range(1, max_attempts + 1):
            notes_tip = _fetch_notes(git, repo, remote, notes_ref, working_ref)
            existing = _existing_note(git, repo, notes_tip, candidate)
            if existing is not None and existing != blob:
                detail = f"existing note {existing} differs from {blob}"
                result = _fail("NOTE_CONFLICT", detail, candidate, notes_ref, remote, attempt, blob)
                break
            if existing == blob:
                result = _ok("NOOP", candidate, notes_ref, remote, attempt, blob, notes_tip)
                break
            tip = _try_push(git, repo, remote, notes_ref, working_ref, candidate, blob)
            if tip is None or isinstance(tip, str):
                result = _ok("PUBLISHED", candidate, notes_ref, remote, attempt, blob, tip)
                break
            if attempt < max_attempts:
                _backoff(attempt)
    except _GitFailure as exc:
        result = _fail(exc.code, exc.detail, candidate, notes_ref, remote, attempt, blob)
    finally:
        deleted = git(repo, "update-ref", "-d", "--", working_ref)
        if deleted.returncode != 0:
            result = _fail(
                "LOCAL_REF_RESET_FAILED",
                _redact(deleted.stderr.strip()),
                candidate,
                notes_ref,
                remote,
                attempt,
                blob,
            )
    if result is not None:
        return result
    detail = f"failed after {max_attempts} attempts"
    return _fail("PUSH_RETRIES_EXHAUSTED", detail, candidate, notes_ref, remote, max_attempts, blob)


def _ok(
    code: str,
    candidate: str,
    notes_ref: str,
    remote: str,
    attempts: int,
    blob: str,
    tip: str | None,
) -> PublishResult:
    return PublishResult(
        ok=True,
        code=code,
        detail=None,
        candidate=candidate,
        notes_ref=notes_ref,
        remote=remote,
        attempts=attempts,
        blob=blob,
        notes_tip=tip,
    )
