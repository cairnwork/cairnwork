"""Standalone, sealed decision gate for the #1353 witnesses."""

# ruff: noqa: E501

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

_WITNESS = "tests/test_1039_1353_witnesses.py"
_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.fsmonitor",
    "GIT_CONFIG_VALUE_0": "false",
}


def _git_blob(repo: Path, name: bytes) -> bytes:
    listed = subprocess.run(
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
            name.decode(),
        ],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
        timeout=10,
    ).stdout
    records = [record for record in listed.split(b"\0") if record]
    if len(records) != 1:
        raise ValueError(f"pinned file is not uniquely tracked: {name!r}")
    record_name = records[0].split(b"\t", 1)[1]
    fields = records[0].split(b"\t", 1)[0].split()
    if record_name != name:
        raise ValueError("Git returned a different pinned path")
    if len(fields) != 3 or fields[0] not in {b"100644", b"100755"}:
        raise ValueError(f"unapproved pinned file mode: {name!r}")
    oid = fields[1]
    if len(oid) != 40 or any(byte not in b"0123456789abcdef" for byte in oid):
        raise ValueError("invalid pinned blob id")
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), "cat-file", "--batch"],
        input=oid + b"\n",
        check=True,
        capture_output=True,
        env=_GIT_ENV,
        timeout=10,
    ).stdout
    header, body = result.split(b"\n", 1)
    object_id, kind, size = header.split()
    data = body[: int(size)]
    if object_id != oid or kind != b"blob" or len(data) != int(size):
        raise ValueError("invalid pinned Git blob")
    blob_header = b"blob " + size + b"\0"
    if hashlib.sha1(blob_header + data).hexdigest().encode() != oid:
        raise ValueError("pinned Git blob id mismatch")
    return data


def _pins(repo: Path) -> dict[str, str]:
    source = _git_blob(repo, _WITNESS.encode())
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PINS" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                _validate_pins(value)
                return value
    raise ValueError("witness PINS literal missing")


def _validate_pins(value: dict[object, object]) -> None:
    for name, digest in value.items():
        if not isinstance(name, str) or not name.startswith("tests/"):
            raise ValueError("pin path is outside tests")
        if any(unicodedata.category(char) == "Cc" for char in name) or "\\" in name:
            raise ValueError("invalid pin path")
        parts = Path(name).parts
        if len(parts) < 2 or any(part in {".", ".."} for part in parts):
            raise ValueError("invalid pin path")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("invalid pin digest")
    seen: set[str] = set()
    for name in set(value) | {_WITNESS, "tests/pra1353_gate.py"}:
        folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", str(name)).casefold())
        if folded in seen:
            raise ValueError("colliding pin paths")
        seen.add(folded)


def _seal(repo: Path, root: Path, pins: dict[str, str]) -> Path:
    sealed = root / "sealed"
    for directory in (sealed / "tests", sealed / "src", sealed / "pinned"):
        directory.mkdir(parents=True)
    names = set(pins) | {_WITNESS, "tests/pra1353_gate.py"}
    for name in sorted(names):
        data = _git_blob(repo, name.encode())
        expected = pins.get(name)
        if expected is not None and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"pin mismatch: {name}")
        pinned_copy = sealed / "pinned" / name
        pinned_copy.parent.mkdir(parents=True, exist_ok=True)
        pinned_copy.write_bytes(data)
        if name.startswith("tests/"):
            target = sealed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    source = _source_blobs(repo)
    for name, data in source.items():
        target = sealed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for path in sorted(sealed.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    sealed.chmod(0o555)
    return sealed


def _source_blobs(repo: Path) -> dict[str, bytes]:
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
            "src/",
        ],
        check=True,
        capture_output=True,
        env=_GIT_ENV,
        timeout=20,
    ).stdout
    entries: list[tuple[bytes, str]] = []
    keys: set[str] = set()
    packages = {
        "cairnwork",
        "cairnwork_attest",
        "cairnwork_protocol",
        "cairnwork_quickstart",
        "cairnwork_sandbox",
    }
    for record in (part for part in listing.split(b"\0") if part):
        metadata, name = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if mode not in {b"100644", b"100755"} or stage != b"0":
            raise ValueError("unapproved source mode")
        if any(byte < 32 or byte == 127 for byte in name):
            raise ValueError("control character in source path")
        if not name.endswith(b".py"):
            raise ValueError("source path must end in .py")
        text = name.decode("utf-8", "strict")
        if any(unicodedata.category(char) == "Cc" for char in text):
            raise ValueError("control character in source path")
        parts = text.split("/")
        if (
            parts[1] not in packages
            or any(not part.isidentifier() for part in parts[1:-1])
            or not parts[-1][:-3].isidentifier()
        ):
            raise ValueError("unapproved source path")
        folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())
        if folded in keys:
            raise ValueError("colliding source paths")
        keys.add(folded)
        if len(oid) != 40 or any(byte not in b"0123456789abcdef" for byte in oid):
            raise ValueError("invalid source blob id")
        entries.append((oid, text))
    init = {name for _, name in entries if name.endswith("/__init__.py")}
    for _, name in entries:
        parent = name.rsplit("/", 1)[0]
        while parent.startswith("src/"):
            if parent + "/__init__.py" not in init:
                raise ValueError("source directory lacks tracked __init__.py")
            parent = parent.rsplit("/", 1)[0]
    result = {}
    for oid, name in entries:
        data = _git_blob_by_oid(repo, oid)
        result[name] = data
    return result


