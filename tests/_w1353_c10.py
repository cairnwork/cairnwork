"""Runner-sniffing residual probes for #1353 C10."""

from __future__ import annotations

import sys


def _record(n, raw, outside, blackbox, blackbox_judge, BlackboxRun):
    import json
    from pathlib import Path

    from _w1353_probe import row

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
    import types

    from cairnwork import blackbox, blackbox_judge
    from cairnwork.blackbox_exec import BlackboxRun
    from cairnwork.blackbox_runner import _RUNNER_SOURCE, MODULE

    rows = []
    runner = types.ModuleType(MODULE)
    sys.modules[MODULE] = runner
    exec(compile(_RUNNER_SOURCE, MODULE, "exec"), runner.__dict__)
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
            ns = {}
            exec(
                compile(
                    "import sys\ndef add(a,b):\n if " + condition + ": return a+b\n return a-b\n",
                    f"cand{n}",
                    "exec",
                ),
                ns,
            )
            if n == 1:
                sys.modules.pop(MODULE, None)
                outside = ns["add"](2, 3)
                raw = {"ok": outside}
            else:
                raw = runner._call(ns["add"], {"args": [2, 3], "kwargs": {}})
                sys.modules.pop(MODULE, None)
                outside = ns["add"](2, 3)
            if saved is not None:
                sys.modules["pytest"] = saved
            if saved_runner is not None:
                sys.modules[MODULE] = saved_runner
            else:
                sys.modules[MODULE] = runner
            rows.append(_record(n, raw, outside, blackbox, blackbox_judge, BlackboxRun))
    finally:
        sys.modules.pop(MODULE, None)
    return rows
