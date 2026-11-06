"""Parent-side parse of the import-error record in a verifier record (#796 step 1).

Not part of the plugin source in report_guard.py: the plugin cannot import cairnwork. The guard
records, per test module whose own import statement failed with "cannot import name X from
<module>", the node id, the module and the file Python found it in (relative to the checkout).
The record is candidate-reachable, so every field is re-validated here and anything malformed is
dropped. ``as_failed`` (step 2) reads it for the base comparison."""

from __future__ import annotations

from dataclasses import replace

from cairnwork.skip_hint import is_module

MAX_ENTRIES = 100
_MAX_PATH = 300
_NOT_REPO = frozenset({".venv", "site-packages", "node_modules"})


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
    If it ever counts teeth per test, a target that never uses the missing name gets teeth."""
    origin_of = {node: origin for node, _module, origin in run.import_errors}
    files = {target.partition("::")[0] for target in targets}
    if not run.collect_errors:
        return run
    for node in run.collect_errors:
        origin = origin_of.get(node)
        if node not in files or origin is None or is_test(origin):
            return run
        if _NOT_REPO & set(origin.split("/")):
            return run
    marked = [t for t in targets if t.partition("::")[0] in set(run.collect_errors)]
    outcomes = {**run.outcomes, **dict.fromkeys(marked, "failed")}
    collected = (*run.collected, *(t for t in marked if t not in run.collected))
    return replace(run, collect_errors=(), outcomes=outcomes, collected=collected)
