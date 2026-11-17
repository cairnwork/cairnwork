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
            "tests/_w" + "1353_allow_imports.py",
            "tests/_w" + "1353_allow_cases.py",
            "tests/_w" + "1353_allow_round5_tests.py",
            "tests/_w" + "1353_allow_round6_cases.py",
            "tests/_w" + "1353_allow_round6_tests.py",
            "tests/_w" + "1353_allow_round8_tests.py",
        ),
        (
            "8a281fd5192cd98b52e16d1728dc621348de40d0d202a15401dcd14c90ac09b9",
            "e4c16f7c9a1d2bde8f08b68ad79886d090aae43a19c5d5114f46fafc4237fe26",
            "d7037a229da0505dfa6efef6612fc4ffb66187198f2890cad7ea11cb4a32ad8f",
            "ef0595ac58c0d1a1cb2a4972af2d9be95c80176288b467cb4e5808f26d9a34c4",
            "3482d0c6e5e22e76998a93decd544f11ba4f0d7eacb2dbc6f27c04ec1599825b",
            "40f0025a17f61835d80a107714ad1184ace0616c472390e071d283a4279b5a18",
            "b3bfc8f955f0399c4e5e6678fe49a02cd23add2e489283d63bd17104775ba9f1",
            "ab5bae57b89a9da25bc3cd8e42d1b0e900b8769bfbae8c195e8f41e23c8a1ea5",
            "0456029e68ba0189373555238e01e10b12b4e104459a589306dbaa5379cbc5f5",
            "640f1ce2f341f9a669de074a7334d5b41458722ecbdccaa3bbbf73752e547985",
            "146679d35fa217b4aaf0b36076ca3376efedec58e259880acddb30b9a1e7210d",
            "62fecb353f61bcdaa20a3aa5acff633d39f267b9848760107f6f6837fbf942d2",
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
