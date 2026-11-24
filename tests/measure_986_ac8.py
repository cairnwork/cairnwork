"""#986 AC8 measurement driver (run on demand by .github/workflows/measure-986.yml, not collected
by the normal suite: the file name has no ``test_`` prefix).

Each case is one real end-to-end task (the #986 step 5b scenario of test_base_fixture_names_e2e)
run with the base-collect step ON (the real ``base_fixture_names.probe``) or OFF (``collect``
returns None, the #916 rule alone). A bad candidate (a binding of a name only a run-time plugin
provides) and a good one (same binding, no plugin) alternate, arms alternate within each pair so
drift hits both. One JSON line per case goes to $CW986_OUT; no assertions: the summary decides."""

import json
import os
import time
from typing import Any

import pytest
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed)
from test_base_fixture_names_e2e import PLUGIN, _run, project  # noqa: F401

from cairnwork import base_fixture_names

pytestmark = pytest.mark.sandbox

N = int(os.environ.get("CW986_N", "20"))
SCENARIOS = {"bad": PLUGIN, "good": "# no plugin here\n"}
CASES = [
    (i, arm, name)
    for i in range(N)
    for name in SCENARIOS
    for arm in (("on", "off") if i % 2 == 0 else ("off", "on"))
]


@pytest.mark.parametrize(("i", "arm", "name"), CASES, ids=[f"{i}-{a}-{n}" for i, a, n in CASES])
def test_ac8_case(project, tmp_path, monkeypatch, i, arm, name):  # noqa: F811
    reasons: list[str] = []
    real: Any = base_fixture_names.probe

    def record(*args: Any, **kwargs: Any):
        found = real(*args, **kwargs)
        reasons.append(found[1])
        return found

    if arm == "on":
        monkeypatch.setattr(base_fixture_names, "probe", record)
    else:
        monkeypatch.setattr(base_fixture_names, "collect", lambda *a, **k: None)
    started = time.perf_counter()
    result = _run(project, tmp_path, SCENARIOS[name])
    seconds = time.perf_counter() - started
    row = {
        "i": i,
        "arm": arm,
        "scenario": name,
        "seconds": round(seconds, 3),
        "status": result.status,
        "refused_conftest": "TEST_COMMIT_CONFTEST_STATIC" in (result.reason or ""),
        "uncomparable": result.uncomparable,
        "probe": reasons[0] if reasons else None,
    }
    out = os.environ.get("CW986_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
