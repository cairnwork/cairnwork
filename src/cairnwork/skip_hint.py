"""Parent-side parse of the skipped-dependency hint in a verifier record (#813).

Not part of the plugin source in report_guard.py: the plugin cannot import cairnwork."""

from __future__ import annotations


def is_module(name: str) -> bool:
    """A dotted module name as the record may carry it (candidate-written: nothing else shown)."""
    return len(name) <= 100 and all(part.isidentifier() for part in name.split("."))


def module_names(data: dict) -> tuple[str, ...]:
    """The modules the record says skipped collectors could not import (#813): a hint for the
    reason, never read by the verdict, so anything but a list of module names is dropped."""
    deps = data.get("collect_skipped_deps")
    names = (
        [d for d in deps if isinstance(d, str) and is_module(d)] if isinstance(deps, list) else []
    )
    return tuple(dict.fromkeys(names))
