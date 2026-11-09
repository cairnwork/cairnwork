"""python -m experimental.import_origin --input shadow-input.json --out DIR"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cairnwork.gitcmd import OID, run_git
from cairnwork.pycache_guard import PYCACHE_DIR
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
            parts = file.relative_to(checkout).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.add(".".join(parts))
    return names


def _changed_candidate_paths(inp: ShadowInput, out: Path) -> set[str]:
    if not OID.fullmatch(inp.base) or not OID.fullmatch(inp.candidate):
        raise Uncomparable("base and candidate must be 40-hex ids")
    changed = run_git(
        out / "repo.git",
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        inp.base,
        inp.candidate,
        "--",
    )
    return {path for path in changed.split("\0") if path}


def _candidate_file(path: Path, checkout: Path, changed: set[str]) -> bool:
    try:
        relative = path.resolve().relative_to(checkout.resolve()).as_posix()
    except ValueError:
        return False
    return relative in changed


def source_of(origin: str, out: Path) -> Path:
    """The source file a cache file stands for. The run sets PYTHONPYCACHEPREFIX to
    ``out/run/tmp/<PYCACHE_DIR>``, which mirrors each source directory's absolute path, so a
    stdlib ``.pyc`` read from there must be judged by its source location, not the prefix."""
    path = Path(os.path.realpath(origin))
    prefix = Path(os.path.realpath(out / "run" / "tmp" / PYCACHE_DIR))
    if _under(path, prefix):
        path = Path("/") / path.relative_to(prefix)
    elif path.parent.name == "__pycache__":
        path = path.parent.parent / path.name
    if path.suffix == ".pyc":
        path = path.with_name(path.name.split(".", 1)[0] + ".py")
    return path


def interpreter_paths(interpreter: str) -> dict[str, str]:
    """The install paths of the interpreter the verifier ran, as that interpreter reports them.
    The observer's own ``sysconfig`` names a different venv (and maybe another Python), so its
    stdlib and site-packages would turn every honest import of the run into ``other``."""
    code = "import json, sysconfig; print(json.dumps(sysconfig.get_paths()))"
    done = subprocess.run(  # noqa: S603 - the verifier's own interpreter path, no shell
        [interpreter, "-I", "-c", code],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    paths = json.loads(done.stdout)
    if not isinstance(paths, dict) or not all(isinstance(v, str) for v in paths.values()):
        raise Uncomparable("the verifier interpreter did not report its install paths")
    return paths


def _classify(
    origin: str | None,
    module: str,
    checkout: Path,
    inp: ShadowInput,
    changed: set[str],
    out: Path,
    paths: dict[str, str],
) -> str:
    if origin in {"built-in", "frozen"}:
        return "stdlib" if module in sys.stdlib_module_names else "other"
    if not origin:
        return "unknown"  # an import event without a filename says nothing about the source
    path = source_of(origin, out)
    if any(_under(path, checkout / root) for root in inp.test_roots):
        return "test-tree"
    if any(key in paths and _under(path, Path(paths[key])) for key in ("purelib", "platlib")):
        return "site-packages"
    if any(key in paths and _under(path, Path(paths[key])) for key in ("stdlib", "platstdlib")):
        return "stdlib"
    if _under(path, checkout):
        # A checkout file the candidate did not change was already in base: not its doing.
        return "candidate-tree" if _candidate_file(path, checkout, changed) else "checkout-base"
    return "other"


def _names_agree(item: dict, out: Path) -> bool:
    """A row counts only when its module name and its file agree. An ``open`` row is named by the
    latest import event, which nested imports make unreliable (``pkg/__init__.py`` read while
    ``_ast_unparse`` was the latest event), so the file's own module path must end with the
    name; a path that does not exist (an interpreter probe) never counts."""
    origin = item["origin"]
    if item["source"] != "open" or origin in (None, "built-in", "frozen"):
        return True
    path = source_of(origin, out)
    if not path.exists():
        return False
    parts = [*path.parent.parts, path.stem] if path.stem != "__init__" else [*path.parent.parts]
    dotted = item["module"].split(".")
    return parts[-len(dotted) :] == dotted


def classify(run: Run, inp: ShadowInput, checkout: Path, out: Path) -> dict:
    support = _module_names(checkout, inp.test_roots)
    changed = _changed_candidate_paths(inp, out)
    paths = interpreter_paths(inp.interpreter)
    events, flagged = [], []
    for event in run.events:
        name, origin = event["module"], event.get("origin")
        classification = _classify(origin, name, checkout, inp, changed, out, paths)
        item = {
            "module": name,
            "origin": origin,
            "classification": classification,
            "source": event.get("source", "audit"),
        }
        stdlib_name = name.split(".", 1)[0] in sys.stdlib_module_names
        support_name = name in support
        if stdlib_name or support_name:
            events.append(item)
            if _names_agree(item, out) and (
                (stdlib_name and classification in {"candidate-tree", "other"})
                or (support_name and classification == "candidate-tree")
            ):
                flagged.append(item)
    return {
        "flags": [FLAG] if flagged else [],
        "events": events,
        "flagged_events": flagged,
        "probes": {
            probe: any(
                event["module"] == name
                or (
                    event.get("source") == "open"
                    and isinstance(event.get("origin"), str)
                    and source_of(event["origin"], out).stem == name
                )
                for event in run.events
            )
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
        return classify(run_pytest(inp, checkout, out), inp, checkout, out)
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
