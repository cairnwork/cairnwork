"""Run ordinary and protected verification against the synthetic corpus."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

import cairnwork_sandbox
from cairnwork.verify_context import VerifyContext

from .attempts import run_protected_once
from .corpus import ENTRIES
from .once import OnceStore


def _safe(text: str, limit: int = 200) -> str:
    """Make untrusted diagnostic text safe for logs and machine-readable output."""
    cleaned = "".join(
        " " if unicodedata.category(char) in {"Zl", "Zp", "Cc"} else char
        for char in text
        if not unicodedata.category(char).startswith("C") or char in "\n\r\t"
    ).strip()
    if cleaned.startswith("::") or cleaned.startswith("##["):
        cleaned = "'" + cleaned
    return cleaned if len(cleaned) <= limit else cleaned[: max(0, limit - 1)] + "…"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=20
    ).stdout.strip()


def _candidate(root: Path, files: dict[str, str]) -> tuple[str, str]:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main", "--template=")
    _git(root, "config", "user.email", "measure@example.invalid")
    _git(root, "config", "user.name", "measurement")
    for name, source in files.items():
        if name.startswith("src/"):
            source = "# base implementation\n"
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "candidate")
    for name, source in files.items():
        if name.startswith("src/"):
            (root / name).write_text(source)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "candidate")
    return base, _git(root, "rev-parse", "HEAD")


def _ordinary(
    root: Path, state: Path, base: str, commit: str, task: str, python: str
) -> tuple[str, float]:
    module = task
    cmd = [
        python,
        "-I",
        "-B",
        "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));"
        "runpy.run_module('cairnwork.verify_cli',run_name='__main__',alter_sys=True)",
        str(Path(__import__("cairnwork").__file__).resolve().parent.parent),
        "--state",
        str(state),
        "run",
        "--repo-path",
        str(root),
        "--base",
        base,
        "--candidate",
        commit,
        "--allowed-file",
        f"src/{module}.py",
        "--verifier",
        "python3 -m pytest -q tests/test_visible.py",
        "--import-path",
        "src",
    ]
    start = time.perf_counter()
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    elapsed = time.perf_counter() - start
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        return "TOOL_ERROR", elapsed
    return str(result.get("core_status") or result.get("status", "UNKNOWN")), elapsed


def _classify(entry: dict[str, Any], status: str) -> str:
    if status == "TOOL_DEFECT":
        return status
    if status == "INCOMPLETE":
        if entry["kind"] == "tamper":
            return "tamper_blocked_incomplete"
        return "NOT_RUN/UNCOMPARABLE"
    if entry["kind"] == "honest":
        return "ok" if status == "PROTECTED_PASS" else "HONEST_BLOCKED"
    return "VERIFIED_WRONG" if status == "PROTECTED_PASS" else "tamper_blocked"


def _measure_one(
    entry: dict[str, Any], index: int, parent: Path, state: Path, account, backend, py: str
):
    row = {key: entry.get(key, False) for key in ("id", "task", "kind", "cls", "known_residual")}
    try:
        root = parent / f"repo-{index}"
        base, commit = _candidate(root, entry["files"])
        core, core_s = _ordinary(root, state, base, commit, entry["task"], sys.executable)
        ctx = VerifyContext(
            root,
            parent / "runs",
            account=account,
            python=py,
            base_commit=base,
            import_paths=("src",),
        )
        if backend is None or account is None:
            code = "NO_SANDBOX" if backend is None else "NEEDS_VERIFIER_ACCOUNT"
            status = "INCOMPLETE" if backend is None else "REFUSED"
            row.update(
                core_status=core,
                core_seconds=core_s,
                protected_status=status,
                protected_code=code,
                protected_seconds=0.0,
                classification="NOT_RUN/UNCOMPARABLE",
            )
            return row, None
        started = time.perf_counter()
        store = OnceStore(parent / f"once-{index}")
        attempts_store = OnceStore(parent / f"attempts-{index}")
        declaration = entry["declaration"]
        if entry.get("reuse_lock"):
            run_protected_once(
                declaration,
                commit,
                ctx=ctx,
                run_id=f"discard-{index}",
                store=store,
                attempts_store=attempts_store,
            )
        if entry.get("resalt"):
            from cairnwork.blackbox import Declaration

            declaration = Declaration("b2" * 32, declaration.checks)
        kwargs = {"timeout": entry["timeout"]} if entry.get("timeout") else {}
        verdict = run_protected_once(
            declaration,
            commit,
            ctx=ctx,
            run_id=f"measure-{index}",
            store=store,
            attempts_store=attempts_store,
            **kwargs,
        )
        protected_s = time.perf_counter() - started
        row.update(
            core_status=core,
            core_seconds=core_s,
            protected_status=verdict.status,
            protected_code=_safe(verdict.code),
            protected_checks=[_safe(line) for line in getattr(verdict, "checks", ())],
            protected_seconds=protected_s,
            classification=_classify(entry, verdict.status),
        )
        return row, protected_s - core_s
    except Exception as exc:  # noqa: BLE001 - measurement records tool defects per row
        row.update(
            core_status="UNKNOWN",
            protected_status="INCOMPLETE",
            protected_code="TOOL_DEFECT",
            classification="TOOL_DEFECT",
            exception_type=_safe(type(exc).__name__),
        )
        return row, None


def _summary(rows: list[dict[str, Any]], deltas: list[float], account, backend):
    eligible = [row for row in rows if not row["known_residual"]]
    honest = sum(row["kind"] == "honest" for row in eligible)
    blocked = sum(row["classification"] == "HONEST_BLOCKED" for row in eligible)
    values = sorted(deltas)
    not_run = sum(
        row["classification"] in {"NOT_RUN/UNCOMPARABLE", "tamper_blocked_incomplete"}
        for row in rows
    )
    outcomes: dict[str, dict[str, int]] = {}
    residual_outcomes: dict[str, int] = {}
    for row in rows:
        code = str(row.get("protected_code", "UNKNOWN"))
        if row["known_residual"]:
            residual_outcomes[code] = residual_outcomes.get(code, 0) + 1
        else:
            by_class = outcomes.setdefault(str(row["cls"] or "honest"), {})
            by_class[code] = by_class.get(code, 0) + 1
    return {
        "rows": rows,
        "n_honest": honest,
        "n_tamper": sum(row["kind"] == "tamper" for row in eligible),
        "per_class": {
            f"C{i}": sum(row["cls"] == f"C{i}" for row in eligible) for i in range(1, 10)
        },
        "per_class_outcomes": outcomes,
        "known_residual_outcomes": residual_outcomes,
        "verified_wrong": sum(row["classification"] == "VERIFIED_WRONG" for row in eligible),
        "honest_blocked": blocked,
        "honest_blocked_rate": blocked / honest if honest else None,
        "tool_defect": sum(row["classification"] == "TOOL_DEFECT" for row in rows),
        "not_run_plus_uncomparable": not_run,
        "not_run_rate": not_run / len(rows) if rows else None,
        "incremental_wall_median": statistics.median(values) if values else None,
        "incremental_wall_p90": values[min(len(values) - 1, int(0.9 * len(values)))]
        if values
        else None,
        "known_residual": [row["id"] for row in rows if row["known_residual"]],
        "host": {
            "platform": platform.platform(),
            "sandbox_kind": backend.name if backend else None,
            "account_available": account is not None,
        },
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "environment": "MISSING_ENV" if backend is None or account is None else "available",
    }


def measure(entries: list[dict[str, Any]], out: Path, *, account, python: str | None = None):
    backend = cairnwork_sandbox.select()
    rows, deltas = [], []
    with tempfile.TemporaryDirectory(prefix="protected-accept-") as temp:
        parent = Path(temp).resolve()
        state = parent / "state"
        state.mkdir(mode=0o700)
        (state / "config.json").write_text('{"verifier_account":false}')
        (state / "ACCEPTING").touch()
        # The account runs the candidate with its own interpreter: the owner's venv is not usable.
        interpreter = python or getattr(account, "python", None) or sys.executable
        for index, entry in enumerate(entries):
            row, delta = _measure_one(entry, index, parent, state, account, backend, interpreter)
            rows.append(row)
            if delta is not None:
                deltas.append(delta)
    result = _summary(rows, deltas, account, backend)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    selected = ENTRIES
    if args.only:
        wanted = set(args.only.split(","))
        selected = [e for e in selected if e["cls"] in wanted or e["kind"] in wanted]
    if args.limit is not None:
        selected = selected[: args.limit]
    from cairnwork.verifier_account import VerifierAccount

    verifier_account = VerifierAccount()
    with verifier_account.exclusive():
        available = verifier_account.available(verifier_account.python)
    account = verifier_account if available else None
    measure(selected, args.out, account=account)


if __name__ == "__main__":
    main()
