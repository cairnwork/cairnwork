"""Git-object sealing helpers for the #1353 witness boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

_PACKAGES = {
    "cairnwork",
    "cairnwork_attest",
    "cairnwork_protocol",
    "cairnwork_quickstart",
    "cairnwork_sandbox",
}
_CONTROL = re.compile(rb"[\x00-\x1f\x7f]")
_OID = re.compile(rb"[0-9a-f]{40}")


def interpreter_paths() -> dict[str, str]:
    """sysconfig paths with the venv site dirs, also under -S (3.11 sets the prefix in site).

    The stdlib dirs come from the base install: inside a venv, platstdlib would be the venv.
    """
    import sys
    import sysconfig

    paths = sysconfig.get_paths(vars={"base": sys.base_prefix, "platbase": sys.base_exec_prefix})
    executable = Path(sys.executable)
    for prefix in (executable.parent, executable.parent.parent):
        if (prefix / "pyvenv.cfg").is_file():
            base = {"base": str(prefix), "platbase": str(prefix)}
            venv = sysconfig.get_paths(scheme="venv", vars=base)
            return {**paths, "purelib": venv["purelib"], "platlib": venv["platlib"]}
    return paths


def check_base_install(stdlib: str, platstdlib: str, *trees: Path) -> None:
    """Fail closed when the base install a venv's pyvenv.cfg names lies inside a checked tree."""
    import sys

    roots = {(stat.st_dev, stat.st_ino) for stat in map(os.stat, trees)}
    names = [sys.base_prefix, sys.base_exec_prefix, stdlib, platstdlib, f"{platstdlib}/lib-dynload"]
    for name in names:
        # File identity, not strings: catches case-folded and symlinked spellings of a tree root.
        for path in {Path(os.path.abspath(name)), Path(os.path.realpath(name))}:
            for parent in (path, *path.parents):
                try:
                    stat = os.stat(parent)
                except OSError as exc:
                    raise ValueError("base install path cannot be checked") from exc
                if (stat.st_dev, stat.st_ino) in roots:
                    raise ValueError("base install lies inside the checked tree")


def git_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
    }


def read_module_report(path: Any) -> dict[str, Any]:
    """Fail closed when a child skipped or corrupted its finally-written report."""
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("child module-origin report missing or unreadable") from exc
    if not isinstance(report, dict) or not isinstance(report.get("modules"), list):
        raise ValueError("invalid child module-origin report")
    return report


def tracked_py_blobs(repo: Any, *, prefix: str) -> dict[str, bytes]:
    """Return validated Python blobs from the index without consulting the checkout."""
    env = git_env()
    listing = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            "ls-files",
            "-s",
            "-z",
            "--",
            prefix,
        ],
        check=True,
        capture_output=True,
        timeout=20,
        env=env,
    ).stdout
    entries = _tracked_entries(listing)
    return _read_blobs(repo, entries, env)


def _tracked_entries(listing: bytes) -> list[tuple[bytes, bytes]]:
    records = [record for record in listing.split(b"\0") if record]
    entries: list[tuple[bytes, bytes]] = []
    names: set[str] = set()
    for record in records:
        metadata, separator, name = record.partition(b"\t")
        fields = metadata.split()
        if (
            not separator
            or len(fields) != 3
            or fields[0] not in {b"100644", b"100755"}
            or fields[2] != b"0"
        ):
            raise ValueError("invalid tracked source entry")
        oid = fields[1]
        if not _OID.fullmatch(oid):
            raise ValueError("invalid tracked blob id")
        if _CONTROL.search(name):
            raise ValueError("control character in tracked path")
        try:
            path = name.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("tracked path is not UTF-8") from exc
        if any(unicodedata.category(char) == "Cc" for char in path):
            raise ValueError("control character in tracked path")
        parts = PurePosixPath(path).parts
        if not parts or parts[0] != "src" or len(parts) < 3:
            raise ValueError("invalid source path")
        if path.endswith(".py"):
            if parts[1] not in _PACKAGES or not all(part.isidentifier() for part in parts[1:-1]):
                raise ValueError("unapproved source path")
            if not parts[-1][:-3].isidentifier():
                raise ValueError("invalid Python filename")
            folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())
            if folded in names:
                raise ValueError("colliding tracked source paths")
            names.add(folded)
            entries.append((oid, name))
        else:
            raise ValueError("tracked source path must end in .py")
    init_paths = {name for _, name in entries if name.endswith(b"/__init__.py")}
    for _, name in entries:
        parent = name.rsplit(b"/", 1)[0]
        while parent.startswith(b"src/") and parent != b"src":
            if parent + b"/__init__.py" not in init_paths:
                raise ValueError("source directory lacks tracked __init__.py")
            parent = parent.rsplit(b"/", 1)[0]
    return entries


def _read_blobs(
    repo: Any, entries: list[tuple[bytes, bytes]], env: dict[str, str]
) -> dict[str, bytes]:
    if not entries:
        return {}
    batch = b"".join(oid + b"\n" for oid, _ in entries)
    output = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), "cat-file", "--batch"],
        input=batch,
        check=True,
        capture_output=True,
        timeout=30,
        env=env,
    ).stdout
    result: dict[str, bytes] = {}
    offset = 0
    for oid, raw_name in entries:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise ValueError("truncated cat-file header")
        header = output[offset:header_end].split()
        if len(header) != 3 or header[0] != oid or header[1] != b"blob":
            raise ValueError("cat-file returned a different object")
        size = int(header[2])
        body_start = header_end + 1
        body_end = body_start + size
        data = output[body_start:body_end]
        if len(data) != size or output[body_end : body_end + 1] != b"\n":
            raise ValueError("truncated cat-file blob")
        header = b"blob " + str(size).encode() + b"\0"
        if hashlib.sha1(header + data).hexdigest().encode() != oid:
            raise ValueError("cat-file blob id mismatch")
        result[raw_name.decode("utf-8")] = data
        offset = body_end + 1
    return result
