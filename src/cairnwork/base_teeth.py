"""Which target tests give teeth on the base (#775). A target that failed in fixture setup, or
whose every failed call raised an ``OSError``, failed because of the environment as much as the
code (report_guard's ``fail_kind``): it gives no teeth. One target with real teeth is enough
(every target must still pass on the candidate). Without one, an environment failure makes the base
UNCOMPARABLE rather than TEETH; no failure at all is NO_TEETH. Raw node ids are looked up in
``fail_kind`` before a ``[case]`` is folded into its target."""

from __future__ import annotations

from cairnwork.reasons import _names

_SETUP_HINT = "; import fix-introduced symbols inside the test function"


def base_teeth(runs, targets) -> tuple[str, str, str]:
    """``(kind, code, detail)`` for ``Comparison``: TEETH, UNCOMPARABLE or NO_TEETH."""
    setup: list[str] = []
    env: list[str] = []
    for run in runs:
        for nodeid, outcome in run.outcomes.items():
            if outcome != "failed" or nodeid.split("[", 1)[0] not in targets:
                continue
            kind = run.fail_kind.get(nodeid)
            if kind is None:
                return ("TEETH", "", "")
            (setup if kind == "setup" else env).append(nodeid)
    if setup:
        files = {target.split("::", 1)[0] for target in targets}
        hint = _SETUP_HINT if any(n.split("::", 1)[0] in files for n in setup) else ""
        detail = f"fixture setup failed for {_names(setup)}{hint}"
        return ("UNCOMPARABLE", "BASE_SETUP_ERROR", detail)
    if env:
        detail = f"the test body raised an OSError for {_names(env)}"
        return ("UNCOMPARABLE", "BASE_ENV_ERROR", detail)
    return ("NO_TEETH", "", "")
