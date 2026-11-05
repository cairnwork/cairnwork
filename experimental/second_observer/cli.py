"""``python -m experimental.second_observer --input shadow-input.json --out DIR [--protect P]...``

Writes one JSON line per target to ``DIR/shadow.jsonl``. Everything it creates is inside DIR,
which must not exist yet and must not be, contain or lie inside the project repository or any
``--protect`` path (receipts, verdicts, the store). It never changes a Cairnwork verdict."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from cairnwork.gitcmd import run_git
from experimental.second_observer import NA, OBS_ERROR, UNCOMPARABLE
from experimental.second_observer.autouse import autouse_files, marked_na
from experimental.second_observer.inputs import ShadowInput, Uncomparable, load
from experimental.second_observer.observe import run_observer
from experimental.second_observer.rebuild import rebuild

PATH_REASON = "SHADOW_OUTPUT_PATH"


def _overlaps(a: Path, b: Path) -> bool:
    a, b = Path(os.path.realpath(a)), Path(os.path.realpath(b))
    return a == b or a in b.parents or b in a.parents


def check_output(out: Path, protected: list[Path]) -> None:
    """Refuse before anything is created. Raises ValueError carrying SHADOW_OUTPUT_PATH."""
    if out.exists() or out.is_symlink():
        raise ValueError(f"{PATH_REASON}: {out} already exists")
    if not out.parent.is_dir():
        raise ValueError(f"{PATH_REASON}: {out.parent} is not a directory")
    for path in protected:
        if _overlaps(out, path):
            raise ValueError(f"{PATH_REASON}: {out} overlaps protected path {path}")


def record(inp: ShadowInput, status: str, detail: str, started: float, extra: dict) -> dict:
    return {
        "v": 1,
        "variant": "A",
        "target": f"{inp.file}::{inp.name}",
        "candidate": inp.candidate,
        "cw_verdict": inp.cw_verdict,
        "cw_reason": inp.cw_reason,
        "observer": status,
        "detail": detail,
        "rule_t": None,  # rule T is step 2
        "elapsed_s": round(time.monotonic() - started, 3),
        "same_process": True,
        **extra,
    }


def static_na(inp: ShadowInput, checkout: Path, out: Path) -> dict:
    """Why a direct call is not comparable (no run): parametrized node or autouse fixture."""
    if inp.na_reason:
        return {"na_reason": inp.na_reason}
    if marked_na(checkout / inp.file, inp.name):
        return {"na_reason": "MARKS"}
    roots = [checkout / rel for rel in inp.pythonpath]
    files = autouse_files(checkout, inp.file, roots)
    if not files:
        return {}
    changed = run_git(out / "repo.git", "diff", "--name-only", "-z", inp.base, inp.candidate)
    return {
        "na_reason": "AUTOUSE_FIXTURE",
        "autouse_files": files,
        "autouse_in_candidate_change": bool(set(files) & set(changed.split("\0"))),
    }


def observe(inp: ShadowInput, out: Path) -> dict:
    started, extra = time.monotonic(), {}
    work = out / "work"
    try:
        checkout = rebuild(inp, out, work)
        gate = static_na(inp, checkout, out)
        if gate:
            return record(inp, NA, gate.pop("na_reason"), started, gate)
        seen = run_observer(inp, checkout, out)
        agrees = (
            None if seen.returncode is None else (seen.returncode == 0) == (seen.status == "PASS")
        )
        extra = {"sys_path": seen.sys_path, "env": dict(seen.env), "returncode": seen.returncode}
        extra["exit_agrees"] = agrees
        return record(inp, seen.status, seen.detail, started, extra)
    except Uncomparable as exc:
        return record(inp, UNCOMPARABLE, str(exc), started, extra)
    except Exception as exc:  # noqa: BLE001 - never a pass: the row says what failed
        return record(inp, OBS_ERROR, f"{type(exc).__name__}: {exc}"[:300], started, extra)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _raw_repo(path: Path) -> list[Path]:
    try:
        repo = json.loads(Path(path).read_text()).get("repo")
    except (OSError, ValueError, AttributeError):
        return []
    return [Path(repo)] if isinstance(repo, str) and repo else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cairnwork-observe", description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--protect", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    # the output path is checked first, with the repo named in the raw input even when the input
    # later turns out to be unusable: nothing is created or run before this passes
    guards = [*args.protect, *_raw_repo(args.input), args.input]
    try:
        check_output(args.out, guards)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        inp, reason = load(args.input), ""
    except Uncomparable as exc:  # nothing to rebuild: still one row
        inp, reason = None, str(exc)
    args.out.mkdir(mode=0o700)
    args.out = args.out.resolve()  # /var/folders is a symlink: the sandbox wants real paths
    if inp is None:
        row = {"v": 1, "variant": "A", "observer": UNCOMPARABLE, "detail": reason,
               "rule_t": None, "same_process": True}  # fmt: skip
    else:
        row = observe(inp, args.out)
    with (args.out / "shadow.jsonl").open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True))
    return 0
