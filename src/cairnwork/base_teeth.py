"""Which target tests give teeth on the base (#775). A target that failed in fixture setup, or
whose every failed call failed from the environment (an OSError, a MemoryError, a
RecursionError, or a child process that could not run, in the exception or its cause/context
chain; env_failure.py, #808), failed because of the environment as much as the code
(report_guard's ``fail_kind``): it gives no teeth. One target with real teeth is enough
(every target must still pass on the candidate). Without one, an environment failure makes the base
UNCOMPARABLE rather than TEETH; no failure at all is NO_TEETH. Raw node ids are looked up in
``fail_kind`` before a ``[case]`` is folded into its target."""

from __future__ import annotations

from cairnwork.node_file import file_part
from cairnwork.p2p_function import _without_case
from cairnwork.reasons import _names

_PLUGIN_HINT = "; install the plugin in the verifier's Python (pytest_config.PLUGINS)"
_SETUP_HINT = "; import fix-introduced symbols inside the test function"


def _shown(nodeids: list[str], hidden: bool) -> str:
    """#1186: a run of a not-yet-published test commit reports how many tests, never which."""
    return f"{len(nodeids)} test(s)" if hidden else _names(nodeids)


def base_teeth(runs, targets) -> tuple[str, str, str]:
    """``(kind, code, detail)`` for ``Comparison``: TEETH, UNCOMPARABLE or NO_TEETH."""
    setup: list[str] = []
    env: list[str] = []
    plugin: list[str] = []
    hidden = any(getattr(run, "hidden_tests", False) for run in runs)
    for run in runs:
        for nodeid, outcome in run.outcomes.items():
            if outcome != "failed" or not (
                {_without_case(nodeid), _without_case(nodeid, all_parts=True)} & targets
            ):
                continue
            kind = run.fail_kind.get(nodeid)
            if kind is None:
                return ("TEETH", "", "")
            {"setup": setup, "plugin": plugin}.get(kind, env).append(nodeid)
    if plugin and not setup:  # #804: only a missing table plugin's fixture stopped the base
        detail = f"a pytest plugin fixture is missing for {_shown(plugin, hidden)}"
        return ("UNCOMPARABLE", "PLUGIN_NOT_INSTALLED", detail + _PLUGIN_HINT)
    if setup or plugin:
        setup += plugin
        files = {file_part(target) for target in targets}
        hint = _SETUP_HINT if any(file_part(n) in files for n in setup) else ""
        detail = f"fixture setup failed for {_shown(setup, hidden)}{hint}"
        return ("UNCOMPARABLE", "BASE_SETUP_ERROR", detail)
    if env:
        detail = (
            "the test body failed from the environment (OSError, MemoryError, RecursionError, "
            f"or a subprocess that could not run) for {_shown(env, hidden)}"
        )
        return ("UNCOMPARABLE", "BASE_ENV_ERROR", detail)
    return ("NO_TEETH", "", "")
