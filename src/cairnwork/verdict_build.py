"""The record-to-Verdict half of report_guard.parse_verdict (moved out for the 400-line cap,
#851). The size cap and JSON decode stay in report_guard, so tests patching its
MAX_REPORT_BYTES still work."""

from __future__ import annotations

from cairnwork.collect_import import import_hints
from cairnwork.data_exec_hook import refused
from cairnwork.pytest_config import PLUGINS
from cairnwork.report_guard_exposed import exposed_values
from cairnwork.skip_hint import module_names as _module_names
from cairnwork.verdict_record import (
    Verdict,
    _parse_skips,
    _sources,
    denied_paths,
    setup_denials,
    well_formed,
)

PLUGIN_KINDS = frozenset(f"plugin:{module}" for _, module, _ in PLUGINS)


def build_verdict(data: dict) -> Verdict:
    collected = data.get("collected")
    outcomes = data.get("outcomes")
    skips = _parse_skips(data.get("skips", {}))
    # fail closed on any malformed shape (a candidate controls the file): node ids and outcomes
    # must be strings, else set()/membership/str compares could misbehave or crash the parent.
    if not isinstance(collected, list) or not all(isinstance(node, str) for node in collected):
        return Verdict(False, {})
    if not isinstance(outcomes, dict) or not all(
        isinstance(node, str) and isinstance(result, str) for node, result in outcomes.items()
    ):
        return Verdict(False, {})
    if skips is None:
        return Verdict(False, {})
    if any(node not in outcomes for node in skips):
        return Verdict(False, {})
    shape, data_refused = well_formed(data, collected, outcomes, PLUGIN_KINDS), refused(data)
    if shape is None or data_refused is None:
        return Verdict(False, {})
    errors, skipped, renamed, teardown, files, kind, file_of, status = shape
    ran = (
        not data_refused  # #850: a declared data file was executed, refused or not
        and bool(collected)
        and not errors
        and not skipped
        and not renamed
        and not teardown
        and status == (0, 0)
        and set(outcomes) == set(collected)
        and all(outcome == "passed" for outcome in outcomes.values())
    )
    return Verdict(
        ran,
        outcomes,
        skips=skips,
        recorded=True,
        collect_errors=tuple(errors),
        collect_skipped=tuple(skipped),
        skipped_deps=_module_names(data),
        **import_hints(data),
        renamed=tuple(renamed),
        teardown_errors=tuple(teardown),
        fail_kind=kind,
        denied=denied_paths(data, kind),
        setup_denied=setup_denials(data, kind),
        paths=_sources(collected, files, file_of or ()),
        exitstatus=status[0],
        testsfailed=status[1],
        collected=tuple(collected),
        collection_finished=data.get("collection_finished") is True,
        data_refused=data_refused,
        uncollected_exposed=exposed_values(data),
    )
