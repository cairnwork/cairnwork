"""#1768: the file set pytest collects against the file set Cairnwork counts, on the base.

Step 1: a pytest config below the root that sets a test-naming key. The verifier's pinned ini
never reads it, so a run from the root cannot see it; it is refused on its own narrow rule.
Anything that cannot be read as a config is refused too, never passed.

Step 2: ``file_parity`` collects the base's test roots (collect-only, the verifier's own sandbox and
ini) and compares the files pytest collected with the files Cairnwork counts (tracked, under a
root, matching ``python_files``). A counted file pytest did not collect (``collect_ignore``,
``collect_ignore_glob``, ``pytest_ignore_collect``) is UNCOMPARABLE ``P2P_BASE_FILES_UNCOLLECTED``;
a run that cannot be read is ``P2P_BASE_COLLECT_UNUSABLE``, never a pass. Items inside a collected
file are not compared: the verifier needs the file set only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cairnwork.collect_reader import _unclean
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.node_file import file_part
from cairnwork.pytest_config import test_file_rule
from cairnwork.pytest_config_files import _CANDIDATES, PytestConfigError, _section
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

NAMING_KEYS = ("python_files", "python_classes", "python_functions")
NESTED = "P2P_BASE_NESTED_NAMING"
UNCOLLECTED = "P2P_BASE_FILES_UNCOLLECTED"
UNUSABLE = "P2P_BASE_COLLECT_UNUSABLE"
_FORMS = {(name, section): form for name, section, form in _CANDIDATES}
_NAMES = frozenset(name for name, _ in _FORMS)


def nested_naming(task) -> None:
    """Raise UNCOMPARABLE ``P2P_BASE_NESTED_NAMING`` when a config file below the base's root sets
    ``python_files``, ``python_classes`` or ``python_functions`` (or cannot be read)."""
    try:
        listing = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    except GitCommandError as exc:
        raise _refuse("the base tree cannot be listed") from exc
    for entry in filter(None, listing.decode("utf-8", "surrogateescape").split("\0")):
        meta, _, path = entry.partition("\t")
        if "/" not in path or path.rsplit("/", 1)[1] not in _NAMES:
            continue
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise _refuse(f"{_shown(path)} is not a regular file")
        try:
            text = run_git(task.repo, "cat-file", "blob", oid)
            keys = _keys(path.rsplit("/", 1)[1], text)
        except (GitCommandError, UnicodeDecodeError, PytestConfigError) as exc:
            raise _refuse(f"{_shown(path)} cannot be read") from exc
        if keys:
            raise _refuse(f"{_shown(path)} sets {', '.join(keys)}")


def _keys(name: str, text: str) -> list[str]:
    found: set[str] = set()
    for (file, section), form in _FORMS.items():
        if file == name:
            table = _section(text, section, form, name)
            found |= {k for k in NAMING_KEYS if table and k in table}
    return sorted(found)


def _shown(path: str) -> str:
    return path.encode("ascii", "backslashreplace").decode()[:80]  # a path is candidate text


def _refuse(why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", NESTED, f"{why} below the root"))


def file_mismatch(counted, collected) -> list[str]:
    """The counted files pytest did not collect, sorted (an extra collected file is no mismatch)."""
    return sorted(set(counted) - set(collected))


def file_parity(task, ctx, budget, attempt_id) -> None:
    """Raise UNCOMPARABLE when the base's collection and its counted test files differ in files."""
    counted = _counted(task)
    if not counted:
        return
    spec = VerifierSpec("p2p-base-files", tuple(task.test_roots), collect_only=True)
    runs = run_verifiers(ctx, task.base_commit, (spec,), f"{attempt_id}-f", timeout=budget)
    try:
        collected, why = _collected_files(runs[0])
    finally:
        for run in runs:  # evidence for this plan only, not for Core
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if why:
        raise _refuse_with(UNUSABLE, f"{why} on the base")
    missing = file_mismatch(counted, collected)
    if missing:
        shown = ", ".join(_shown(p) for p in missing[:3])
        raise _refuse_with(
            UNCOLLECTED, f"{len(missing)} counted test file(s) not collected: {shown}"
        )


def _counted(task) -> list[str]:
    """Tracked files under a test root that match the base's ``python_files``."""
    try:
        raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", "--name-only", task.base_commit, "--")
        is_test = test_file_rule(task.repo, task.base_commit)
    except (GitCommandError, PytestConfigError) as exc:
        raise _refuse_with(UNUSABLE, "the base test files cannot be listed") from exc
    folded = tuple(f"{root.casefold()}/" for root in task.test_roots)
    names = raw.decode("utf-8", "surrogateescape").split("\0")
    return sorted(p for p in names if p.casefold().startswith(folded) and is_test(p))


def _collected_files(run) -> tuple[set[str], str]:
    """(files with a collected id, "") or (set(), why the run says nothing about files)."""
    why = _unclean(run)
    if why:
        return set(), why
    if run.collect_errors or run.collect_skipped or run.returncode not in (0, 5):
        return set(), f"collect-rc-{run.returncode}"  # a failed collector: its files are unknown
    return {file_part(node) for node in run.collected}, ""


def _refuse_with(code: str, why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", code, why))
