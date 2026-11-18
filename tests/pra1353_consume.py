"""Consumer side of the isolated #1353 runner boundary."""

# Evasion source strings below are intentionally preserved as executable snippets.
# ruff: noqa: E501

from __future__ import annotations

import _imp
import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from pra1353_seal import tracked_py_blobs

_NO_SANDBOX_BASE = "UNCOMPARABLE: BASE_ENV_FAILED: unchanged base: base-probe-failed (no-sandbox: no usable sandbox backend, so nothing ran)"
_ACCOUNT_GAP = (
    "[blackbox:add: VERIFICATION_INCOMPLETE: BLACKBOX_NEEDS_VERIFIER_ACCOUNT [matched 1/1]]"
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "pra1353_runner.py"
SEQUENCES = set(range(56))
_SEALED_PACKAGE_FILES = {
    "tests/__init__.py": b'"""Sealed namespace for witness imports."""\n',
    "tests/attest/__init__.py": b'"""Sealed namespace for attestation helpers."""\n',
}
_PRODUCT_TOP_LEVELS = {
    "cairnwork",
    "cairnwork_attest",
    "cairnwork_protocol",
    "cairnwork_quickstart",
    "cairnwork_sandbox",
}
# Third-party contents are trusted as part of the installed interpreter environment; their
# origins are allow-listed below, but their individual bytes are not pinned. nexus-certify loads
# ``product``; pytest loads ``py`` in the pinned test-helper dependency closure.
_THIRD_PARTY_TOP_LEVELS = {
    "cryptography",
    "_cffi_backend",
    "cffi",
    "nexus_certify",
    "nexus_core",
    "_pytest",
    "_virtualenv",
    "iniconfig",
    "pluggy",
    "product",
    "py",
    "pygments",
    "pytest",
}


def check_pins(pins: dict[str, str]) -> None:
    for relative, expected in pins.items():
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"pin mismatch: {relative}")


def decode_report(completed: Any) -> dict[str, Any]:
    if completed.returncode != 0:
        raise ValueError(
            f"runner exited {completed.returncode}: {_neutralize(completed.stderr[-400:])}"
        )
    lines = completed.stdout.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise ValueError("runner did not produce exactly one JSON record")
    try:
        report = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("runner output was not JSON") from exc
    validate_rows(report.get("rows"))
    return report


def validate_rows(rows: Any) -> None:
    if not isinstance(rows, list) or len(rows) != 56:
        raise ValueError("runner row count mismatch")
    seqs = [row.get("seq") for row in rows if isinstance(row, dict)]
    if len(seqs) != len(rows) or len(seqs) != 56 or set(seqs) != SEQUENCES:
        raise ValueError("runner sequence set mismatch")
    if len(set(seqs)) != 56:
        raise ValueError("runner duplicate sequence")


def launch(
    pins: dict[str, str],
    *,
    runner: Path | None = None,
    run=subprocess.run,
    interpreter: str | Path = sys.executable,
    source_repo: Path | None = None,
):
    runner = runner or ROOT / "tests" / "pra1353_runner.py"
    check_pins(pins)
    pin_bytes = {name: (ROOT / name).read_bytes() for name in pins}
    for name, data in pin_bytes.items():
        if hashlib.sha256(data).hexdigest() != pins[name]:
            raise ValueError(f"pin changed while sealing: {_neutralize(name)}")
    source_bytes = _tracked_source_bytes(source_repo or ROOT)
    source_pins = {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()}
    paths = sysconfig.get_paths()
    with tempfile.TemporaryDirectory(prefix="cw1353-consumer-") as tmp:
        temp_root = Path(tmp).resolve()
        sealed = temp_root / "sealed"
        (sealed / "tests").mkdir(parents=True)
        (sealed / "src").mkdir()
        for name, data in {**pin_bytes, **source_bytes}.items():
            target = sealed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for name, data in _SEALED_PACKAGE_FILES.items():
            target = sealed / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _make_read_only(sealed)
        cwd = temp_root / "empty"
        cwd.mkdir()
        stdlib_roots = [Path(paths["stdlib"]), Path(paths["platstdlib"])]
        roots = [*stdlib_roots, Path(paths["platstdlib"]) / "lib-dynload"]
        roots = list(dict.fromkeys(roots))
        imports = [sealed / "src", sealed / "tests", Path(paths["purelib"]), Path(paths["platlib"])]
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(temp_root),
            "TMPDIR": str(temp_root),
            "PYTHONHASHSEED": "0",
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "PRA1353_CHILD_REPORT_DIR": str(temp_root / "child-reports"),
        }
        (temp_root / "child-reports").mkdir()
        try:
            completed = run(
                [
                    str(interpreter),
                    "-I",
                    "-S",
                    "-B",
                    str(sealed / Path(runner).relative_to(ROOT)),
                    *(str(path) for path in [*roots, *imports]),
                ],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            report = decode_report(completed)
            _validate_module_origins(
                report.get("modules"), pins, source_pins, sealed, roots, imports
            )
            _validate_child_reports(
                report.get("child_reports"), pins, source_pins, sealed, roots, imports
            )
            _reject_sealed_bytecode(sealed)
        finally:
            _restore_permissions(sealed)
    return report


def _tracked_source_bytes(repo: Path = ROOT) -> dict[str, bytes]:
    return tracked_py_blobs(repo, prefix="src/")


def _reject_sealed_bytecode(sealed: Path) -> None:
    if any(sealed.rglob("*.pyc")):
        raise ValueError("sealed runner wrote bytecode")


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _restore_permissions(root: Path) -> None:
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)


