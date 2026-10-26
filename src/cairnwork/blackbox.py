"""Function-level blackbox checks (#303): answers the candidate never sees, compared by the owner.

A candidate's code runs in the pytest process that produces the verdict, so it can change what a
protected test reads (``sys.modules["helper"] = fake``) and turn a wrong fix green. A blackbox
check keeps the expected answers out of that process altogether: the owner declares, in a file
only the owner can read, a top-level function and cases (args, kwargs, expected). The candidate's
function runs in the sandbox under the verifier account (blackbox_exec.py) and is given the inputs
only; the owner reads what it returned and compares it with the answers itself. Each check becomes
one more Core observation (``blackbox:<id>``) and one more required verifier id, so a failed or
missing check keeps Core from VERIFIED; Core's definition is not changed.

What it protects, and what it does not. The output can be forged (the candidate shares the
runner's interpreter); the protection is that the candidate does not know the answers. So it holds
for answers that cannot be guessed, under a verifier account (another uid, which cannot read the
declaration). Answers that are all booleans or null can be guessed: the check still runs, and its
artifact id and reason say ``low-entropy``. Without an account the candidate runs as the owner and
could read the declaration: the check runs for the evidence but never passes
(``VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT``). Pytest tests that are not declared
here are not protected; a candidate that hard-codes the declared answers is the general limit of
any verification. Every run says which cases matched, so a declaration used again and again is an
oracle for whoever submits the candidates: replace it as deferred tests are replaced. Values are
JSON without floats (Core's canonical_json): ints, strings, booleans, null, lists and string-keyed
objects; anything else a function returns is ``UNSERIALIZABLE``.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cairnwork_protocol import canonical_hash, canonical_json

BAD = "BAD_BLACKBOX"
PREFIX = "blackbox:"
INCOMPLETE = "VERIFICATION_INCOMPLETE"
MAX_BYTES = 1 << 20
MAX_CASES = 100
_ID = re.compile(r"[a-z0-9_-]{1,40}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SALT = re.compile(r"[0-9a-f]{64}")
_GUESSABLE = ("true", "false", "null")


class BlackboxRejected(ValueError):
    code = BAD


@dataclass(frozen=True)
class Case:
    id: str
    args: str  # canonical JSON: frozen, so a TaskSpec holding it cannot be changed in place
    kwargs: str
    expected: str


@dataclass(frozen=True)
class Check:
    id: str
    module: str
    function: str
    cases: tuple[Case, ...]

    @property
    def verifier_id(self) -> str:
        return PREFIX + self.id

    @property
    def low_entropy(self) -> bool:
        return all(case.expected in _GUESSABLE for case in self.cases)


@dataclass(frozen=True)
class Declaration:
    salt: str
    checks: tuple[Check, ...]

    def commitment(self) -> str:
        """What request and binding carry (never the answers): a salted hash over all of it."""
        return canonical_hash(self._plain(with_expected=True) | {"salt": self.salt})

    def runner_input(self, paths: list[str], out: str) -> bytes:
        """The runner's input: everything but the answers and the salt."""
        plain = self._plain(with_expected=False) | {"paths": paths, "out": out}
        return canonical_json(plain).encode()

    def _plain(self, *, with_expected: bool) -> dict[str, Any]:
        def case(c: Case) -> dict[str, Any]:
            plain = {"id": c.id, "args": json.loads(c.args), "kwargs": json.loads(c.kwargs)}
            return plain | ({"expected": json.loads(c.expected)} if with_expected else {})

        return {
            "checks": [
                {"id": k.id, "module": k.module, "function": k.function,
                 "cases": [case(c) for c in k.cases]}
                for k in self.checks
            ]
        }  # fmt: skip


def load(path: Path) -> Declaration:
    """Read and check an owner's declaration file; BlackboxRejected when anything is off. It must
    be a regular file of this user with no group or other access: the answers in it are the whole
    protection, and the sandbox does not hide every readable file (bubblewrap binds / read-only)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise BlackboxRejected(f"cannot open the declaration: {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise BlackboxRejected("the declaration must be your own regular file, mode 0600")
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise BlackboxRejected(f"declaration over {MAX_BYTES} bytes")
    return parse(raw)


def parse(raw: bytes) -> Declaration:
    """Messages never quote the file: a rejected declaration still holds the owner's answers."""
    try:
        return _parse(raw)
    except RecursionError:
        raise BlackboxRejected("declaration nested too deeply") from None


def _parse(raw: bytes) -> Declaration:
    try:
        data = json.loads(raw, object_pairs_hook=_no_duplicates, parse_float=_no_float,
                          parse_constant=_no_float)  # fmt: skip
    except json.JSONDecodeError as exc:
        raise BlackboxRejected(f"not JSON (line {exc.lineno} column {exc.colno})") from None
    except (ValueError, UnicodeDecodeError):  # our hooks' fixed messages; decoding: none
        raise BlackboxRejected("not strict JSON: no duplicate keys, floats or NaN") from None
    _keys(data, {"salt", "checks"}, "declaration")
    if not isinstance(data["salt"], str) or not _SALT.fullmatch(data["salt"]):
        raise BlackboxRejected("salt must be 64 lowercase hex digits (32 random bytes)")
    checks = data["checks"]
    if not isinstance(checks, list) or not checks:
        raise BlackboxRejected("checks must be a non-empty list")
    parsed = tuple(_check(check) for check in checks)
    _unique([check.id for check in parsed], "check ids")
    return Declaration(data["salt"], parsed)


def _check(data: Any) -> Check:
    _keys(data, {"id", "module", "function", "cases"}, "check")
    module, function, cases = data["module"], data["function"], data["cases"]
    _match(_ID, data["id"], "check id")
    if not isinstance(module, str) or not all(_NAME.fullmatch(p) for p in module.split(".")):
        raise BlackboxRejected("module must be a dotted name")
    _match(_NAME, function, "function (top-level name)")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise BlackboxRejected(f"check {data['id']}: 1 to {MAX_CASES} cases")
    parsed = tuple(_case(case) for case in cases)
    _unique([case.id for case in parsed], f"case ids of {data['id']}")
    return Check(data["id"], module, function, parsed)


def _case(data: Any) -> Case:
    _keys(data, {"id", "args", "expected"}, "case", optional={"kwargs"})
    _match(_ID, data["id"], "case id")
    args, kwargs = data["args"], data.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise BlackboxRejected(f"case {data['id']}: args must be a list, kwargs an object")
    return Case(data["id"], canonical_json(args), canonical_json(kwargs),
                canonical_json(data["expected"]))  # fmt: skip


def _keys(data: Any, required: set[str], what: str, optional: frozenset | set = frozenset()):
    if not isinstance(data, dict) or not required <= set(data) <= required | set(optional):
        raise BlackboxRejected(f"{what} must have keys {sorted(required)} (+{sorted(optional)})")


def _match(pattern: re.Pattern, value: Any, what: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise BlackboxRejected(f"bad {what}")


def _unique(values: list[str], what: str) -> None:
    if len(values) != len(set(values)):
        raise BlackboxRejected(f"duplicate {what}")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_float(text: str) -> Any:
    raise ValueError("floats and NaN/Infinity are not supported")
