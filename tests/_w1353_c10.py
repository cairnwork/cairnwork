"""Runner-sniffing residual probes for #1353 C10."""

from __future__ import annotations

import json
import runpy
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory

from _w1353_probe import row

import cairnwork.blackbox_judge as blackbox_judge
from cairnwork import blackbox
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.blackbox_runner import _RUNNER_SOURCE, MODULE


def _record(n, raw, outside, blackbox, BlackboxRun):

    run = BlackboxRun(
        json.dumps({"checks": {"add": {"file": "/co/calc.py", "cases": {"one": raw}}}}).encode(),
        0,
        False,
        0,
        (),
        (),
        Path("/tmp/run"),
        "/co",
        "p",
        True,
    )
    spec = blackbox.parse(
        json.dumps(
            {
                "salt": "ab" * 32,
                "checks": [
                    {
                        "id": "add",
                        "module": "calc",
                        "function": "add",
                        "cases": [{"id": "one", "args": [2, 3], "expected": 5}],
                    }
                ],
            }
        ).encode()
    )
    (verdict,) = blackbox_judge.judge_run(spec, "c" * 40, run)
    return row(
        "C10",
        f"runner-sniff-{n}",
        "python -P -m _cairnwork_blackbox_runner→blackbox_judge.judge_run",
        verdict.reason,
        "blackbox:add: PASS [matched 1/1]",
        "KNOWN_RESIDUAL",
        f"outside-runner return={outside}; production runs "
        "python -P -m _cairnwork_blackbox_runner "
        "(src/cairnwork/blackbox_exec.py:139), so the name is absent from sys.modules",
    )


def c10_rows():

    rows = []
    runner = types.ModuleType(MODULE)
    with TemporaryDirectory() as directory:
        runner_path = Path(directory) / "runner.py"
        runner_path.write_text(_RUNNER_SOURCE)
        runner_namespace = runpy.run_path(str(runner_path))
        runner_call = runner_namespace["_call"]
        sys.modules.update({"_cairnwork_blackbox_runner": runner})
        try:
            for n, condition in enumerate(
                (
                    f"{MODULE!r} in sys.modules",
                    "'pytest' not in sys.modules",
                    f"{MODULE!r} in sys.modules",
                ),
                1,
            ):
                saved = sys.modules.pop("pytest", None) if n == 2 else None
                saved_runner = sys.modules.pop(MODULE, None) if n == 3 else None
                candidate_path = Path(directory) / f"cand{n}.py"
                candidate_path.write_text(
                    "import sys\ndef add(a,b):\n if " + condition + ": return a+b\n return a-b\n"
                )
                ns = runpy.run_path(str(candidate_path))
                if n == 1:
                    sys.modules.pop(MODULE, None)
                    outside = ns["add"](2, 3)
                    raw = {"ok": outside}
                else:
                    raw = runner_call(ns["add"], {"args": [2, 3], "kwargs": {}})
                    sys.modules.pop(MODULE, None)
                    outside = ns["add"](2, 3)
                if saved is not None:
                    sys.modules.update({"pytest": saved})
                if saved_runner is not None:
                    sys.modules.update({"_cairnwork_blackbox_runner": saved_runner})
                else:
                    sys.modules.update({"_cairnwork_blackbox_runner": runner})
                rows.append(_record(n, raw, outside, blackbox, BlackboxRun))
        finally:
            sys.modules.pop(MODULE, None)
    return rows