def _neutralize(value: str) -> str:
    return "\n".join(
        "| " + line if line.lstrip().startswith(("::", "##[")) else line
        for line in value.splitlines()
    )


def _validate_reported_path_text(value: str, name: str, field: str) -> None:
    if "\0" in value or "\n" in value or "\r" in value:
        raise ValueError(
            f"invalid runner module {field} for {_neutralize(name)}: {_neutralize(value)}"
        )


def _reported_path(value: str, name: str, field: str) -> Path:
    _validate_reported_path_text(value, name, field)
    try:
        return Path(value).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            f"invalid runner module {field} for {_neutralize(name)}: {_neutralize(str(exc))}"
        ) from None


def _is_interpreter_module(name: str, origin: str, loader: object) -> bool:
    if origin == "built-in":
        return loader == "BuiltinImporter" and name in sys.builtin_module_names
    if origin == "frozen":
        return loader == "FrozenImporter" and _imp.is_frozen(name)
    return False


def _module_expectations(
    pins: dict[str, str], source_pins: dict[str, str], sealed: Path
) -> tuple[dict[str, Path], dict[str, Path], dict[str, str]]:
    pinned_paths = {name: sealed / name for name in pins if name.startswith(("tests/", "scripts/"))}
    generated = {
        name: hashlib.sha256(data).hexdigest() for name, data in _SEALED_PACKAGE_FILES.items()
    }
    all_expected = {**pins, **source_pins, **generated}
    paths = {name: (sealed / name).resolve() for name in all_expected}
    pinned_names = {_module_name(name): path for name, path in pinned_paths.items()}
    expected_names = {_module_name(name): path for name, path in paths.items()}
    pinned_names["__main__"] = sealed / "tests/pra1353_runner.py"
    return pinned_names, expected_names, all_expected


