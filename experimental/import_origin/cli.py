"""python -m experimental.import_origin --input shadow-input.json --out DIR"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import sysconfig
from pathlib import Path

from experimental.import_origin.runner import Run, run_pytest
from experimental.second_observer.cli import check_output
from experimental.second_observer.inputs import ShadowInput, Uncomparable, load
from experimental.second_observer.rebuild import rebuild

FLAG = "PROTECTED_NAME_FROM_CANDIDATE"
PROBES = {f"P{i}": f"p{i}_probe_target" for i in range(1, 5)}


def _raw_repo(path: Path) -> list[Path]:
    try:
        repo = json.loads(path.read_text()).get("repo")
    except (OSError, ValueError, AttributeError):
        return []
    return [Path(repo)] if isinstance(repo, str) and repo else []


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _module_names(checkout: Path, roots: tuple[str, ...]) -> set[str]:
    names = {"conftest"}
    for rel in roots:
        root = checkout / rel
        if not root.is_dir():
            continue
        for file in root.rglob("*.py"):
            parts = file.relative_to(root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.add(".".join(parts))
                names.add(parts[-1])
    return names


def _classify(origin: str | None, module: str, checkout: Path, inp: ShadowInput) -> str:
    if origin in {"built-in", "frozen"}:
        return "stdlib" if module in sys.stdlib_module_names else "other"
    if not origin:
        return "unknown"  # an import event without a filename says nothing about the source
    path = Path(origin)
    if any(_under(path, checkout / root) for root in inp.test_roots):
        return "test-tree"
    if _under(path, checkout):
        return "candidate-tree"
    paths = sysconfig.get_paths()
    if any(_under(path, Path(paths[key])) for key in ("stdlib", "platstdlib")):
        return "stdlib"
    if any(_under(path, Path(paths[key])) for key in ("purelib", "platlib")):
        return "site-packages"
    return "other"


def classify(run: Run, inp: ShadowInput, checkout: Path) -> dict:
    support = _module_names(checkout, inp.test_roots)
    protected = set(sys.stdlib_module_names) | support
    events, flagged = [], []
    for event in run.events:
        name, origin = event["module"], event.get("origin")
        classification = _classify(origin, name, checkout, inp)
        item = {
            "module": name,
            "origin": origin,
            "classification": classification,
            "source": event.get("source", "audit"),
        }
        if name in protected:
            events.append(item)
            if classification == "candidate-tree":
                flagged.append(item)
    return {
        "flags": [FLAG] if flagged else [],
        "events": events,
        "flagged_events": flagged,
        "probes": {
            probe: any(event["module"] == name for event in run.events)
            for probe, name in PROBES.items()
        },
        "capture": run.capture,
        "returncode": run.returncode,
        "same_process": True,
    }


def observe(inp: ShadowInput, out: Path) -> dict:
    work = out / "work"
    try:
        checkout = rebuild(inp, out, work)
        return classify(run_pytest(inp, checkout, out), inp, checkout)
    except Uncomparable as exc:
        return {
            "detail": str(exc),
            "capture": "complete",
            "events": [],
            "flagged_events": [],
            "flags": [],
            "probes": dict.fromkeys(PROBES, False),
            "returncode": None,
            "same_process": True,
        }
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m experimental.import_origin")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--protect", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    guards = [*args.protect, *_raw_repo(args.input), args.input]
    try:
        check_output(args.out, guards)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        inp = load(args.input)
        detail = ""
    except Uncomparable as exc:
        inp, detail = None, str(exc)
    args.out.mkdir(mode=0o700)
    args.out = args.out.resolve()
    row = (
        observe(inp, args.out)
        if inp is not None
        else {
            "detail": detail,
            "capture": "complete",
            "events": [],
            "flagged_events": [],
            "flags": [],
            "probes": dict.fromkeys(PROBES, False),
            "returncode": None,
            "same_process": True,
        }
    )
    row = {"v": 1, "target": f"{inp.file}::{inp.name}" if inp is not None else None, **row}
    (args.out / "import_origin.jsonl").write_text(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True))
    return 0
