"""Parent-side parse of the import-error record in a verifier record (#796 step 1).

Not part of the plugin source in report_guard.py: the plugin cannot import cairnwork. The guard
records, per test module whose own import statement failed with "cannot import name X from
<module>", the node id, the module and the file Python found it in (relative to the checkout).
Nothing reads this yet: no verdict changes. The record is candidate-reachable, so every field is
re-validated here and anything malformed is dropped."""

from __future__ import annotations

from cairnwork.skip_hint import is_module

MAX_ENTRIES = 100
_MAX_PATH = 300


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
