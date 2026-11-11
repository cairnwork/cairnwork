"""Refuse own import-system edits that can change what the deferred tests import (#1065).

An own changed file whose added or removed statements reach the import machinery (sys.modules,
finders, importlib, __import__, runpy, pytest_plugins, or a dynamic-access word; see
candidate_statements.py) is refused outright: such a swap needs no file. A one-line fix in a
function that never reaches machinery is not refused even when the file uses getattr elsewhere,
while calling, moving or rewriting machinery, or flipping the block that wraps it, is. A file
with only a path redirect or program launch (sys.path, __path__, site, PYTHONPATH, subprocess) is
refused only when a file the candidate changed can shadow an imported module (candidate_shadow.py),
so an honest edit that inserts its own directory still verifies.
Residuals (#1115), left to the blackbox and verifier checks: base machinery reached through
same-file aliases, registries or dunders; other control-flow edits inside code that reaches
machinery. Also unscanned: a ``.pyc`` without source; sys.path outside the repo; a write in one
changed file and the path edit in another changed file is not combined. Accepted false refusals:
a new or changed call to same-file code reaching import machinery; an added module some file
imports, or a changed one sharing a module name, next to a redirect; an added or changed
statement pair that writes a file and edits sys.path in one file, and a changed statement that
uses a sys alias on the import tables.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cairnwork.candidate_alias import alias_machinery_changed, runtime_shadow_added
from cairnwork.candidate_shadow import find_shadow_file
from cairnwork.candidate_statements import machinery_changed
from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.gitcmd import GitCommandError, run_git_bytes
from cairnwork.sys_path_names import DYNAMIC_ATTRS, DYNAMIC_NAMES
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected

_OID = re.compile(r"^[0-9a-f]{40}$")
_OWN_KINDS = frozenset({"ADD", "MODIFY"})
_REDIRECT, _MACHINERY, _RUNTIME = "redirect", "machinery", "runtime-shadow"
# Fail closed: a signalling file counts as a pure redirect only if no new or changed statement
# names one of these (unparsed source, so string-built machinery names still fall here).
# Same-file calls are followed only into import machinery, not dynamic access (#1111 review).
_REACH_WORDS = tuple(
    word.encode()
    for word in (
        "sys.modules", "meta_path", "path_hooks", "path_importer_cache", "importlib", "runpy",
        "pytest_plugins", "__import__",
    )
)  # fmt: skip
_MACHINERY_WORDS = _REACH_WORDS + tuple(
    word.encode() for word in (*sorted(DYNAMIC_NAMES), *sorted(DYNAMIC_ATTRS))
)

# Literal attribute names that can expose import machinery, execute code, intercept runtime
# behavior, or mutate containers holding that machinery. Container mutators reached through
# getattr are refused; dunder mutators such as __setitem__ are already refused by the underscore
# rule. Source: Python import machinery, execution primitives, and interception hooks.
DYNAMIC_ACCESS_DENYLIST = frozenset(
    {
        "modules",
        "path",
        "meta_path",
        "path_hooks",
        "path_importer_cache",
        "builtins",
        "import_module",
        "reload",
        "find_spec",
        "exec_module",
        "loader",
        "spec",
        "builtin_module_names",
        "stdlib_module_names",
        "invalidate_caches",
        "find_loader",
        "create_module",
        "load_module",
        "origin",
        "submodule_search_locations",
        "util",
        "machinery",
        "abc",
        "add",
        "discard",
        "__import__",
        "exec",
        "eval",
        "compile",
        "open",
        "settrace",
        "setprofile",
        "addaudithook",
        "excepthook",
        "displayhook",
        "breakpointhook",
        "append",
        "insert",
        "extend",
        "remove",
        "pop",
        "popitem",
        "clear",
        "update",
        "setdefault",
        "sort",
        "reverse",
    }
)


def refuse_new_sys_path_mutation(
    repo: Path,
    base: str,
    candidate: str,
    allowed: tuple[str, ...] | list[str],
    test_commit: str | None = None,
) -> None:
    """Refuse a changed own Python file with a source-visible import-system signal when a file the
    candidate added or modified can shadow an imported module. Only ``allowed`` files are read for
    signals; test trees contribute imported names for shadow detection only."""
    try:
        if not _OID.fullmatch(base) or not _OID.fullmatch(candidate):
            raise ValueError("base and candidate must be full 40-character object ids")
        if test_commit is not None and not _OID.fullmatch(test_commit):
            raise ValueError("test commit must be a full 40-character object id")
        changes = read_change_set(repo, base, candidate)
        own = set(allowed)  # on the worker path the judged commit also adds the hidden tests
        changed = {
            e.path: e.change_type
            for e in changes.entries
            if e.change_type in _OWN_KINDS and e.path in own
        }
        for path in changed:
            signal = path.endswith(".py") and _blob_signal(repo, base, candidate, path, changed)
            if signal:  # a base signal is no exemption: it may be a comment next to a redirect
                if signal == _MACHINERY:  # an in-process swap needs no file (#303 SWAP, finders)
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{path}: edits the import machinery in-process (sys.modules, finders, "
                        "importlib, __import__, runpy or pytest_plugins)",
                    )
                if signal == _RUNTIME:
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{path}: writes a file and edits the import path in the same change "
                        "(a shadow module can be created at run time)",
                    )
                shadow = find_shadow_file(repo, candidate, test_commit, changed)
                if shadow:
                    raise CandidateRejected(
                        "CANDIDATE_SYS_PATH_MUTATION",
                        f"{path}: has a source-visible sys.path/import-machinery signal; "
                        f"{shadow} can shadow an imported module",
                    )
    except CandidateRejected:
        raise
    except (
        ChangeSetError,
        GitCommandError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        # A failed scan cannot establish verifier integrity, so it must not lead to VERIFIED.
        detail = f"cannot scan candidate import paths: {exc}"
        raise CandidateRejected("UNREADABLE_CHANGE", detail) from exc


def _blob_signal(repo: Path, base: str, commit: str, path: str, changed: dict[str, str]) -> str:
    """Read and scan one committed blob: "" (none), _REDIRECT (a path or program launch, which
    needs a shadow file) or _MACHINERY (an added or removed statement reaches machinery).
    Unreadable or unparseable candidate source fails closed."""
    data = run_git_bytes(repo, "cat-file", "blob", f"{commit}:{path}")
    signal = scan_sys_path_blob(data)
    if signal is None:
        raise ValueError(f"{commit}:{path} could not be parsed or decoded")
    added = changed[path] == "ADD"
    old = None if added else run_git_bytes(repo, "cat-file", "blob", f"{base}:{path}")
    if alias_machinery_changed(old, data):
        return _MACHINERY
    if not signal:
        return ""
    if machinery_changed(old, data, _MACHINERY_WORDS, _REACH_WORDS, DYNAMIC_ACCESS_DENYLIST):
        return _MACHINERY
    if runtime_shadow_added(old, data):
        return _RUNTIME
    return _REDIRECT