def _validate_module_origins(
    modules: Any,
    pins: dict[str, str],
    source_pins: dict[str, str],
    sealed: Path,
    stdlib_roots: list[Path],
    imports: list[Path],
) -> None:
    if not isinstance(modules, list):
        raise ValueError("runner module-origin report missing")
    pinned_names, expected_names, all_expected = _module_expectations(pins, source_pins, sealed)
    seen_names = set()
    for item in modules:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("invalid runner module-origin entry")
        name, filename = item["name"], item.get("file")
        seen_names.add(name)
        loader = item.get("loader")
        expected = pinned_names.get(name)
        if filename is None:
            origin = item.get("origin")
            if isinstance(origin, str) and _is_interpreter_module(name, origin, loader):
                continue
            if name == "__main__" and origin is None and loader == "NoneType":
                continue
            if name == "_cairnwork_blackbox_runner" and origin is None and loader == "NoneType":
                continue
            # cryptography's in-memory OpenSSL bridge is paired with its audited CFFI backend.
            if name == "_openssl" and origin is None and loader == "NoneType":
                continue
            else:
                raise ValueError(
                    f"runner module without file is not builtin or frozen: {_neutralize(name)}"
                )
        if not isinstance(filename, str):
            raise ValueError("invalid runner module file")
        origin = item.get("origin")
        if origin is not None and not isinstance(origin, str):
            raise ValueError(f"runner module origin missing: {_neutralize(name)}")
        _validate_reported_path_text(filename, name, "file")
        if isinstance(origin, str) and origin not in {"built-in", "frozen"}:
            _validate_reported_path_text(origin, name, "origin")
        path = _reported_path(filename, name, "file")
        origin_path = (
            _reported_path(origin, name, "origin")
            if isinstance(origin, str) and origin not in {"built-in", "frozen"}
            else None
        )
        named_path = expected_names.get(name)
        if named_path is not None and path != named_path:
            raise ValueError(f"module name served from wrong sealed file: {_neutralize(name)}")
        if expected is not None and path != expected.resolve():
            raise ValueError(f"pinned runner module origin mismatch: {_neutralize(name)}")
        if _is_under(path, sealed):
            _validate_sealed_module(name, path, origin, loader, all_expected, sealed)
        elif any(_is_under(path, root) for root in stdlib_roots) and not any(
            _is_under(path, root) for root in imports[2:]
        ):
            if origin != "frozen" and (origin_path is None or origin_path != path):
                raise ValueError(f"stdlib module origin disagrees with file: {_neutralize(name)}")
        elif any(_is_under(path, root) for root in imports):
            if origin is None and name.split(".")[0] == "cryptography":
                pass
            elif origin_path is None or origin_path != path:
                raise ValueError(
                    f"dependency module origin disagrees with file: {_neutralize(name)}"
                )
            top_level = name.split(".")[0]
            if top_level not in _THIRD_PARTY_TOP_LEVELS or top_level in expected_names:
                raise ValueError(f"unapproved dependency module: {_neutralize(name)}")
        else:
            raise ValueError(f"runner module outside allowed roots: {_neutralize(name)}")
    if any(name.startswith("_w1353_") for name in seen_names):
        raise ValueError("retired witness module was imported")
    if "_openssl" in seen_names and "_cffi_backend" not in seen_names:
        raise ValueError("in-memory OpenSSL bridge lacks its CFFI backend")


def _validate_child_reports(
    reports: Any,
    pins: dict[str, str],
    source_pins: dict[str, str],
    sealed: Path,
    roots: list[Path],
    imports: list[Path],
) -> None:
    if not isinstance(reports, list) or not reports:
        raise ValueError("child module-origin reports missing")
    for child in reports:
        if not isinstance(child, dict):
            raise ValueError("invalid child module-origin report")
        _validate_module_origins(child.get("modules"), pins, source_pins, sealed, roots, imports)


def _validate_sealed_module(name, path, origin, loader, expected_hashes, sealed):
    if not (
        (name == "__main__" and origin is None)
        or (
            isinstance(origin, str)
            and origin not in {"built-in", "frozen"}
            and _reported_path(origin, name, "origin") == path
        )
    ):
        raise ValueError(f"runner module origin disagrees with file: {_neutralize(name)}")
    if loader != "SourceFileLoader" and not (name == "__main__" and loader == "NoneType"):
        raise ValueError(f"sealed module is not source loaded: {_neutralize(name)}")
    relative = path.relative_to(sealed.resolve()).as_posix()
    if relative not in expected_hashes:
        raise ValueError(f"sealed module is not pinned: {_neutralize(name)}")
    if name != "__main__" and _module_name(relative) != name:
        raise ValueError(f"sealed module name disagrees with file: {_neutralize(name)}")
    top_level = name.split(".")[0]
    if top_level in _THIRD_PARTY_TOP_LEVELS:
        raise ValueError(f"third-party module loaded from sealed tree: {_neutralize(name)}")
    if relative.startswith("src/") and top_level not in _PRODUCT_TOP_LEVELS:
        raise ValueError(f"unapproved sealed product module: {_neutralize(name)}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_hashes.get(relative) != digest:
        raise ValueError(f"sealed module bytes changed: {_neutralize(name)}")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _module_name(relative):
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if parts[0] == "src":
        parts.pop(0)
        return ".".join(parts)
    if parts[0] == "tests":
        return parts[-1] if len(parts) == 2 and parts[1] != "attest" else ".".join(parts)
    if len(parts) == 2:
        return parts[-1]
    return ".".join(parts)


def sandbox_available():
    from cairnwork_sandbox import select

    return sys.platform == "darwin" and select() is not None


def c2_unavailable(rows):
    return [
        r["observed"] for r in rows if r["class_id"] == "C2" and r["probe"].startswith("verify-cli")
    ]


def blackbox_classification(status, reason):
    from pra1353_derive import blackbox_classification as classify

    return classify(status, reason)
