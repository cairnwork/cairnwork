"""Report changes to the trusted #1353 pin table and pinned helpers, and refuse tracked bytecode.

A guarded change is approved by the owner's PIN-APPROVE, or automatically when Claude, Codex and
Gemini each ACCEPT the exact head in a record posted by the reviewer login bound to that family
(not the PR author), no comment carries a negative verdict on that head, and the PR touches no
self-guard path (the guard, its lists, workflows, owners).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

FIXED_PATHS = {
    "tests/_w1353_allow_pins.py",
    "tests/test_1039_1353_witnesses.py",
    "tests/test_1039_1353_pin_completeness.py",
    "tests/pra1353_gate.py",
    "tests/pra1353_seal.py",
    ".github/workflows/ci.yml",
    ".github/workflows/pin-guard-trusted.yml",
    "scripts/ci/pin_guard.py",
    "scripts/ci/pin_guard_status.py",
}
APPROVAL = re.compile(r"^PIN-APPROVE ([0-9a-fA-F]{40})$")
BYTECODE_SUFFIXES = (".pyc", ".pyo")
# Changing any of these can widen who or what this guard trusts: owner PIN-APPROVE only.
SELF_GUARD_PREFIXES = (".github/", "scripts/ci/", "tests/test_pin_guard")
SELF_GUARD_PATHS = {"codeowners", "docs/codeowners", "tests/_w1353_allow_pins.py"}
FAMILIES = {
    "claude": re.compile(r"\b(?:claude|opus|sonnet)\b", re.IGNORECASE),
    "codex": re.compile(r"\b(?:codex|gpt)\b", re.IGNORECASE),
    "gemini": re.compile(r"\bgemini\b", re.IGNORECASE),
}
NEGATIVE = re.compile(
    r"\b(?:REJECT(?:ED)?|REQUEST_?CHANGES|REPAIR_?REQUIRED)\b|(?i:verdict)\s*:\s*(?i:reject)"
)
HEX = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])")
RECORD = re.compile(
    r"^\*\*(?P<label>[^*\n]{1,100})\*\* \((?P<who>[^()\n]{1,200})\): "
    r"\*\*(?P<verdict>ACCEPT|REJECT) at (?P<sha>[0-9a-f]{40})\*\*\.?$"
)


def _literal(node: ast.expr) -> object:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_literal(item) for item in node.elts]
    raise ValueError("unsupported pin table expression")


def _pin_table(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TRUSTED_HELPER_PINS"
            for target in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TRUSTED_HELPER_PINS"
        ):
            value = node.value
    if value is None:
        raise ValueError("pin table missing")
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
        if len(value.args) != 1 or not isinstance(value.args[0], ast.Call):
            raise ValueError("unsupported dict expression")
        zipped = value.args[0]
        if (
            not isinstance(zipped.func, ast.Name)
            or zipped.func.id != "zip"
            or len(zipped.args) != 2
        ):
            raise ValueError("unsupported zip expression")
        keys, hashes = _literal(zipped.args[0]), _literal(zipped.args[1])
        if not isinstance(keys, list) or not isinstance(hashes, list) or len(keys) != len(hashes):
            raise ValueError("invalid zipped table")
        entries = dict(zip(keys, hashes, strict=True))
    elif isinstance(value, ast.Dict):
        pairs = zip(value.keys, value.values, strict=True)
        decoded = [
            (_literal(key) if key is not None else None, _literal(item)) for key, item in pairs
        ]
        if any(not isinstance(key, str) for key, _ in decoded):
            raise ValueError("invalid pin table key")
        entries = {key: item for key, item in decoded}
    else:
        raise ValueError("unsupported pin table")
    if not entries:
        raise ValueError("empty or invalid pin table")
    pinned: set[str] = set()
    for key in entries:
        if not isinstance(key, str):
            raise ValueError("empty or invalid pin table")
        pinned.add(key)
    return pinned


def _changed(head: Path, base_sha: str, head_sha: str) -> set[str]:
    base = subprocess.run(
        ["git", "-C", str(head), "merge-base", "--end-of-options", base_sha, head_sha],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(head),
            "diff",
            "--name-only",
            "--no-renames",
            "--end-of-options",
            base,
            head_sha,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def _bytecode(head: Path, head_sha: str) -> list[str]:
    """Tracked bytecode in the head tree; Python may run it instead of the reviewed .py (#1465).

    A gitlink (submodule) is refused too: ls-tree -r does not list the files inside it.
    """
    listing = subprocess.run(
        ["git", "-C", str(head), "ls-tree", "-r", "-z", "--full-tree"]
        + ["--end-of-options", head_sha],
        check=True,
        capture_output=True,
    ).stdout
    hits = []
    for entry in filter(None, listing.split(b"\0")):
        meta, raw = entry.split(b"\t", 1)  # "<mode> <type> <object>\t<path>"; else rc 2
        name = raw.decode("utf-8", "surrogateescape")
        parts = name.casefold().split("/")
        gitlink = meta.split(b" ")[1] == b"commit"
        if gitlink or "__pycache__" in parts or parts[-1].endswith(BYTECODE_SUFFIXES):
            hits.append(name)
    return sorted(hits)


def _read_comments(comments: Path | None) -> list[tuple[object, str, bool]]:
    """(login, body, edited); an edited comment could have been rewritten after the fact."""
    if comments is None:
        return []
    data = json.loads(comments.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("comments must be a list")
    kept: list[tuple[object, str, bool]] = []
    for comment in data:
        user = comment.get("user") if isinstance(comment, dict) else None
        login = user.get("login") if isinstance(user, dict) else None
        body = comment.get("body", "") if isinstance(comment, dict) else ""
        if not isinstance(body, str):
            continue
        created, updated = comment.get("created_at"), comment.get("updated_at")
        edited = not isinstance(created, str) or not isinstance(updated, str) or created != updated
        kept.append((login, body, edited))
    return kept


def _approval(comments: Path | None, approvers: set[str], head_sha: str) -> str:
    stale = denied = False
    for login, body, edited in _read_comments(comments):
        if edited:
            continue
        for line in body.splitlines():
            match = APPROVAL.fullmatch(line)
            if not match:
                continue
            if not isinstance(login, str) or login.lower() not in approvers:
                denied = True
            elif match.group(1).lower() == head_sha.lower():
                return "PIN_CHANGE_APPROVED"
            else:
                stale = True
    if stale:
        return "PIN_APPROVAL_STALE"
    if denied:
        return "PIN_APPROVER_NOT_ALLOWED"
    return "PIN_CHANGE_UNAPPROVED"


def _identity(login: str) -> str:
    """One account seen as PR author ('app/x') or commenter ('x[bot]') compares equal."""
    return login.strip().lower().removeprefix("app/").removesuffix("[bot]")


def _negative(line: str, head: str) -> bool:
    """A negative verdict word on a line naming this head (any case, 7+ hex) or no sha at all."""
    if not NEGATIVE.search(line):
        return False
    shas = [sha.lower() for sha in HEX.findall(line)]
    return not shas or any(head.startswith(sha) for sha in shas)


def _three_family_accept(
    comments: Path | None, reviewers: dict[str, str], author: str | None, head_sha: str
) -> bool:
    """Each family ACCEPTs head_sha in an unedited record by the login bound to that family,
    which is not the PR author. Any negative verdict on head_sha, or with no sha, in any
    comment by anyone (edited or not) blocks: an unclear REJECT falls back to the owner."""
    if not author or not reviewers:
        return False
    head = head_sha.lower()
    accepted: set[str] = set()
    for login, body, edited in _read_comments(comments):
        if any(_negative(line, head) for line in body.splitlines()):
            return False
        family = reviewers.get(login.lower()) if isinstance(login, str) else None
        if edited or family is None or _identity(str(login)) == _identity(author):
            continue
        fenced = False
        for line in body.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            match = RECORD.fullmatch(line)
            if not match or fenced or match.group("sha") != head:
                continue
            # One verdict per comment, outside code fences, naming the login's own family.
            text = f"{match.group('label')} {match.group('who')}"
            if match.group("verdict") == "ACCEPT" and FAMILIES[family].search(text):
                accepted.add(family)
            break
    return accepted == set(FAMILIES)


def _self_guard(changed: set[str]) -> list[str]:
    hits = []
    for path in changed:
        folded = path.casefold()
        if folded in SELF_GUARD_PATHS or folded.startswith(SELF_GUARD_PREFIXES):
            hits.append(path)
    return sorted(hits)


def _logins(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _reviewers(path: Path) -> dict[str, str]:
    """'<login> <family>' per line; one family per account, each account once, else fail closed."""
    bound: dict[str, str] = {}
    seen: set[str] = set()
    for entry in _logins(path):
        parts = entry.split()
        if len(parts) != 2 or parts[1] not in FAMILIES or _identity(parts[0]) in seen:
            raise ValueError("invalid reviewer allowlist")
        seen.add(_identity(parts[0]))
        bound[parts[0]] = parts[1]
    return bound


def evaluate(args: argparse.Namespace) -> tuple[int, str, list[str]]:
    trusted = Path(args.trusted)
    if not trusted.is_dir():
        raise ValueError("trusted tree is not a directory")
    bytecode = _bytecode(Path(args.head), args.head_sha)
    if bytecode:  # no approval can allow it; json.dumps keeps each name on one log line
        return 3, "BYTECODE_TRACKED", [json.dumps(name) for name in bytecode]
    pin_file = trusted / "tests/_w1353_allow_pins.py"
    guarded = set(FIXED_PATHS)
    if pin_file.exists():
        guarded.update(_pin_table(pin_file))
    changed = _changed(Path(args.head), args.base_sha, args.head_sha)
    hits = sorted(guarded & changed)
    if not hits:
        return 0, "PIN_GUARD_OK", hits
    approvers = _logins(trusted / "scripts/ci/pin_guard_approvers.txt")
    comments = Path(args.comments) if args.comments else None
    outcome = _approval(comments, approvers, args.head_sha)
    if outcome == "PIN_CHANGE_APPROVED":
        return 0, outcome, hits
    reviewers = _reviewers(trusted / "scripts/ci/pin_guard_reviewers.txt")
    if not _self_guard(changed) and _three_family_accept(
        comments, reviewers, args.pr_author, args.head_sha
    ):
        return 4, "PIN_CHANGE_AUTO_APPROVED", hits
    return 1, outcome, hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--comments")
    parser.add_argument("--pr-author")
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", args.base_sha) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", args.head_sha
        ):
            raise ValueError("invalid commit SHA")
        code, result, paths = evaluate(args)
    except Exception:  # Fail closed for every unexpected parsing, I/O, or git error.  # noqa: BLE001
        code, result, paths = 2, "PIN_GUARD_INCOMPLETE", []
    suffix = f" {','.join(paths)}" if paths else ""
    print(f"PIN_GUARD {result}{suffix}")
    return code


if __name__ == "__main__":
    sys.exit(main())