def _git_blob_by_oid(repo: Path, oid: bytes) -> bytes:
    output = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), "cat-file", "--batch"],
        input=oid + b"\n",
        check=True,
        capture_output=True,
        env=_GIT_ENV,
        timeout=10,
    ).stdout
    header, body = output.split(b"\n", 1)
    obj, kind, size = header.split()
    data = body[: int(size)]
    if obj != oid or kind != b"blob" or len(data) != int(size):
        raise ValueError("invalid source Git blob")
    blob_header = b"blob " + size + b"\0"
    if hashlib.sha1(blob_header + data).hexdigest().encode() != oid:
        raise ValueError("source Git blob id mismatch")
    return data


def _restore_permissions(path: Path) -> None:
    for item in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        item.chmod(0o755 if item.is_dir() else 0o644)
    path.chmod(0o755)


def _run_sealed(sealed: Path, repo: Path) -> int:
    stdlib = Path(sysconfig_path("stdlib"))
    platstdlib = Path(sysconfig_path("platstdlib"))
    purelib = Path(sysconfig_path("purelib"))
    platlib = Path(sysconfig_path("platlib"))
    roots = [
        str(stdlib),
        str(platstdlib),
        str(platstdlib / "lib-dynload"),
        str(sealed / "src"),
        str(sealed / "tests"),
        str(purelib),
        str(platlib),
    ]
    code = "import sys; sys.path[:]=__import__('json').loads(__import__('os').environ['PRA1353_PATHS']); import pytest; raise SystemExit(pytest.main(['-c','/dev/null','--rootdir='+__import__('os').environ['PRA1353_SEALED'],'--confcutdir='+__import__('os').environ['PRA1353_SEALED']+'/tests','-p','no:cacheprovider','--noconftest',__import__('os').environ['PRA1353_WITNESS']]))"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(sealed.parent),
        "TMPDIR": str(sealed.parent),
        "LANG": "C.UTF-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PRA1353_PATHS": __import__("json").dumps(roots),
        "PRA1353_SEALED": str(sealed),
        "PRA1353_WITNESS": str(sealed / _WITNESS),
        "PRA1353_SOURCE_REPO": str(repo),
    }
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code],
        cwd=sealed,
        env=env,
        check=False,
    ).returncode


def sysconfig_path(name: str) -> str:
    """Return this interpreter's path; under -S, 3.11 leaves sys.prefix at the base install."""
    import sysconfig

    executable = Path(sys.executable)
    for prefix in (executable.parent, executable.parent.parent):
        if name in {"purelib", "platlib"} and (prefix / "pyvenv.cfg").is_file():
            base = {"base": str(prefix), "platbase": str(prefix)}
            return sysconfig.get_paths(scheme="venv", vars=base)[name]
    return sysconfig.get_paths()[name]


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--sealed":
        return _run_sealed(Path.cwd(), Path(sys.argv[2]))
    repo = Path.cwd().resolve()
    if len(sys.argv) != 1:
        raise ValueError("unexpected gate arguments")
    pins = _pins(repo)
    with tempfile.TemporaryDirectory(prefix="cw1353-gate-") as directory:
        root = Path(directory).resolve()
        sealed = _seal(repo, root, pins)
        try:
            return subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(sealed / "tests/pra1353_gate.py"),
                    "--sealed",
                    str(repo),
                ],
                cwd=sealed,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(root),
                    "TMPDIR": str(root),
                    "LANG": "C.UTF-8",
                    **_GIT_ENV,
                },
                check=False,
            ).returncode
        finally:
            _restore_permissions(sealed)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
