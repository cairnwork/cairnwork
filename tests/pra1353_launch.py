"""Route launcher for #1353: the only module that imports cairnwork, one fresh child per call.

Witness and runner processes call product routes through ``launch_route`` and never import
``cairnwork*`` themselves, so product code cannot rebind witness, derive or runner state.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pra1353_seal import read_module_report

# One route call is a few imports plus at most a sandbox-free judge; the receipt group builds
# keys, bundles and git trees, and measured well under a minute. 120 s is 2x the slowest.
DEFAULT_TIMEOUT = 120
_COUNTER = itertools.count()

# The sealed import roots reach the no-site child; its module-origin report is written in finally.
_BOOT = (
    "import json,sys,types; roots=json.loads(sys.argv.pop(1)); report=sys.argv.pop(1); "
    "sys.path[:]=roots\n"
    "try:\n import pra1353_launch; pra1353_launch.child_main()\n"
    "finally:\n "
    "modules=[{'name':n,'file':getattr(m,'__file__',None),'origin':getattr(getattr(m,'__spec__',None),'origin',None),'loader':(lambda x:x.__name__ if isinstance(x,type) else type(x).__name__)(getattr(getattr(m,'__spec__',None),'loader',None))} for n,m in sorted(sys.modules.items()) if isinstance(m,types.ModuleType) and type(m).__name__!='Lib']; "  # noqa: E501
    "open(report,'w').write(json.dumps({'modules':modules},sort_keys=True))"
)


def launch_route(
    route: str, args: dict[str, Any], *, timeout: int = DEFAULT_TIMEOUT, boot: str = _BOOT
) -> dict[str, Any]:
    """Run one route in a fresh child; any deviation raises and never yields a row."""
    report = Path(os.environ["PRA1353_CHILD_REPORT_DIR"]) / (
        f"route-{route}-{os.getpid()}-{next(_COUNTER)}.json"
    )
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        boot,
        os.environ["PRA1353_IMPORT_ROOTS"],
        str(report),
    ]
    try:
        done = subprocess.run(
            command,
            input=json.dumps({"route": route, "args": args}),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"route {route} timed out after {timeout} s") from exc
    if done.returncode != 0:
        raise RuntimeError(f"route {route} exited {done.returncode}: {done.stderr[-400:]}")
    lines = done.stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise RuntimeError(f"route {route} did not print exactly one JSON line")
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"route {route} output was not JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"route {route} output was not a JSON object")
    try:
        read_module_report(report)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return result


def judge(spec: str | Path, raw: str, changes: dict[str, Any] | None = None) -> str:
    """Return the blackbox_judge reason for a declaration (JSON text or file path) and report."""
    args: dict[str, Any] = {"raw": raw, "changes": changes or {}}
    args["spec_path" if isinstance(spec, Path) else "spec_json"] = str(spec)
    return launch_route("judge", args)["reason"]


def child_main() -> None:
    request = json.loads(sys.stdin.read())
    handler = _ROUTES[request["route"]]
    print(json.dumps(handler(request["args"]), default=str))


def _judge(args: dict[str, Any]) -> dict[str, Any]:
    from dataclasses import replace

    from cairnwork import blackbox, blackbox_judge
    from cairnwork.blackbox_exec import BlackboxRun

    run = BlackboxRun(args["raw"].encode(), 0, False, 0, (), (), Path("/tmp/run"), "/co", "p", True)
    if "spec_path" in args:
        spec = blackbox.load(Path(args["spec_path"]))
    else:
        spec = blackbox.parse(args["spec_json"].encode())
    (verdict,) = blackbox_judge.judge_run(spec, "c" * 40, replace(run, **args["changes"]))
    return {"reason": verdict.reason}


def _c7_unprotected(args: dict[str, Any]) -> dict[str, Any]:
    from types import SimpleNamespace

    from cairnwork import verify_cli
    from cairnwork.intake import Intake
    from cairnwork.pipeline import TaskRejected

    namespace = SimpleNamespace(
        require_protection=True,
        blackbox=None,
        verifier=(),
        repo_path=".",
        base="0" * 40,
        candidate="1" * 40,
        allowed_file=["src/calc.py"],
        test_commit="",
        import_path=[],
    )
    try:
        value = verify_cli._run(Intake(Path("/no-such-intake")), namespace)
    except TaskRejected as exc:
        value = exc.code
    return {"value": value}


def _c8_rows(args: dict[str, Any]) -> dict[str, Any]:
    tests = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "tests", tests / "__init__.py", submodule_search_locations=[str(tests)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("sealed tests package initializer is unavailable")
    package = importlib.util.module_from_spec(spec)
    sys.modules["tests"] = package
    spec.loader.exec_module(package)
    from pra1353_receipt import c8_rows

    return {"rows": c8_rows(Path(args["parent"]))}


def _c10_control(args: dict[str, Any]) -> dict[str, Any]:
    """C10 outside-runner control: never imports cairnwork, so product code cannot forge it."""
    module = "_cairnwork_blackbox_runner"  # same literal as pra1353_consume
    n = int(args["n"])
    condition = f"{module!r} in sys.modules" if n != 2 else "'pytest' not in sys.modules"
    ns: dict[str, Any] = {}
    exec("import sys\ndef add(a,b):\n if " + condition + ": return a+b\n return a-b\n", ns)
    names = [name for name in sys.modules if name.split(".")[0].startswith("cairnwork")]
    return {"outside": ns["add"](2, 3), "cairnwork_modules": names}


_ROUTES = {
    "judge": _judge,
    "c7_unprotected": _c7_unprotected,
    "c8_rows": _c8_rows,
    "c10_control": _c10_control,
}
