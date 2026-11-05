"""Pick the existing (pass-to-pass) tests to run alongside a task's own verifiers (#117).

A task's author declares the fail-to-pass verifiers that must pass on the candidate. Those
catch "did the change do the job", not "did the change quietly break something that already
worked". Widening runs EXISTING tests (ones that pass on the base) over the candidate too.

This module is pure selection: given the files a candidate may change, the test files that
exist on the base and a mode, it returns the test files to consider. It runs nothing and
knows nothing about pass or fail; p2p.py finds which of them pass on the base.

Modes (state config ``p2p_selection``):

* ``off`` - no widening; only the author's verifiers run.
* ``whole-suite`` (default) - every existing test file under the test roots.
* ``module-dir`` - the tests in the directories that hold a test for a changed file. A changed
  file maps to a test by mirror (strip a leading ``src`` and the top-level package, look in
  ``<test_root>/<remaining dirs>``) or by name (``test_<stem>.py`` / ``<stem>_test.py``
  anywhere under the roots). Every test directly in a matched test's directory is selected:
  on a flat layout ``tests/test_base.py`` brings in its sibling ``tests/test_sibling.py``
  (the regression #117 is about). A changed file that maps to nothing selects the tests
  directly in the test roots, never the whole tree. Changed test files are not sources. A selected
  directory with more than ``MODULE_DIR_CAP`` test files contributes only the files named after a
  change (#333: whole flat directories of a large repo made the base probe time out); the cut
  directories are recorded in the P2P rule (``cap_note``), so a cut to nothing is visible.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from posixpath import basename, dirname, splitext

from cairnwork.gitcmd import GitCommandError
from cairnwork.pytest_config import (
    DEFAULT_PYTHON_FILES,
    PytestConfigError,
    configured_python_files,
    test_file_rule,
)

MODES = ("off", "whole-suite", "module-dir")
DEFAULT_MODE = "whole-suite"  # Intake (cairnwork-route, issue runner)
DIRECT_MODE = "off"  # TaskSpec built directly (pipeline.run_task, drills): no P2P unless set
DEFAULT_BUDGET_SECONDS = 300  # seconds per P2P run (probe, candidate run, re-run)
MODULE_DIR_CAP = 25  # test files a module-dir directory may bring in whole (#333)
# the id p2p.py gives the verifier it adds (p2p-<applied mode>); no task may take one (#644)
VERIFIER_IDS = tuple(f"p2p-{mode}" for mode in MODES if mode != "off")


def name_rule(repo: Path, base_commit: str | None) -> Callable[[str], bool] | None:
    """The base's python_files rule for the name match (#870). None, the exact default names that
    main matches, when the base sets no python_files or just pytest's default (so the default
    selection is unchanged), or when its config is unreadable: p2p._base_tests has already
    refused such a base before this runs."""
    try:
        if set(configured_python_files(repo, base_commit)) <= set(DEFAULT_PYTHON_FILES):
            return None
        return test_file_rule(repo, base_commit)
    except (GitCommandError, PytestConfigError):
        return None


def _under_root(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == r or path.startswith(f"{r}/") for r in roots)


def _named(test: str, stem: str, is_test: Callable[[str], bool] | None) -> bool:
    """A test file named after a changed file's ``stem``: test_<stem>.py / <stem>_test.py, or with
    the base's python_files rule any matching file holding the stem as a name part (#870)."""
    if is_test is None:
        return basename(test) in {f"test_{stem}.py", f"{stem}_test.py"}
    part = rf"(?:^|[-_.]){re.escape(stem)}(?:[-_.]|$)"
    return is_test(test) and bool(re.search(part, splitext(basename(test))[0]))


def _mirror_subpath(changed_dir: str) -> str:
    """Drop a leading ``src`` and then the top-level package; what remains mirrors under a
    test root (possibly empty)."""
    parts = [p for p in changed_dir.split("/") if p]
    if parts and parts[0] == "src":
        parts = parts[1:]
    return "/".join(parts[1:])


def select_p2p_targets(
    changed_files: tuple[str, ...],
    existing_tests: tuple[str, ...],
    test_roots: tuple[str, ...],
    mode: str = DEFAULT_MODE,
    is_test: Callable[[str], bool] | None = None,
) -> tuple[str, ...]:
    """Existing test files to consider as P2P candidates (sorted, de-duplicated). Paths are
    repo-relative POSIX; ``existing_tests`` are the test files on the base under ``test_roots``.
    ``is_test`` is the base's ``python_files`` rule (pytest_config.test_file_rule); a test then
    names a change when it matches it and holds the changed file's stem as a name part (#870).
    Raises ValueError on an unknown mode."""
    if mode not in MODES:
        raise ValueError(f"unknown p2p_selection mode {mode!r}; one of {MODES}")
    if mode == "off" or not existing_tests:
        return ()
    tests = frozenset(existing_tests)
    if mode == "whole-suite":
        return tuple(sorted(tests))
    return tuple(sorted(_module_dir(changed_files, tests, test_roots, is_test)[0]))


def _module_dir(
    changed_files: tuple[str, ...],
    tests: frozenset[str],
    test_roots: tuple[str, ...],
    is_test: Callable[[str], bool] | None = None,
) -> tuple[set[str], set[str]]:
    """(selected test files, directories cut to the files named after a change)."""
    size: dict[str, int] = {}
    for t in tests:
        size[dirname(t)] = size.get(dirname(t), 0) + 1
    root_level = {dirname(t) for t in tests if dirname(t) in test_roots}
    selected: set[str] = set()
    cut: set[str] = set()
    for changed in changed_files:
        if _under_root(changed, test_roots):
            continue
        tail = _mirror_subpath(dirname(changed))
        mirror = {f"{r}/{tail}" for r in test_roots} if tail else set()
        stem = splitext(basename(changed))[0]
        hit = {dirname(t) for t in tests if dirname(t) in mirror or _named(t, stem, is_test)}
        for d in hit or root_level:
            whole = size[d] <= MODULE_DIR_CAP
            cut |= set() if whole else {d}
            selected |= {
                t for t in tests if dirname(t) == d and (whole or _named(t, stem, is_test))
            }
    return selected, cut


def cap_note(
    changed_files: tuple[str, ...],
    existing_tests: tuple[str, ...],
    test_roots: tuple[str, ...],
    mode: str,
    is_test: Callable[[str], bool] | None = None,
) -> dict:
    """What the P2P rule adds when module-dir cut a directory (#333); {} when nothing was cut."""
    if mode != "module-dir":
        return {}
    cut = _module_dir(changed_files, frozenset(existing_tests), test_roots, is_test)[1]
    return {"capped_dirs": sorted(cut), "cap": MODULE_DIR_CAP} if cut else {}


def check_p2p_config(loaded: dict) -> None:
    """The state config's ``p2p_selection`` and ``p2p_time_budget_seconds``; ValueError if bad."""
    if loaded.get("p2p_selection", DEFAULT_MODE) not in MODES:
        raise ValueError(f"p2p_selection must be one of {sorted(MODES)}")
    seconds = loaded.get("p2p_time_budget_seconds", 1)
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 86400:
        raise ValueError("p2p_time_budget_seconds must be a whole number of seconds")
