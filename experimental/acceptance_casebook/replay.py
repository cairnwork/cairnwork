"""Injected-runner acceptance replay; the real archive/venv runner is step 3b."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from experimental.acceptance_casebook.identity import (
    IdentityError,
    confirmation_problem,
    resolve_actor,
)
from experimental.acceptance_casebook.origin import read_origin
from experimental.acceptance_casebook.store import (
    CasebookError,
    _append,
    _read_chain,
    list_cases,
    locked,
)


def RUNNER(*_: Any) -> Any:
    """Safe default: absence of a real 3b runner is an explicit failed run."""
    return {"exit_code": 1, "verdict": None}


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_ZERO = "0" * 64


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve() or (
            left.exists() and right.exists() and os.path.samefile(left, right)
        )
    except OSError:
        return False


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CasebookError("REPLAY_INPUT_INVALID", str(exc)) from exc


def _version(version: str, registry: Any, clone: Path) -> str:
    if not _SHA.fullmatch(version) or not isinstance(registry, dict) or version not in registry:
        raise CasebookError("UNKNOWN_ACCEPTANCE_VERSION")
    item = registry[version]
    if not isinstance(item, dict) or not _SHA.fullmatch(str(item.get("tree", ""))):
        raise CasebookError("UNKNOWN_ACCEPTANCE_VERSION")
    try:
        tree = subprocess.check_output(
            [
                "git",
                "-C",
                str(clone),
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{version}^{{tree}}",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CasebookError("UNKNOWN_ACCEPTANCE_VERSION") from exc
    if tree != item["tree"]:
        raise CasebookError("UNKNOWN_ACCEPTANCE_VERSION")
    return tree


def _confirmation(casebook: Path, case: dict[str, Any]) -> str:
    entries, _ = _read_chain(casebook)
    for entry, digest_value in entries:
        if entry.get("type") == "confirmation" and entry.get("case") == case["line_sha256"]:
            if entry.get("conclusion") == "confirmed" and confirmation_problem(case, entry) is None:
                return digest_value
    raise CasebookError("CONFIRMATION_MISSING")


def _run_one(
    case: dict[str, Any], args: dict[str, Any], repeat: int
) -> tuple[str, str, int, str | None]:
    try:
        value = RUNNER(case, args)
    except (TimeoutError, subprocess.TimeoutExpired):
        return "UNREPLAYABLE", "TIMEOUT", 124, None
    except Exception:  # noqa: BLE001 - injected runner failures must become UNREPLAYABLE
        return "UNREPLAYABLE", "RUNNER_ERROR", 1, None
    if isinstance(value, str) and value in ("accepted", "refused"):
        return value, "", 0, value
    if not isinstance(value, dict):
        return "UNREPLAYABLE", "UNREADABLE_OUTPUT", 1, None
    code, verdict = value.get("exit_code"), value.get("verdict")
    if type(code) is not int:  # bool, float, str or None is never a real exit code
        return "UNREPLAYABLE", "UNREADABLE_OUTPUT", 1, None
    if verdict == "VERIFIED" and code == 0:
        mapped = "accepted"
    elif verdict == "FAILED" and code == 0:
        mapped = "refused"
    elif verdict == "REFUSED":
        return "UNREPLAYABLE", "PRE_CORE_REFUSED", int(code or 1), None
    else:
        return "UNREPLAYABLE", "UNREADABLE_OUTPUT", int(code or 1), None
    output = str(value.get("output", verdict))
    return mapped, "", int(code), output


def _bound_inputs(case: dict[str, Any], inputs: Any, request: dict[str, Any]) -> dict[str, Any]:
    raw = inputs.get(case["line_sha256"]) if isinstance(inputs, dict) else None
    if not isinstance(raw, dict):
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    if set(raw) != {"allowed_file", "verifier"}:
        raise CasebookError("REPLAY_INPUT_MISMATCH")
    for key in ("base", "candidate"):
        value = case.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise CasebookError("REPLAY_INPUT_MISMATCH")
    contract = request.get("acceptance_contract", {})
    plan = request.get("verification_plan", {})
    bindings = {
        "allowed_paths": raw.get("allowed_file"),
        "required_verifier_ids": raw.get("verifier"),
    }
    expected = {
        "allowed_paths": contract.get("allowed_paths", request.get("allowed_paths")),
        "required_verifier_ids": plan.get(
            "required_verifier_ids",
            contract.get("required_verifier_ids", request.get("required_verifier_ids")),
        ),
    }
    for key, supplied in bindings.items():
        if not isinstance(supplied, list) or not all(isinstance(v, str) for v in supplied):
            raise CasebookError("REPLAY_INPUT_MISMATCH")
        if expected[key] is None or sorted(supplied) != sorted(expected[key]):
            raise CasebookError("REPLAY_INPUT_MISMATCH")
    return {**raw, "base": case["base"], "candidate": case["candidate"]}


def _append_record(out: Path, record: dict[str, Any]) -> None:
    def action() -> None:
        entries, prev = _read_chain(out)
        _append(out, record, prev)

    locked(out, action)


def _case_result(
    case: dict[str, Any],
    raw: dict[str, Any],
    request: dict[str, Any],
    input_digest: str,
    version: str,
    tree: str,
    head: str,
    confirmation: str,
    replayer: dict[str, Any],
    repeat: int,
    run_id: str,
) -> dict[str, Any]:
    start = datetime.now(UTC).isoformat()
    mapped, reasons, codes, hashes = [], [], [], []
    for index in range(repeat):
        result, reason, code, output = _run_one(
            case,
            {**raw, "request": request},
            index,
        )
        mapped.append(result)
        reasons.append(reason or None)
        codes.append(code)
        hashes.append(_digest(output.encode()) if output is not None else None)
    unique = set(mapped)
    reason = next((r for r in reasons if r), None)
    if len(unique) > 1:
        outcome, reason = "UNREPLAYABLE", "NONDETERMINISTIC"
    elif "UNREPLAYABLE" in unique:
        outcome = "UNREPLAYABLE"
    else:
        same = (case["kind"], mapped[0]) in (
            ("accepted_bad", "accepted"),
            ("refused_good", "refused"),
        )
        outcome, reason = ("same_mistake" if same else "corrected"), None
    return {
        "type": "replay_result",
        "run": run_id,
        "case": case["line_sha256"],
        "kind": case["kind"],
        "confirmation": confirmation,
        "version": version,
        "version_tree": tree,
        "casebook_head": head,
        "origin_row_sha256": case["origin"]["row_sha256"],
        "inputs_sha256": input_digest,
        "argv": raw,
        "exit_codes": codes,
        "verify_output_sha256": hashes,
        "mapped": mapped,
        "outcome": outcome,
        "reason": reason,
        "started_at": start,
        "finished_at": datetime.now(UTC).isoformat(),
        "replayer": replayer,
    }


def _identity(args: argparse.Namespace) -> dict[str, Any]:
    opts = {
        k: getattr(args, k)
        for k in (
            "lineage",
            "model_family",
            "dispatch_id",
            "github_login",
            "source_ref",
            "source_file",
            "display_name",
        )
    }
    return resolve_actor({}, opts)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acceptance_casebook replay")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("replay")
    for name in ("casebook", "notary_db", "versions", "inputs", "cairnwork_clone", "out"):
        r.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--repeat", type=int, default=2)
    for name in (
        "lineage",
        "model-family",
        "dispatch-id",
        "github-login",
        "source-ref",
        "source-file",
        "display-name",
    ):
        r.add_argument("--" + name)
    report = sub.add_parser("replay-report")
    report.add_argument("--out", type=Path, required=True)
    return p


def _case_record(
    case: dict[str, Any],
    inputs: Any,
    input_digest: str,
    tree: str,
    args: argparse.Namespace,
    head: str,
    replayer: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    try:
        rec, _ = read_origin(args.notary_db, case["task_id"], case["attempt_id"])
        confirmation = _confirmation(args.casebook, case)
        raw = _bound_inputs(case, inputs, rec.request)
        result = _case_result(
            case,
            raw,
            rec.request,
            input_digest,
            args.version,
            tree,
            head,
            confirmation,
            replayer,
            args.repeat,
            run_id,
        )
        result["confirmation"] = confirmation
    except Exception as exc:  # noqa: BLE001 - one case failure must not abort the run
        code = exc.code if isinstance(exc, CasebookError) else "UNREPLAYABLE_CASE"
        result = {
            "type": "replay_result",
            "run": run_id,
            "case": case["line_sha256"],
            "kind": case["kind"],
            "outcome": "UNREPLAYABLE",
            "reason": code,
            "mapped": [],
            "exit_codes": [],
            "verify_output_sha256": [],
            "version": args.version,
            "version_tree": tree,
            "casebook_head": head,
            "origin_row_sha256": case["origin"]["row_sha256"],
            "inputs_sha256": input_digest,
            "argv": {},
            "replayer": replayer,
        }
    if case["kind"] not in ("accepted_bad", "refused_good"):
        result["outcome"], result["reason"] = "UNREPLAYABLE", "INVALID_KIND"
    return result


def _replay(args: argparse.Namespace) -> int:
    if args.repeat < 1:
        raise CasebookError("REPLAY_INPUT_INVALID")
    run_id = _digest(f"{datetime.now(UTC).isoformat()}:{args.version}".encode())
    tree = _version(args.version, _json(args.versions), args.cairnwork_clone)
    replayer = _identity(args)
    inputs_bytes = args.inputs.read_bytes()
    inputs = json.loads(inputs_bytes)
    input_digest = _digest(inputs_bytes)
    book = args.casebook
    db = args.notary_db
    protected = [book.with_name(book.name + s) for s in ("", ".head", ".lock")]
    protected += [db.with_name(db.name + s) for s in ("", "-wal", "-shm", "-journal")]
    outputs = [args.out.with_name(args.out.name + s) for s in ("", ".head", ".lock")]
    if any(_same_path(output, path) for output in outputs for path in protected):
        raise CasebookError("REPLAY_OUTPUT_REFUSED")

    head, all_cases = locked(book, lambda: (_read_chain(book)[1], list_cases(book, args.notary_db)))
    eligible = [case for case in all_cases if case["state"] == "confirmed"]
    excluded = {
        state: sum(c["state"] == state for c in all_cases)
        for state in ("unconfirmed", "rejected", "origin_changed")
    }
    counts: dict[str, dict[str, int]] = {}
    for case in eligible:
        result = _case_record(case, inputs, input_digest, tree, args, head, replayer, run_id)
        _append_record(args.out, result)
        bucket = counts.setdefault(
            case["kind"], {"same_mistake": 0, "corrected": 0, "unreplayable": 0}
        )
        key = "unreplayable" if result["outcome"] == "UNREPLAYABLE" else result["outcome"]
        bucket[key] += 1
    bad = sum(v["unreplayable"] for v in counts.values())
    summary = {
        "type": "replay_summary",
        "run": run_id,
        "version": args.version,
        "eligible": len(eligible),
        "excluded": excluded,
        "counts": counts,
        "complete": bool(eligible) and bad == 0,
    }
    _append_record(args.out, summary)
    if not eligible:
        print("NO_ELIGIBLE_CASES")
    return 3 if not eligible or bad else 0


def report(path: Path) -> str:
    try:
        entries, _ = _read_chain(path)
    except CasebookError:
        return "REPLAY_NOT_APPEND_ONLY"
    completed = {entry.get("run") for entry, _ in entries if entry.get("type") == "replay_summary"}
    incomplete = {
        entry.get("run")
        for entry, _ in entries
        if entry.get("type") == "replay_result" and entry.get("run") not in completed
    }
    if incomplete or not entries:
        return "INCOMPLETE_RUN"
    return "OK"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "replay-report":
            result = report(args.out)
            print(result)
            return 0 if result == "OK" else 2
        return _replay(args)
    except CasebookError as exc:
        print(exc.code)
        return 2
    except IdentityError as exc:
        print(exc.code)
        return 2
    except Exception:  # noqa: BLE001 - never print an untrusted exception message
        print("REPLAY_ERROR")
        return 2
