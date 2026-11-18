"""Runner-sniffing residual probes for #1353 C10."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pra1353_seal import read_module_report


def _record(n, raw, outside, blackbox, blackbox_judge, BlackboxRun):
    import json
    from pathlib import Path

    from pra1353_probe import row

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
    raw_return = _raw_return(verdict, outside)
    return row(
        "C10",
        f"runner-sniff-{n}",
        "python -P -m _cairnwork_blackbox_runner→blackbox_judge.judge_run",
        raw_return,
        "blackbox:add: PASS [matched 1/1]",
        "KNOWN_RESIDUAL",
        f"outside-runner return={outside}; production runs "
        "python -P -m _cairnwork_blackbox_runner "
        "(src/cairnwork/blackbox_exec.py:139), so the name is absent from sys.modules",
    )


def _raw_return(verdict, outside):
    return {"status": verdict.reason, "detail": "", "outside": outside}


def c10_rows():
    from cairnwork import blackbox, blackbox_judge
    from cairnwork.blackbox_exec import BlackboxRun

    rows = []
    source = _child_source()
    for n in range(1, 4):
        report_dir = Path(os.environ["PRA1353_CHILD_REPORT_DIR"])
        report = report_dir / f"c10-{n}-{os.getpid()}.json"
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                source,
                os.environ["PRA1353_IMPORT_ROOTS"],
                str(report),
                str(n),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        try:
            read_module_report(report)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        result = json.loads(done.stdout)
        rows.append(
            _record(n, result["raw"], result["outside"], blackbox, blackbox_judge, BlackboxRun)
        )
    return rows


def _child_source():
    return """import json,sys,types
roots=json.loads(sys.argv[1]); report=sys.argv[2]; sys.path[:]=roots
def run():
 from cairnwork.blackbox_runner import _RUNNER_SOURCE,MODULE
 n=int(sys.argv[3]); runner=types.ModuleType(MODULE); sys.modules[MODULE]=runner
 exec(compile(_RUNNER_SOURCE,MODULE,"exec"),runner.__dict__)
 if n != 2: import pytest
 condition=(f"{MODULE!r} in sys.modules" if n != 2 else "'pytest' not in sys.modules")
 ns={}; exec("import sys\\ndef add(a,b):\\n if "+condition+": return a+b\\n return a-b\\n",ns)
 if n == 1:
  sys.modules.pop(MODULE,None); outside=ns["add"](2,3); raw={"ok":outside}
 else:
  if n == 3: sys.modules.pop(MODULE,None)
  raw=runner._call(ns["add"],{"args":[2,3],"kwargs":{}})
  sys.modules.pop(MODULE,None); outside=ns["add"](2,3)
 print(json.dumps({"raw":raw,"outside":outside}))
try:
 run()
finally:
 modules=[{'name':n,'file':getattr(m,'__file__',None),'origin':getattr(getattr(m,'__spec__',None),'origin',None),'loader':(lambda x:x.__name__ if isinstance(x,type) else type(x).__name__)(getattr(getattr(m,'__spec__',None),'loader',None))} for n,m in sorted(sys.modules.items()) if isinstance(m,types.ModuleType)]
 open(report,'w').write(json.dumps({'modules':modules},sort_keys=True))
"""
