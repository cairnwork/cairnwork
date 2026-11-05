"""The shadow input: what the verifier used, as recorded at verify time (Open 1, choice b).

The record names the interpreter, the installed plugin set and the ini text the verifier ran
with, plus their hashes. The CLI does not trust it blindly: it re-derives the plugin set from the
live interpreter and compares. Anything missing or different is UNCOMPARABLE, never NA."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from cairnwork import pytest_config
from cairnwork.gitcmd import OID

REQUIRED = ("repo", "base", "candidate", "test_commit", "tested_tree", "target")
NAME = re.compile(r"[A-Za-z_]\w*(::[A-Za-z_]\w*)?")
HEX_OIDS = ("base", "candidate", "test_commit", "tested_tree")


class Uncomparable(Exception):
    """The observer's inputs cannot be tied to what the verifier ran (reason in the text)."""


@dataclass(frozen=True)
class ShadowInput:
    repo: Path
    base: str
    candidate: str
    test_commit: str
    tested_tree: str
    file: str
    name: str
    interpreter: str
    pythonpath: tuple[str, ...]
    cw_verdict: str
    cw_reason: str
    test_roots: tuple[str, ...]
    na_reason: str = ""


def plugins_sha256(plugins) -> str:
    return hashlib.sha256("\n".join(sorted(plugins)).encode()).hexdigest()


def describe_verifier(python: str, ini_text: bytes) -> dict:
    """What the verifier hook records at verify time (also used to build test inputs)."""
    plugins = list(pytest_config.installed_plugins(python))
    return {
        "interpreter": python,
        "installed_plugins": plugins,
        "installed_plugins_sha256": plugins_sha256(plugins),
        "ini_text": ini_text.decode(),
        "ini_sha256": hashlib.sha256(ini_text).hexdigest(),
    }


def load(path: Path) -> ShadowInput:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Uncomparable(f"unreadable shadow input: {exc}") from exc
    if not isinstance(raw, dict):
        raise Uncomparable("shadow input is not an object")
    return check(raw)


def check(raw: dict) -> ShadowInput:
    for key in (*REQUIRED, "interpreter", "installed_plugins", "ini_text"):
        if raw.get(key) in (None, "", []) and key != "installed_plugins":
            raise Uncomparable(f"missing {key}")
    if not isinstance(raw.get("installed_plugins"), list):
        raise Uncomparable("missing installed_plugins")
    if not all(isinstance(raw[k], str) and OID.fullmatch(raw[k]) for k in HEX_OIDS):
        raise Uncomparable("base, candidate, test_commit and tested_tree must be 40-hex ids")
    python = raw["interpreter"]
    if not (isinstance(python, str) and os.path.isabs(python) and os.access(python, os.X_OK)):
        raise Uncomparable(f"interpreter is not an executable absolute path: {python!r}")
    ini = raw["ini_text"].encode()
    if raw.get("ini_sha256") != hashlib.sha256(ini).hexdigest():
        raise Uncomparable("ini_text does not match the recorded ini_sha256")
    plugins = raw["installed_plugins"]
    if raw.get("installed_plugins_sha256") != plugins_sha256(plugins):
        raise Uncomparable("installed_plugins does not match the recorded hash")
    live = pytest_config.installed_plugins(python)
    if plugins_sha256(live) != plugins_sha256(plugins):
        raise Uncomparable(f"installed plugins differ from the verifier's: now {sorted(live)}")
    file, _, name = str(raw["target"]).partition("::")
    base, bracket, _ = name.partition("[")  # what follows "[" is never used for anything
    if not file.endswith(".py") or ".." in file.split("/") or not NAME.fullmatch(base):
        raise Uncomparable(f"target must be <file>.py::<test>, got {raw['target']!r}")
    if file.startswith("/"):
        raise Uncomparable("target file must be relative")
    return ShadowInput(
        repo=Path(raw["repo"]),
        base=raw["base"],
        candidate=raw["candidate"],
        test_commit=raw["test_commit"],
        tested_tree=raw["tested_tree"],
        file=file,
        name=base.replace("::", "."),
        na_reason="PARAMETRIZED" if bracket else "",
        interpreter=python,
        pythonpath=_ini_pythonpath(raw["ini_text"]),
        cw_verdict=str(raw.get("cw_verdict", "")),
        cw_reason=str(raw.get("cw_reason", "")),
        test_roots=tuple(raw.get("test_roots") or ("tests",)),
    )


def _ini_pythonpath(ini_text: str) -> tuple[str, ...]:
    """The verifier's ini import path as paths relative to its checkout (the first entry), so
    they can be re-rooted in the rebuilt checkout. Anything outside that checkout is refused."""
    for line in ini_text.splitlines():
        if line.startswith("pythonpath"):
            entries = shlex.split(line.partition("=")[2])
            if not entries:
                return ()
            first = entries[0]
            rel = tuple(os.path.relpath(e, first) for e in entries[1:])
            if any(r == ".." or r.startswith("../") or os.path.isabs(r) for r in rel):
                raise Uncomparable("ini pythonpath leaves the verifier's checkout")
            return rel
    return ()
