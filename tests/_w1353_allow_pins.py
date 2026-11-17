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
            "tests/_w" + "1353_allow_syntax.py",
            "tests/_w" + "1353_allow_round13_cases.py",
            "tests/_w" + "1353_allow_round13_tests.py",
        ),
        (
            "233dac858b05bfaf3c0f4804ca12b8b2f8965919dc0506f60d3521f8e44eb62b",
            "d1b6aa85b28fe7108403a4f98f7bfd27337d6f500b738dc566f5d2879a1cb0b6",
            "78790cc519650a3ee3e0f55e92a9699e570e91ea0b84ac5efcb27e4d6d78d647",
            "ef0595ac58c0d1a1cb2a4972af2d9be95c80176288b467cb4e5808f26d9a34c4",
            "0c6890ce90e9acf28ebdb777a8cfd866c523085ae45365a87840c1c6901b6ce5",
            "89f014c8f094b8456bf16f8d58592a8e07e80e1ec34d50ef29343a47eb57e062",
            "b163281f6ec0491e8dea310fdc372d5128be1a53316a66f1020b2679442a461c",
            "46201462bb09cf3eeb29460417b65c7d65b69d3f55c10e7c8983e5d52e5f568a",
            "ab5bae57b89a9da25bc3cd8e42d1b0e900b8769bfbae8c195e8f41e23c8a1ea5",
            "7cd936b81039387d982fb8bf54c21ef2ceca09402319a2c30d5e618c48105ad7",
            "640f1ce2f341f9a669de074a7334d5b41458722ecbdccaa3bbbf73752e547985",
            "146679d35fa217b4aaf0b36076ca3376efedec58e259880acddb30b9a1e7210d",
            "62fecb353f61bcdaa20a3aa5acff633d39f267b9848760107f6f6837fbf942d2",
            "40b2b11f1ab71b9f7d7bc62d9ed377d0200c77639c38ddc90bdced44dd1ce409",
            "79985fdb7be72c683420e8fd79e6245d87e9562a8295b98e5fcbdd04fd1759c4",
            "6a0fef7ba32e4ecd1a49ba85b7590f8d3373babba215a74e9160f2fae376f521",
            "69864b0c2a4aa57960338b71c78d07917d19f51cd3d2c54beee902f66797592e",
            "6a09dde430b5eb4179fba10ecfabd043d76d59042fa38d2972b5633b304cad01",
            "cd06326270b093b55dfbb2433daf25ae60a51040c8250f63afafa2c4dbe68422",
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
