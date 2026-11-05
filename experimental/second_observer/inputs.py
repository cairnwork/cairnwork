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
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cairnwork import pytest_config
from cairnwork.gitcmd import OID, GitCommandError

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


def interpreter_identity(python: str) -> str:
    """Which interpreter this is: its real path, version and prefix, as the interpreter says."""
    done = subprocess.run(  # noqa: S603 - the verifier's own interpreter path, no shell
        [python, "-I", "-c", "import sys; print(sys.version); print(sys.prefix)"],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    return hashlib.sha256((os.path.realpath(python) + "\n" + done.stdout).encode()).hexdigest()


def describe_verifier(python: str, ini_text: bytes, import_paths=()) -> dict:
    """What the verifier hook records at verify time (also used to build test inputs)."""
    plugins = list(pytest_config.installed_plugins(python))
    return {
        "interpreter": python,
        "interpreter_identity": interpreter_identity(python),
        "installed_plugins": plugins,
        "installed_plugins_sha256": plugins_sha256(plugins),
        "ini_text": ini_text.decode(),
        "ini_sha256": hashlib.sha256(ini_text).hexdigest(),
        "import_paths": list(import_paths),
    }


def load(path: Path) -> ShadowInput:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Uncomparable(f"unreadable shadow input: {exc}") from exc
    if not isinstance(raw, dict):
        raise Uncomparable("shadow input is not an object")
    return check(raw)


def _strings(value, many=False) -> bool:
    if many:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, str)


def _require_types(raw: dict) -> None:
    single = (*REQUIRED, "interpreter", "interpreter_identity", "ini_text", "ini_sha256")
    for key in (*single, "installed_plugins_sha256"):
        if not _strings(raw.get(key)) or not raw[key]:
            raise Uncomparable(f"missing or not a string: {key}")
    for key in ("installed_plugins", "import_paths"):
        if key not in raw:
            raise Uncomparable(f"missing {key}")
        if not _strings(raw[key], many=True):
            raise Uncomparable(f"{key} must be a list of strings")
    roots = raw.get("test_roots", ["tests"])
    if not _strings(roots, many=True) or not roots:
        raise Uncomparable("test_roots must be a non-empty list of strings")
    for key in ("cw_verdict", "cw_reason"):
        if not _strings(raw.get(key, "")):
            raise Uncomparable(f"{key} must be a string")


def _regenerate_ini(raw: dict, repo: Path, live_plugins) -> None:
    """The ini must be exactly what Cairnwork renders from the recorded import paths, the base
    config and the live plugin set: a stale, edited or foreign text is not comparable."""
    first = _first_entry(raw["ini_text"])
    try:
        settings = pytest_config.verifier_settings(repo, raw["base"], tuple(raw["import_paths"]))
        expected = settings.ini_text(Path(first), tuple(live_plugins))
    except (pytest_config.PytestConfigError, GitCommandError, OSError) as exc:
        raise Uncomparable(f"cannot re-render the ini: {exc}") from exc
    if expected != raw["ini_text"].encode():
        raise Uncomparable("ini_text is not what Cairnwork renders for the recorded inputs")


def check(raw: dict) -> ShadowInput:
    _require_types(raw)
    if not all(OID.fullmatch(raw[k]) for k in HEX_OIDS):
        raise Uncomparable("base, candidate, test_commit and tested_tree must be 40-hex ids")
    python = raw["interpreter"]
    if not (os.path.isabs(python) and os.access(python, os.X_OK)):
        raise Uncomparable(f"interpreter is not an executable absolute path: {python!r}")
    try:
        if interpreter_identity(python) != raw["interpreter_identity"]:
            raise Uncomparable("interpreter is not the one the verifier used")
        live = pytest_config.installed_plugins(python)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Uncomparable(f"interpreter does not run: {exc}") from exc
    if raw["ini_sha256"] != hashlib.sha256(raw["ini_text"].encode()).hexdigest():
        raise Uncomparable("ini_text does not match the recorded ini_sha256")
    plugins = raw["installed_plugins"]
    if raw["installed_plugins_sha256"] != plugins_sha256(plugins):
        raise Uncomparable("installed_plugins does not match the recorded hash")
    if plugins_sha256(live) != plugins_sha256(plugins):
        raise Uncomparable(f"installed plugins differ from the verifier's: now {sorted(live)}")
    _regenerate_ini(raw, Path(raw["repo"]), live)
    file, _, name = raw["target"].partition("::")
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
        cw_verdict=raw.get("cw_verdict", ""),
        cw_reason=raw.get("cw_reason", ""),
        test_roots=tuple(raw.get("test_roots") or ("tests",)),
    )


def _pythonpath_entries(ini_text: str) -> list[str]:
    for line in ini_text.splitlines():
        if line.startswith("pythonpath"):
            return shlex.split(line.partition("=")[2])
    raise Uncomparable("ini has no pythonpath line")


def _first_entry(ini_text: str) -> str:
    entries = _pythonpath_entries(ini_text)
    if not entries or not os.path.isabs(entries[0]):
        raise Uncomparable("the first ini pythonpath entry must be an absolute path")
    return entries[0]


def _ini_pythonpath(ini_text: str) -> tuple[str, ...]:
    """The verifier's ini import path as paths relative to its checkout (the first entry, which
    is "."), so they can be re-rooted in the rebuilt checkout; the checkout root stays first, as
    pytest puts it. Anything outside that checkout is refused."""
    first = _first_entry(ini_text)
    rel = tuple(os.path.relpath(e, first) for e in _pythonpath_entries(ini_text))
    if any(r == ".." or r.startswith("../") or os.path.isabs(r) for r in rel):
        raise Uncomparable("ini pythonpath leaves the verifier's checkout")
    return rel
