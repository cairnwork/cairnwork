"""The VerifierRun record (moved out of verifiers.py for the 400-line cap, #851)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cairnwork.core_request import VerifierObservation
from cairnwork.verifier_spec import VerifierSpec


@dataclass(frozen=True)
class VerifierRun:
    spec: VerifierSpec
    returncode: int | None
    timed_out: bool
    mutated: tuple[str, ...]
    log_path: Path
    observation: VerifierObservation
    outcomes: dict[str, str]
    skips: dict[str, tuple[str, str]] = field(default_factory=dict)
    survivors: int = 0
    ran: bool = False
    # a plain test failure: the guard reported a failed test and nothing about the run itself
    # is suspect (no timeout, mutation, survivor or oversized log); p2p.py may re-run only this
    plain_failure: bool = False
    report_too_big: bool = False  # the guard's report exceeded MAX_REPORT_BYTES: no verdict read
    recorded: bool = False  # the guard's record was there and well-formed (base_comparison, #234)
    collect_errors: tuple[str, ...] = ()  # collectors the guard saw fail (B2, #234)
    skipped_deps: tuple[str, ...] = ()  # modules skipped collectors could not import (#813)
    import_errors: tuple[tuple[str, str, str], ...] = ()  # #796: guard's import record, unread
    submodule_errors: tuple[tuple[str, str, str], ...] = ()  # #796 S3: the same, unread
    teardown_errors: tuple[str, ...] = ()  # tests whose teardown failed (#478, report_guard)
    fail_kind: dict[str, str] = field(default_factory=dict)  # #775: node -> setup/env/plugin
    forged: tuple[str, ...] = ()  # FORGED_BYTECODE findings (#289): a base run proves nothing
    log_too_big: bool = False  # the run's log went over MAX_LOG_BYTES (#481, base_comparison)
    why: str = ""  # in words, why the run did not pass ("" when it passed): reasons.py
    hidden_tests: bool = False  # #1186: reason rendering must not expose overlay names
    # facts about the sandbox policy used (e.g. "shm withheld: BWRAP_TMPFS_SIZE_UNSUPPORTED"); not
    # in the observation's artifact hash, so recording them changes no bound digest
    notes: tuple[str, ...] = ()
    collected: tuple[str, ...] = ()  # #793: node ids pytest collected (the guard's record)
    collect_skipped: tuple[str, ...] = ()  # #793: collectors skipped while collecting
    collection_finished: bool = False  # #793: collection was not stopped part way
    deny_evidence: str = ""  # #797 step 1: a base run's deny channel (deny_channel.py); not hashed
    deny_denials: frozenset[tuple[str, str]] = frozenset()  # #991: its exact denials; not hashed
    setup_denied: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # #962; unhashed
    data_refused: tuple[str, ...] = ()  # #850: declared data was executed: never a pass
    not_run: tuple[str, ...] = ()  # #962 R0b: named targets or skipped collectors missing
    renamed: tuple[str, ...] = ()  # #962 R0b: items changed after collection
    uncollected_exposed: tuple[str, ...] | None = None
    fixture_names: frozenset[str] | None = None  # #986: the recorder's names; None unless verified
