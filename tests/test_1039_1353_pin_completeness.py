"""Every witness-guard helper on disk must be pinned (closes the _w1353_allow* prefix gap).

The witness scan in test_1039_1353_witnesses.py skips files whose name starts with
``_w1353_allow``. That exclusion is only safe if every such file is also pinned by raw bytes.
This test fails if an unpinned ``_w1353*.py`` file appears, whatever its name.
"""

from __future__ import annotations

from pathlib import Path

from tests._w1353_allow_pins import TRUSTED_HELPER_PINS

# The pin table itself is the documented residual (review-only); it cannot pin itself.
_SELF_PIN_EXCEPTIONS = {"_w1353_allow_pins.py"}


def test_every_w1353_file_on_disk_is_pinned_or_the_pin_table() -> None:
    tests_dir = Path(__file__).parent
    on_disk = {path.name for path in tests_dir.glob("_w1353*.py")}
    pinned = {Path(key).name for key in TRUSTED_HELPER_PINS}
    unpinned = on_disk - pinned - _SELF_PIN_EXCEPTIONS
    assert not unpinned, f"unpinned witness-guard files: {sorted(unpinned)}"


def test_every_pinned_file_exists() -> None:
    tests_dir = Path(__file__).parent
    missing = [key for key in TRUSTED_HELPER_PINS if not (tests_dir.parent / key).is_file()]
    assert not missing, f"pinned files missing on disk: {missing}"
