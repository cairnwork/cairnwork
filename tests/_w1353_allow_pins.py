"""Raw-byte pins for trusted witness and checker helpers.

Residual (review-only): (1) a PR that edits a pinned file together with its pin; (2) edits to this
pin table itself; (3) edits to test harness configuration (tests/conftest.py, pytest plugins,
pyproject pytest settings); or (4) edits to the unpinned entry test file
tests/test_1039_1353_witnesses.py (it is the file that runs the pin checks, so it cannot pin itself)
are caught only by review; the pin failure message names the file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# Hashes cover each file's exact bytes. This table is the documented residual.
TRUSTED_HELPER_PINS: dict[str, str] = dict(
    zip(
        (
            "tests/_w" + "1353_probe.py",
            "tests/_w" + "1353_extra.py",
            "tests/_w" + "1353_receipt.py",
            "tests/_w" + "1353_c10.py",
            "tests/_w" + "1353_allow.py",
            "tests/_w" + "1353_allow_rules.py",
            "tests/_w" + "1353_allow_alias.py",
            "tests/_w" + "1353_allow_imports.py",
            "tests/_w" + "1353_allow_cases.py",
            "tests/_w" + "1353_allow_round5_tests.py",
            "tests/_w" + "1353_allow_round6_cases.py",
            "tests/_w" + "1353_allow_round6_tests.py",
            "tests/_w" + "1353_allow_round8_tests.py",
            "tests/_w" + "1353_allow_positions.py",
            "tests/_w" + "1353_allow_round10_cases.py",
            "tests/_w" + "1353_allow_round10_tests.py",
        ),
        (
            "4817f0bf2f697ad21f087548823a03b451f30b3a22e7ddf8cbbad3bdc62cb619",
            "ad811d6ab87b26335c3ce4e10a628b6eef6ff3fd5d2741225da4c71c27e841f7",
            "028a34af1f8145fa60a6d4294b2fc284263975b5b9f6715cee8a51a311859cf2",
            "ef0595ac58c0d1a1cb2a4972af2d9be95c80176288b467cb4e5808f26d9a34c4",
            "5c4a28740faf71c029c792ae1ca7a0a7ae6057151b719f651ce0d82ce8f94d46",
            "89f014c8f094b8456bf16f8d58592a8e07e80e1ec34d50ef29343a47eb57e062",
            "b163281f6ec0491e8dea310fdc372d5128be1a53316a66f1020b2679442a461c",
            "46201462bb09cf3eeb29460417b65c7d65b69d3f55c10e7c8983e5d52e5f568a",
            "ab5bae57b89a9da25bc3cd8e42d1b0e900b8769bfbae8c195e8f41e23c8a1ea5",
            "0456029e68ba0189373555238e01e10b12b4e104459a589306dbaa5379cbc5f5",
            "640f1ce2f341f9a669de074a7334d5b41458722ecbdccaa3bbbf73752e547985",
            "146679d35fa217b4aaf0b36076ca3376efedec58e259880acddb30b9a1e7210d",
            "62fecb353f61bcdaa20a3aa5acff633d39f267b9848760107f6f6837fbf942d2",
            "40b2b11f1ab71b9f7d7bc62d9ed377d0200c77639c38ddc90bdced44dd1ce409",
            "79985fdb7be72c683420e8fd79e6245d87e9562a8295b98e5fcbdd04fd1759c4",
            "0aba5d68c773e43f9c78a6931cbb3824e5003372f90eeeab34d4d777603ce21a",
        ),
        strict=True,
    )
)


def raw_digest(source: Any) -> str:
    return hashlib.sha256(source).hexdigest()


def _resolved_key(path: str | Path, root: Path) -> Path:
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def module_pin_mismatch(source: Any, path: str | Path, root: Path | None = None) -> bool:
    repo_root = (root or Path.cwd()).resolve()
    target = _resolved_key(path, repo_root)
    resolved_pins = {
        _resolved_key(key, repo_root): (key, digest) for key, digest in TRUSTED_HELPER_PINS.items()
    }
    if target not in resolved_pins:
        return True
    key, expected = resolved_pins[target]
    pinned_path = (repo_root / key).resolve()
    return not pinned_path.is_file() or raw_digest(source) != expected
