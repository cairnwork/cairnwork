"""Parent-side parse of the import-error record in a verifier record (#796 step 1).

Not part of the plugin source in report_guard.py: the plugin cannot import cairnwork. The guard
records, per test module whose own import statement failed with "cannot import name X from
<module>", the node id, the module and the file Python found it in (relative to the checkout).
The record is candidate-reachable, so every field is re-validated here and anything malformed is
dropped. ``as_failed`` (step 2) reads it for the base comparison. S3 adds a second record,
``collect_missing_submodules`` (a new submodule of an in-repo package), parsed the same way."""

from __future__ import annotations

import keyword
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import TypedDict

from cairnwork.skip_hint import is_module

MAX_ENTRIES = 100
_MAX_PATH = 300
_NOT_REPO = frozenset({".venv", "site-packages", "node_modules"})
# #796 S3: more vendored or tool trees, for the new submodule record only (S2's list: #933)
_NOT_REPO_S3 = _NOT_REPO | {"dist-packages", ".tox", ".nox", "_vendor", "vendor", "third_party"}
# S3: a missing leaf with one of these names is test-suite or build output, not fix code
_HELD_BACK = ("test*", "*_test", "*tests", "conftest*", "fixture*", "mock*",
              "_version*", "version", "*_pb2*", "*_ext", "*_cy")  # fmt: skip


def _relative(path: str) -> bool:
    """A plain relative file path: no absolute form, no ``..``, nothing that is not printable."""
    parts = path.split("/")
    return (
        0 < len(path) <= _MAX_PATH
        and path.isprintable()
        and "\\" not in path
        and not path.startswith("/")
        and all(part not in ("", ".", "..") for part in parts)
    )


