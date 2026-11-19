"""Record and re-check the base Python install the sealed #1353 gate trusts (#1450).

The gate (pra1353_gate.py) refuses a base install that lies inside the checked tree, but it runs
inside the interpreter whose paths it checks, so it cannot attest that interpreter. This script
adds the missing outside reference:

* ``record OUT`` runs in ci.yml right after the interpreter is installed and before ``uv sync`` or
  any other step that executes code from the pull request. It writes the base install paths and a
  digest of its stdlib, platstdlib and lib-dynload trees to OUT (a path outside the checkout).
* ``verify OUT`` runs right before the gate. It recomputes both from the running interpreter and
  fails closed on any difference, a missing or malformed record, or an unreadable tree.

Standard library only; run it as ``python -I -S -B``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import sysconfig
from pathlib import Path

SKIPPED_DIRS = frozenset({"__pycache__", "site-packages"})  # not the interpreter's own code

RECORD_KEYS = frozenset({"version", "base_prefix", "base_exec_prefix", "roots", "digest", "files"})


def _roots() -> list[str]:
    base = {"base": sys.base_prefix, "platbase": sys.base_exec_prefix}  # not a venv's own lib
    stdlib = sysconfig.get_path("stdlib", vars=base)
    platstdlib = sysconfig.get_path("platstdlib", vars=base)
    found = {stdlib, platstdlib, os.path.join(platstdlib, "lib-dynload")}
    return sorted(os.path.realpath(p) for p in found if os.path.isdir(p))


def _entry(outer, root: str, path: str) -> None:
    """Feed one entry: relative path, type, mode bits, and content (a symlink: its target text)."""
    info = os.lstat(path)
    rel = f"{root}\0{os.path.relpath(path, root)}".encode()
    if stat.S_ISLNK(info.st_mode):
        body, kind = os.readlink(path).encode(), b"L"  # a symlink to a directory counts here
    elif stat.S_ISDIR(info.st_mode):
        body, kind = b"", b"D"
    elif stat.S_ISREG(info.st_mode):
        with open(path, "rb") as handle:
            body, kind = hashlib.sha256(handle.read()).digest(), b"F"
    else:
        raise OSError(f"not a regular file, directory or symlink: {path}")
    mode = stat.S_IMODE(info.st_mode).to_bytes(2, "big")
    outer.update(len(rel).to_bytes(4, "big") + rel + kind + mode + body)


def tree_digest(roots: list[str]) -> tuple[str, int]:
    """SHA-256 over every entry under the roots: files, symlinks (also those that point at a
    directory, which ``os.walk`` lists among the directories but does not enter) and the
    directories themselves, each with its mode bits. Anything else is an error, so a swapped-in
    device or socket cannot hide. Returns (digest, number of entries)."""
    outer, count = hashlib.sha256(), 0
    for root in sorted(set(roots)):
        for here, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
            for name in sorted([*names, *dirs]):
                _entry(outer, root, os.path.join(here, name))
                count += 1
    return outer.hexdigest(), count


def snapshot(roots: list[str] | None = None) -> dict[str, object]:
    chosen = _roots() if roots is None else sorted(roots)
    digest, files = tree_digest(chosen)
    return {
        "version": sys.version,
        "base_prefix": os.path.realpath(sys.base_prefix),
        "base_exec_prefix": os.path.realpath(sys.base_exec_prefix),
        "roots": chosen,
        "digest": digest,
        "files": files,
    }


def compare(recorded: object, current: dict[str, object]) -> str | None:
    """None when the record matches; else a one-line reason (never the file contents)."""
    if not isinstance(recorded, dict) or set(recorded) != RECORD_KEYS:
        return "record is malformed"
    for key in sorted(RECORD_KEYS):
        if recorded[key] != current[key]:
            return f"{key} differs from the recorded base Python install"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"record", "verify"}:
        print("usage: pra1353_base_record.py record|verify OUT", file=sys.stderr)
        return 2
    out = Path(argv[2])
    try:
        current = snapshot()
        if argv[1] == "record":
            if out.exists():
                print(f"refusing to overwrite {out}", file=sys.stderr)
                return 1
            out.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
            print(f"recorded {current['files']} files, digest {current['digest']}")
            return 0
        reason = compare(json.loads(out.read_text(encoding="utf-8")), current)
    except (OSError, ValueError) as exc:  # no record, unreadable tree, bad JSON: fail closed
        print(f"base Python install check failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    if reason is not None:
        print(f"base Python install check failed: {reason}", file=sys.stderr)
        return 1
    print("base Python install matches the record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
