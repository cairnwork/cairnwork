"""#1768: the file set pytest collects against the file set Cairnwork counts, on the base.

Step 1: a pytest config below the root that sets a test-naming key. The verifier's pinned ini
never reads it, so a run from the root cannot see it; it is refused on its own narrow rule.
Anything that cannot be read as a config is refused too, never passed.

Step 2: ``file_parity`` collects the directories the P2P run covers (collect-only, the
verifier's own sandbox and ini) and compares the files pytest collected with the files
Cairnwork counts (tracked, under those directories, matching ``python_files``). A counted file
pytest did not collect (``collect_ignore``, ``collect_ignore_glob``, ``pytest_ignore_collect``,
a hook that empties a module) is UNCOMPARABLE ``P2P_BASE_FILES_UNCOLLECTED``; a run that cannot
be read is ``P2P_BASE_COLLECT_UNUSABLE``, never a pass. A module that defines nothing pytest
would collect (helpers, mixins) and a skipped module are not hidden files. Items inside a
collected file are not compared: the verifier needs the file set only.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

from cairnwork.collect_reader import _unclean
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.gitcmd import GitCommandError, run_git, run_git_bytes
from cairnwork.node_file import file_part
from cairnwork.pytest_config import (
    DEFAULT_PYTHON_FILES,
    _file_matches,
    base_settings,
    configured_python_files,
)
from cairnwork.pytest_config_files import _CANDIDATES, PytestConfigError, _section
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

NAMING_KEYS = ("python_files", "python_classes", "python_functions")
NESTED = "P2P_BASE_NESTED_NAMING"
UNCOLLECTED = "P2P_BASE_FILES_UNCOLLECTED"
UNUSABLE = "P2P_BASE_COLLECT_UNUSABLE"
_FORMS = {(name, section): form for name, section, form in _CANDIDATES}
_NAMES = frozenset(name.casefold() for name, _ in _FORMS)  # APFS reads PYTEST.INI


def nested_naming(task) -> None:
    """Raise UNCOMPARABLE ``P2P_BASE_NESTED_NAMING`` when a config file below the base's root sets
    ``python_files``, ``python_classes`` or ``python_functions`` (or cannot be read)."""
    try:
        listing = run_git_bytes(task.repo, "ls-tree", "-r", "-z", task.base_commit, "--")
    except GitCommandError as exc:
        raise _refuse("the base tree cannot be listed") from exc
    for entry in filter(None, listing.decode("utf-8", "surrogateescape").split("\0")):
        meta, _, path = entry.partition("\t")
        if "/" not in path or path.rsplit("/", 1)[1].casefold() not in _NAMES:
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
        if file == name.casefold():
            table = _section(text, section, form, file)
            opts = str(table.get("addopts", "")) if table else ""  # ``-o python_files=...``
            found |= {k for k in NAMING_KEYS if table and (k in table or k in opts)}
    return sorted(found)


def _shown(path: str) -> str:
    return path.encode("ascii", "backslashreplace").decode()[:80]  # a path is candidate text


def _refuse(why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", NESTED, f"{why} below the root"))


def file_mismatch(counted, collected) -> list[str]:
    """The counted files pytest did not collect, sorted (an extra collected file is no mismatch)."""
    return sorted(set(counted) - set(collected))


def file_parity(task, files, ctx, budget, attempt_id) -> None:
    """Raise UNCOMPARABLE when the base's collection and its counted test files differ in files,
    for the directories of the selected ``files`` (what this P2P run covers)."""
    dirs = _dirs(files)
    counted = _counted(task, dirs)
    if not counted:
        return
    spec = VerifierSpec("p2p-base-files", dirs, collect_only=True)
    runs = run_verifiers(ctx, task.base_commit, (spec,), f"{attempt_id}-f", timeout=budget)
    try:
        collected, why = _collected_files(runs[0])
    finally:
        for run in runs:  # evidence for this plan only, not for Core
            shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if why:
        raise _refuse_with(UNUSABLE, f"{why} on the base")
    missing = [p for p in file_mismatch(counted, collected) if _may_have_tests(task, p)]
    if missing:
        shown = ", ".join(_shown(p) for p in missing[:3])
        raise _refuse_with(
            UNCOLLECTED, f"{len(missing)} counted test file(s) not collected: {shown}"
        )


def _dirs(files) -> tuple[str, ...]:
    """The parent directories of ``files``, without one that lies under another."""
    tops = sorted({f.rsplit("/", 1)[0] for f in files if "/" in f}, key=lambda d: (len(d), d))
    kept: list[str] = []
    for d in tops:
        if not any(d == k or d.startswith(f"{k}/") for k in kept):
            kept.append(d)
    return tuple(sorted(kept))


def _counted(task, dirs) -> list[str]:
    """Tracked files under ``dirs`` that match the base's ``python_files`` as pytest reads them: a
    pattern with a ``/`` matches the checkout's absolute path, so no default is added here (that
    widening would count files pytest never collects)."""
    try:
        raw = run_git_bytes(task.repo, "ls-tree", "-r", "-z", "--name-only", task.base_commit, "--")
        patterns = configured_python_files(task.repo, task.base_commit) or DEFAULT_PYTHON_FILES
    except (GitCommandError, PytestConfigError) as exc:
        raise _refuse_with(UNUSABLE, "the base test files cannot be listed") from exc
    folded = tuple(f"{d.casefold()}/" for d in dirs)
    names = raw.decode("utf-8", "surrogateescape").split("\0")
    return sorted(
        p
        for p in names
        if p.casefold().startswith(folded) and any(_file_matches(x, p) for x in patterns)
    )


def _collected_files(run) -> tuple[set[str], str]:
    """(files pytest reached, "") or (set(), why the run says nothing about files). A skipped
    module (``importorskip``, a module-level skip) was reached, its tests are not known."""
    why = _unclean(run)
    if why:
        return set(), why
    if run.collect_errors or run.returncode not in (0, 5):
        return set(), f"collect-rc-{run.returncode}"  # a failed collector: its files are unknown
    return {file_part(n) for n in (*run.collected, *run.collect_skipped)}, ""


def _may_have_tests(task, path: str) -> bool:
    """Whether a counted file nobody collected from could have held a test: a module that defines
    no test function or class (a helper, a mixin, a class with ``__init__``) collects nothing, and
    that is not a hidden file. Anything unreadable, or a custom naming rule, says yes."""
    try:
        custom = {"python_classes", "python_functions"} & {
            k for k, _ in base_settings(task.repo, task.base_commit).values
        }
        tree = ast.parse(run_git(task.repo, "cat-file", "blob", f"{task.base_commit}:{path}"))
        return _tests_in(tree.body, bool(custom))
    except (GitCommandError, PytestConfigError, UnicodeDecodeError, SyntaxError, ValueError,
            RecursionError, MemoryError):  # fmt: skip
        return True


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _tests_in(nodes, custom: bool) -> bool:
    for node in nodes:
        if isinstance(node, _DEFS):
            hit = custom or node.name.startswith("test")
        elif isinstance(node, ast.ClassDef):
            hit = _class_has_tests(node, custom)
        else:  # if / try / with blocks at module level
            hit = _tests_in(ast.iter_child_nodes(node), custom)
        if hit:
            return True
    return False


def _class_has_tests(node: ast.ClassDef, custom: bool) -> bool:
    if node.bases or node.keywords:  # may inherit tests or be a unittest case: say yes
        return True
    methods = [m for m in node.body if isinstance(m, _DEFS)]
    if any(m.name in ("__init__", "__new__") for m in methods):  # pytest skips such a class
        return False
    if not (custom or node.name.startswith("Test")):
        return False
    return any(custom or m.name.startswith("test") for m in methods)


def _refuse_with(code: str, why: str) -> BaseUncomparable:
    return BaseUncomparable(Comparison("UNCOMPARABLE", code, why))
