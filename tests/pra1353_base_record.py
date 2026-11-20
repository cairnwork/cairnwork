"""Record and re-check the base Python install the sealed #1353 gate trusts (#1450).

The gate (pra1353_gate.py) refuses a base install that lies inside the checked tree, but it runs
inside the interpreter whose paths it checks, so it cannot attest that interpreter. This script
adds the missing outside reference:

* In ci.yml, ``record OUT`` runs right after ``uv sync`` of the trusted venv built from the base's
  lock and before any step that executes code from the pull request. It writes the base install
  paths and digests of its stdlib, platstdlib, lib-dynload and in-prefix sys.path entries to OUT
  (outside the checkout).
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

ROOT_SKIPPED = frozenset({"site-packages"})  # only directly under a root; the gate runs -I -S

RECORD_KEYS = frozenset(
    {"version", "base_prefix", "base_exec_prefix", "roots", "digest", "files", "path_extras"}
)


def _roots() -> list[str]:
    base = {"base": sys.base_prefix, "platbase": sys.base_exec_prefix}  # not a venv's own lib
    stdlib = sysconfig.get_path("stdlib", vars=base)
    platstdlib = sysconfig.get_path("platstdlib", vars=base)
    found = {stdlib, platstdlib, os.path.join(platstdlib, "lib-dynload")}
    return sorted(os.path.realpath(p) for p in found if os.path.isdir(p))


def _file_digest(path: str) -> bytes:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).digest()


def _path_entries(roots: list[str]) -> list[str]:
    """Return in-prefix sys.path entries outside the recorded roots.

    "In the prefix" compares the lexical and the resolved form of the entry with both forms of the
    prefixes, so a symlinked prefix does not hide an entry that spells the real path. "In the
    roots" compares only the entry's lexical form (roots are taken in both spellings): an entry
    that is itself a symlink standing outside the roots but pointing into them is recorded, as the
    roots' digest hashes the tree it points at, not the link."""
    prefixes = {
        form(p)
        for p in (sys.base_prefix, sys.base_exec_prefix)
        for form in (os.path.abspath, os.path.realpath)
    }
    recorded = {form(r) for r in roots for form in (os.path.abspath, os.path.realpath)}
    entries = set()
    for entry in sys.path:
        path = os.path.abspath(entry)
        forms = {path, os.path.realpath(entry)}
        under_prefix = any(f == p or f.startswith(p + os.sep) for f in forms for p in prefixes)
        in_roots = any(path == r or path.startswith(r + os.sep) for r in recorded)
        if under_prefix and not in_roots:
            entries.add(path)
    return sorted(entries)


def _path_state(path: str, roots: list[str]) -> str:
    """Return absent or a digest of a sys.path entry and its importable contents."""
    if not os.path.lexists(path):
        return "absent"
    info = os.lstat(path)
    mode = stat.S_IMODE(info.st_mode).to_bytes(2, "big")
    if stat.S_ISREG(info.st_mode):
        body = b"F" + mode + _file_digest(path)
    elif stat.S_ISDIR(info.st_mode):
        return tree_digest([path])[0]
    elif stat.S_ISLNK(info.st_mode):
        body = b"L" + mode + os.readlink(path).encode() + b"\0" + _link_target(path, roots)
    else:
        raise OSError(f"not a regular file, directory or symlink: {path}")
    return hashlib.sha256(body).hexdigest()


def _path_extras(roots: list[str]) -> list[list[str]]:
    return [[path, _path_state(path, roots)] for path in _path_entries(roots)]


def _link_target(path: str, roots: list[str]) -> bytes:
    """What a symlink stands for, beyond its text: the content of a regular file it points at,
    the real path of a directory inside the trees (the walk covers it; one outside them is an
    error, as its content would go unseen), a marker if dangling.

    Coverage is limited to the listed trees and the sys.path entries under the base prefixes:
    interpreter executables (a venv's bin/python) and libpython are NOT recorded (known boundary).
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return b"dangling"
    real = os.path.realpath(path)
    if stat.S_ISREG(info.st_mode):
        return b"file" + _file_digest(real)
    if stat.S_ISDIR(info.st_mode):
        if not any(real == r or real.startswith(r + os.sep) for r in roots):
            raise OSError(f"symlink to a directory outside the trees: {path}")  # fail closed
        return b"dir-inside" + real.encode()  # the resolved target too: a hop may be retargeted
    raise OSError(f"symlink to something that is not a file or directory: {path}")


def _entry(outer, roots: list[str], root: str, path: str) -> None:
    """Feed one entry: relative path, type, mode bits, and content (a symlink: its target text
    and what it points at)."""
    info = os.lstat(path)
    rel = f"{root}\0{os.path.relpath(path, root)}".encode()
    if stat.S_ISLNK(info.st_mode):
        body, kind = os.readlink(path).encode() + b"\0" + _link_target(path, roots), b"L"
    elif stat.S_ISDIR(info.st_mode):
        body, kind = b"", b"D"
    elif stat.S_ISREG(info.st_mode):
        body, kind = _file_digest(path), b"F"
    else:
        raise OSError(f"not a regular file, directory or symlink: {path}")
    mode = stat.S_IMODE(info.st_mode).to_bytes(2, "big")
    outer.update(len(rel).to_bytes(4, "big") + rel + kind + mode + body)


def _raise(error: OSError) -> None:
    raise error  # os.walk skips an unreadable directory silently unless told otherwise


def tree_digest(roots: list[str]) -> tuple[str, int]:
    """SHA-256 over every entry under the roots and the roots themselves, bytecode caches
    included: files, symlinks (those
    that point at a directory too, which ``os.walk`` lists among the directories but does not
    enter) and directories, each with its mode bits. A symlink also contributes what it points at
    (see ``_link_target``). An unreadable directory, or anything that is not a file, directory or
    symlink, is an error, so nothing can hide. Returns (digest, number of entries)."""
    outer, count = hashlib.sha256(), 0
    chosen = sorted(set(roots))
    for root in chosen:
        _entry(outer, chosen, root, root)
        count += 1
        for here, dirs, names in os.walk(root, followlinks=False, onerror=_raise):
            # __pycache__ is hashed (a swapped unchecked-hash .pyc would run without its .py
            # changing); only a root's own site-packages is left out, never a nested one
            dirs[:] = sorted(d for d in dirs if not (here == root and d in ROOT_SKIPPED))
            for name in sorted([*names, *dirs]):
                _entry(outer, chosen, root, os.path.join(here, name))
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
        "path_extras": _path_extras(chosen),
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