def import_errors(data: dict) -> tuple[tuple[str, str, str], ...]:
    """The ``(node, module, origin)`` entries of ``collect_import_errors``, each re-validated:
    a string node id that is a relative file path, a dotted module name, and a relative file
    path. Anything but a list of such dicts (or an entry that is not) is dropped, so an old
    record without the field gives ``()``."""
    raw = data.get("collect_import_errors")
    if not isinstance(raw, list):
        return ()
    found = []
    for entry in raw[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        node, module, origin = entry.get("node"), entry.get("module"), entry.get("origin")
        if (
            isinstance(node, str)
            and isinstance(module, str)
            and isinstance(origin, str)
            and _relative(node)
            and module
            and is_module(module)
            and _relative(origin)
        ):
            found.append((node, module, origin))
    return tuple(dict.fromkeys(found))


def missing_submodules(data: dict) -> tuple[tuple[str, str, str], ...]:
    """#796 S3: the ``(node, module, parent_origin)`` entries of ``collect_missing_submodules``
    (a test module whose own import statement failed with "No module named 'pkg.newmod'" while
    ``pkg`` was loaded from ``parent_origin``), each re-validated: a relative node path, a dotted
    module name whose parts are identifiers and not keywords, and a relative path that is the
    parent package's own ``__init__.py`` (any prefix, for src layouts). Kept apart from
    ``collect_import_errors``: a reader of that list would read an entry as "the module exists".
    Anything malformed is dropped; an old record without the field gives ``()``. Unread so far."""
    raw = data.get("collect_missing_submodules")
    if not isinstance(raw, list):
        return ()
    found = []
    for entry in raw[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        node, module = entry.get("node"), entry.get("module")
        origin = entry.get("parent_origin")
        if not (isinstance(node, str) and isinstance(module, str) and isinstance(origin, str)):
            continue
        parts = module.split(".")
        tail = "/".join(parts[:-1]) + "/__init__.py"
        if (
            _relative(node)
            and len(parts) > 1
            and is_module(module)
            and not any(keyword.iskeyword(part) for part in parts)
            and _relative(origin)
            and (origin == tail or origin.endswith("/" + tail))
        ):
            found.append((node, module, origin))
    return tuple(dict.fromkeys(found))


class ImportHints(TypedDict):
    import_errors: tuple[tuple[str, str, str], ...]
    submodule_errors: tuple[tuple[str, str, str], ...]


def import_hints(data: dict) -> ImportHints:
    """Both import records, as the ``Verdict`` fields of the same names (report_guard.py is at
    its line cap, so it takes them in one place)."""
    return ImportHints(import_errors=import_errors(data), submodule_errors=missing_submodules(data))


def as_failed(run, targets, is_test):
    """A base run whose only collect errors are test modules that failed on "cannot import name X
    from <module>", where <module> is an in-repo file the base could import: the same evidence the
    in-function form gives (the name is missing on the base), so return the run with those errors
    cleared and the new or changed ``targets`` of those files marked failed (no ``fail_kind``).
    Anything else, even one other collect error or a record that does not pass, returns ``run``
    unchanged (BASE_COLLECTION_ERROR, UNCOMPARABLE). Only collect_errors, outcomes and collected
    change, so every other check of ``_base_problem`` still reads what it read.

    TRUST: the guard's record is a hint, not evidence. Repo code can build an ImportError whose
    innermost frame is the test file, and a test file can raise one itself, so the record can be
    made to qualify. What stops a wrong candidate is the candidate run: it must collect and pass
    every target, and a forged error is raised there too (tests/test_collect_import_teeth.py).

    DEPENDENCY: marking every target of the file failed is safe only because ``base_teeth`` grants
    TEETH when ANY target failed without a fail_kind (tests/test_collect_import_teeth.py pins it).
    If it ever counts teeth per test, a target that never uses the missing name gets teeth.

    S3 (#796): a collect error recorded in ``submodule_errors`` ("No module named 'pkg.newmod'"
    while ``pkg`` loaded from an in-repo ``__init__.py``) qualifies the same way when
    ``_new_submodule`` passes; a node in both records must pass both. Residual (written in
    PRESERVED_BEHAVIORS): a neutral-named missing module under the source tree is trusted as fix
    code, and an extension or generated module the verifier cannot build is wrongly attributed
    to the candidate (FAILED, never VERIFIED)."""
    checks: dict[str, list[bool]] = {}  # node -> one verdict per entry; a node in both lists
    for node, _module, origin in run.import_errors:  # must pass both rule sets
        checks.setdefault(node, []).append(_in_repo(origin, is_test, _NOT_REPO))
    for node, module, origin in run.submodule_errors:
        checks.setdefault(node, []).append(_new_submodule(module, origin, is_test))
    files = {target.partition("::")[0] for target in targets}
    if not run.collect_errors:
        return run
    for node in run.collect_errors:
        if node not in files or not checks.get(node) or not all(checks[node]):
            return run
    marked = [t for t in targets if t.partition("::")[0] in set(run.collect_errors)]
    outcomes = {**run.outcomes, **dict.fromkeys(marked, "failed")}
    collected = (*run.collected, *(t for t in marked if t not in run.collected))
    return replace(run, collect_errors=(), outcomes=outcomes, collected=collected)


def _in_repo(origin: str, is_test, not_repo: frozenset[str]) -> bool:
    """S2's rule for the file the base imported: not a test file, not under a vendored tree."""
    return not is_test(origin) and not (not_repo & set(origin.split("/")))


def _new_submodule(module: str, parent_origin: str, is_test) -> bool:
    """#796 S3: the parent package passes the S2 rule (wider vendored list), and the missing
    submodule looks like code the fix adds: no directory named tests/test above it, a leaf name
    that is not test-suite or build output, and neither ``<leaf>.py`` nor ``<leaf>/__init__.py``
    beside the parent's ``__init__.py`` would be a test file by the base's rule."""
    folder, leaf = parent_origin.rpartition("/")[0], module.rpartition(".")[2]
    would = (f"{folder}/{leaf}.py", f"{folder}/{leaf}/__init__.py")
    return (
        _in_repo(parent_origin, is_test, _NOT_REPO_S3)
        and not {"tests", "test"} & set(folder.split("/"))
        and not any(fnmatchcase(leaf, pattern) for pattern in _HELD_BACK)
        and not any(is_test(path) for path in would)
    )
