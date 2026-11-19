"""G1 AC1 probe (stdlib only), run in place of the #1353 runner by the child `consume.launch`
starts. It tries to write every root the referee later trusts and prints one JSON report:
``{"writable": [[label, how, path], ...], "tried": N}``. Anything it creates it removes again."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

MARK = f".g1-probe-{os.getpid()}"


def _create(directory: Path, name: str) -> bool:
    target = directory / name
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False
    os.close(fd)
    target.unlink()
    return True


def _chmod_back(path: Path) -> bool:
    """True when the owner can give itself write access again (the same-uid undo of a seal)."""
    try:
        before = stat.S_IMODE(path.lstat().st_mode)
        os.chmod(path, before | 0o200)
        changed = stat.S_IMODE(path.lstat().st_mode) != before
        os.chmod(path, before)
    except OSError:
        return False
    return changed


def _create_after_chmod(directory: Path, name: str) -> bool:
    """chmod the directory writable, create a file, restore the mode (a seal undone, then used)."""
    try:
        before = stat.S_IMODE(directory.lstat().st_mode)
        os.chmod(directory, before | 0o300)
    except OSError:
        return False
    try:
        return _create(directory, name)
    finally:
        os.chmod(directory, before)


def _rewrite(path: Path) -> bool:
    try:
        before = stat.S_IMODE(path.lstat().st_mode)
        os.chmod(path, before | 0o200)
        try:
            with open(path, "ab"):
                return True
        finally:
            os.chmod(path, before)
    except OSError:
        return False


def probe_root(label: str, root: Path, found: list[list[str]]) -> int:
    tried = 0
    suffix = ".pth" if label in ("purelib", "platlib") else ""
    for how, hit in (
        ("create", _create(root, MARK + suffix)),
        ("chmod", _chmod_back(root)),
        ("chmod+create", _create_after_chmod(root, MARK + suffix)),
    ):
        tried += 1
        if hit:
            found.append([label, how, str(root)])
    cache = root / "__pycache__"
    if cache.is_dir():
        tried += 1
        if _create(cache, MARK + ".pyc"):
            found.append([label + "/__pycache__", "create", str(cache)])
    if label.startswith("sealed"):
        for item in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
            tried += 1
            if _rewrite(item):
                found.append([label, "rewrite", str(item)])
    return tried


def main(argv: list[str]) -> None:
    stdlib, platstdlib, dynload, sealed_tests, sealed_src, purelib, platlib = map(Path, argv)
    sealed = sealed_tests.parent
    roots = {
        "stdlib": stdlib,
        "platstdlib": platstdlib,
        "lib-dynload": dynload,
        "sealed": sealed,
        "sealed/tests": sealed_tests,
        "sealed/src": sealed_src,
        "purelib": purelib,
        "platlib": platlib,
        "child-reports": Path(os.environ["PRA1353_CHILD_REPORT_DIR"]),
        "cwd": Path.cwd(),
    }
    found: list[list[str]] = []
    tried = sum(probe_root(label, root, found) for label, root in roots.items() if root.is_dir())
    print(json.dumps({"writable": found, "tried": tried}))


if __name__ == "__main__":
    main(sys.argv[1:])
