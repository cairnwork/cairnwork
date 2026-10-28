"""``cairnwork-verify``: judge a commit made elsewhere (see verify.py).

    cairnwork-verify --state DIR run --repo-path P --base SHA --candidate SHA \\
        --allowed-file F [...] --verifier CMD [...] [--test-commit SHA] \\
        [--import-path DIR ...]    # src layout: DIR joins the verifiers' import path (#283)
        [--blackbox FILE]          # the owner's function-level checks and answers (#303)
        [--require-protection]     # strict mode: not VERIFIED unless protected (protection.py)
    cairnwork-verify --state DIR blackbox-commitment --blackbox FILE   # for a receiver's policy
    cairnwork-verify --state DIR import-tests --repo-path P --bundle FILE
        # keep a deferred test commit in the private store (deferred_store.py), not on a remote
    cairnwork-verify --state DIR cleanup      # after a crashed run, before ``recover``
    cairnwork-verify --state DIR migrate-notary  # back up, then upgrade an older notary (#343)

Prints one JSON object. Exit codes for ``run``: 0 verified; 2 refused, uncomparable or not
verified (``status`` says which: REFUSED, UNCOMPARABLE or FAILED; #234 added UNCOMPARABLE, so a
caller that switches on REFUSED must handle it); 3 usage error; 1 internal error. ``protection``
says what a verdict rests on (protection.py): ``partial`` when declared blackbox checks are among
the required observations (``partial_macos`` on macOS, #316), ``same_process_only`` when there are
none ("" when Core was never asked). With ``--require-protection`` an unprotected run is not
VERIFIED (either grade passes), and without ``--blackbox`` it is refused before anything runs
(error UNPROTECTED).

Exit codes for ``migrate-notary``: 0 migrated, or already current (``"migrated": false``); 2
refused: NOTARY_SCHEMA_MISMATCH (unknown version), NOTARY_MISSING, BAD_STATE (not an owner-only
directory) or RUN_LOCK_BUSY (a run, recovery or migration holds the run lock); 1 INTERNAL (e.g.
the backup could not be written; the notary is then unchanged).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, NoReturn

import cairnwork_sandbox
from cairnwork import blackbox, protection, runlock
from cairnwork.blackbox_exec import account_can_read
from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.intake import Intake, IntakeRefused
from cairnwork.notary import NotarySchemaMismatch
from cairnwork.notary_migrate import NotaryMissing, migrate_notary
from cairnwork.pipeline import TaskRejected
from cairnwork.runlock import RunLockBusy
from cairnwork.verify_candidate import cleanup, tests_consumed, verify_candidate


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        _emit({"error": "USAGE", "detail": message}, 3)
        sys.exit(3)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    intake = Intake(Path(args.state).resolve())
    try:
        if args.command == "cleanup":
            return _emit(cleanup(intake), 0)
        if args.command == "migrate-notary":  # the only path that upgrades a notary (#343)
            return _emit(_migrate(intake), 0)
        if args.command == "import-tests":
            return _import_tests(intake, args)
        if args.command == "blackbox-commitment":  # what a receiver's policy pins (#303, #312)
            declared = blackbox.load(Path(args.blackbox))
            ids = sorted(check.id for check in declared.checks)  # only the ids: no answers or salt
            return _emit({"blackbox_commitment": declared.commitment(), "blackbox_checks": ids}, 0)
        result = _run(intake, args)
    except IntakeRefused as exc:
        return _emit({"error": exc.code, "detail": exc.detail}, 2)
    except (TaskRejected, blackbox.BlackboxRejected) as exc:
        return _emit({"error": exc.code, "detail": str(exc)}, 2)
    except (NotarySchemaMismatch, NotaryMissing) as exc:  # NOTARY_SCHEMA_OLDER: migrate-notary
        return _emit({"error": exc.code, "detail": str(exc)}, 2)
    except Exception as exc:  # noqa: BLE001 - reported as unknown, never as success
        detail = f"{type(exc).__name__}: {exc}"
        if args.command == "migrate-notary":  # no tests involved
            return _emit({"error": "INTERNAL", "detail": detail}, 1)
        used = tests_consumed(intake, getattr(args, "test_commit", ""))  # a retry needs new tests?
        return _emit({"error": "INTERNAL", "detail": detail, "tests_consumed": used}, 1)
    return _emit(result, 0 if result["verified"] is True else 2)  # UNCOMPARABLE is never 0


def _migrate(intake: Intake) -> dict[str, Any]:
    """Only on a state directory we own (BAD_STATE otherwise) and with no run, recovery or other
    migration under way: the exclusive run lock, never waited for (RUN_LOCK_BUSY)."""
    intake.check_state()
    try:
        with runlock.held(intake.state / "ownership.sqlite", exclusive=True):
            return migrate_notary(intake.state / "notary.sqlite")
    except RunLockBusy as exc:  # only here: a busy run lock elsewhere keeps its meaning
        raise IntakeRefused(exc.code, str(exc)) from exc


def _import_tests(intake: Intake, args) -> int:
    intake.prepare()
    repo = Path(args.repo_path).resolve()
    try:
        sha = intake.test_store().import_bundle(
            repo, Path(args.bundle).resolve(), intake.state / "verify-used-tests"
        )
    except TestCommitRejected as exc:
        return _emit({"error": exc.code, "detail": str(exc)}, 2)
    return _emit({"imported": sha}, 0)


def _require_unmasked_state(intake: Intake) -> None:
    """A state dir or run root under a path the sandbox masks would be empty inside the run,
    so pytest fails at getcwd and the deferred tests are burned for nothing (#324). With a
    verifier account every sandboxed run lives under the account's own run root, never under
    the state, so only the mode without one refuses."""
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or intake.verifier_account() is not None:
        return
    home = Path.home().resolve()
    follow = getattr(backend, "masks_link_targets", False)  # bwrap: a link's target (#339)
    for path in (intake.state.resolve(), intake.run_root.resolve()):
        for masked in (home / rel for rel in backend.masked_home_paths):
            if path.is_relative_to(masked) or _same_dir_above(path, masked, follow):
                raise IntakeRefused(
                    "STATE_UNDER_SANDBOX_MASK",
                    f"{path} is under {masked}, which the verifier sandbox masks;"
                    " put the state outside it (docs/SECURITY.md)",
                )


def _same_dir_above(path: Path, masked: Path, follow: bool = False) -> bool:
    """``path`` or a parent is ``masked`` under another spelling (APFS ignores case), or with
    ``follow`` the directory a symlinked ``masked`` points to."""
    try:
        want = masked.stat() if follow else masked.lstat()
    except OSError:
        return False
    for p in (path, *path.parents):
        try:
            got = p.lstat()
        except OSError:
            continue
        if (got.st_dev, got.st_ino) == (want.st_dev, want.st_ino):
            return True
    return False


def _run(intake: Intake, args) -> dict[str, Any]:
    _require_unmasked_state(intake)
    try:
        verifiers = tuple(
            (f"pytest-{i}", tuple(shlex.split(command))) for i, command in enumerate(args.verifier)
        )
    except ValueError as exc:
        raise TaskRejected("BAD_VERIFIER", str(exc)) from exc
    path = Path(args.blackbox) if args.blackbox is not None else None
    try:  # read by the owner's process only; never passed on to the run
        declared = blackbox.load(path) if path is not None else None
    except blackbox.BlackboxRejected as exc:
        raise TaskRejected(exc.code, str(exc)) from exc
    if args.require_protection and path is None:  # strict mode could never be met: burn nothing
        raise TaskRejected(protection.UNPROTECTED_CODE, "--require-protection needs --blackbox")
    account = intake.verifier_account() if path is not None else None
    if path is not None and account is not None and account_can_read(account, path):
        raise TaskRejected(
            blackbox.BAD, "the verifier account can read the declaration, or could not be asked"
        )
    return verify_candidate(
        intake,
        repo=Path(args.repo_path).resolve(),
        base=args.base,
        candidate=args.candidate,
        allowed_files=tuple(args.allowed_file),
        verifiers=verifiers,
        test_commit=args.test_commit,
        import_paths=tuple(args.import_path),
        blackbox=declared,
        require_protection=protection.WEAKEST if args.require_protection else "",
    )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cairnwork-verify")
    parser.add_argument("--state", required=True)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("cleanup")
    commands.add_parser("migrate-notary")
    commands.add_parser("blackbox-commitment").add_argument("--blackbox", required=True)
    imp = commands.add_parser("import-tests")
    imp.add_argument("--repo-path", required=True)
    imp.add_argument("--bundle", required=True)
    run = commands.add_parser("run")
    run.add_argument("--repo-path", required=True)
    run.add_argument("--base", required=True)
    run.add_argument("--candidate", required=True)
    run.add_argument("--allowed-file", action="append", required=True)
    run.add_argument("--verifier", action="append", required=True)
    run.add_argument("--test-commit", default="")
    run.add_argument("--import-path", action="append", default=[])
    run.add_argument("--blackbox", default=None)  # "" is a path that cannot be opened, not "none"
    run.add_argument("--require-protection", action="store_true")  # strict mode (#303)
    return parser


def _emit(value: dict[str, Any], code: int) -> int:
    print(json.dumps(value, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
