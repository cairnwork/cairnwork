"""#793 Step 1: read a ``--collect-only`` verifier run as the node ids pytest collected.

A collect-only run (``VerifierSpec.collect_only``) runs in the same sandbox, pinned ini and
report guard as any verifier run; it is evidence about which tests exist, never a pass (no test
ran, so the guard's ``ran`` stays false and ``run_verifiers`` never marks it passed).

pytest's exit code alone does not say which tests are known, so this reader checks it against the
guard's record:

- rc 0: ids collected, no collector failed: every id is accepted.
- rc 5: nothing collected (no tests, or all deselected): the empty set, no collector failed.
- rc 2: some collectors failed. A file whose collector failed is named in ``error_files`` (its ids
  are unknown, never "absent"); the ids of the other files are accepted. A failed collector that
  is not under a target file (a directory, a conftest) leaves nothing readable.

Anything else (a timeout, a mutated checkout, survivors, forged bytecode, an oversized log or
report, no well-formed record, a collection stopped part way, an id outside the targets, a
recorded outcome, a code/record mismatch) gives ``unusable``: the caller must not read any id.
"""

from __future__ import annotations

from dataclasses import dataclass

from cairnwork.node_file import file_part, name_part


@dataclass(frozen=True)
class Collected:
    ids: tuple[str, ...] = ()  # sorted node ids collected, from files that collected cleanly
    error_files: tuple[str, ...] = ()  # target files whose collector failed: ids unknown
    skipped_files: tuple[str, ...] = ()  # target files skipped while collecting: ids unknown
    unusable: str = ""  # non-empty: why nothing can be read from the run
    exposed: tuple[str, ...] | None = None


def read_collected(run) -> Collected:
    """The collected ids of a collect-only ``VerifierRun``; see the module docstring."""
    if not run.spec.collect_only:
        raise ValueError(f"{run.spec.verifier_id}: not a collect-only run")
    why = _unclean(run)
    if why:
        return Collected(unusable=why)
    targets = frozenset(run.spec.targets)
    error_files = _files(run.collect_errors, targets)
    skipped_files = _files(run.collect_skipped, targets)
    if error_files is None or skipped_files is None:
        return Collected(unusable="collect-error-outside-a-target")
    if any(_file(node) not in targets for node in run.collected):
        return Collected(unusable="collected-outside-the-targets")
    clean = not error_files
    expected = {0: clean and bool(run.collected), 5: clean and not run.collected, 2: not clean}
    if not expected.get(run.returncode, False):
        return Collected(unusable=f"collect-rc-{run.returncode}")
    ids = tuple(sorted(n for n in set(run.collected) if _file(n) not in error_files))
    exposed = _exposed(
        getattr(run, "uncollected_exposed", None), targets, error_files | skipped_files
    )
    return Collected(ids, tuple(sorted(error_files)), tuple(sorted(skipped_files)), exposed=exposed)


def _exposed(raw, targets: frozenset[str], unknown: frozenset[str]) -> tuple[str, ...] | None:
    if not isinstance(raw, tuple) or not all(isinstance(item, str) for item in raw):
        return None
    if len(raw) > 5000 or any(len(item) > 1024 for item in raw):
        return None
    if len(set(raw)) != len(raw):
        return None
    if any(not _valid_exposed_id(item) for item in raw):
        return None
    if any(_file(item) not in targets or _file(item) in unknown for item in raw):
        return None
    return tuple(sorted(raw))


def _valid_exposed_id(item: str) -> bool:
    parts = [file_part(item), *name_part(item).split("::")] if "::" in item else [item]
    return (
        len(parts) >= 3
        and all(parts)
        and "[" not in parts[-1]
        and all(32 <= ord(char) <= 126 and not char.isspace() for char in item)
    )


def _unclean(run) -> str:
    if run.returncode is None:
        return "collect-not-run"
    for why, bad in (
        ("collect-forged", run.forged),
        ("collect-timeout", run.timed_out),
        ("collect-survivors", run.survivors),
        ("collect-mutated", run.mutated),
        ("collect-over-cap", run.report_too_big or run.log_too_big),
        ("collect-not-recorded", not run.recorded),
        ("collect-unfinished", not run.collection_finished),  # stopped part way: ids unknown
        ("collect-ran-tests", run.outcomes),
    ):
        if bad:
            return why
    return ""


def _file(nodeid: str) -> str:
    return file_part(nodeid)


def _files(nodes, targets: frozenset[str]) -> frozenset[str] | None:
    """The target files the collector node ids belong to; None when one is outside them."""
    files = frozenset(_file(node) for node in nodes)
    return files if files <= targets else None
