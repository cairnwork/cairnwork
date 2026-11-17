"""Round-eight pin namespace and attribution witnesses, imported by the entry suite."""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

from tests import _w1353_allow_pins as pins
from tests import _w1353_allow_rules as allow_rules

ROUND_EIGHT_GUARD_TEST_NAMES = frozenset(
    {
        "test_w1353_namespace_has_no_shadow_directories",
        "test_w1353_shadow_directory_mutation_control",
        "test_w1353_pin_modules_import_from_their_reviewed_files",
        "test_w1353_module_origin_rejects_foreign_sys_modules_entry",
        "test_w1353_witness_objects_come_from_pinned_modules",
    }
)


def _shadow_dirs(root):
    return [path for path in root.rglob("_w1353*") if path.is_dir()]


def _has_shadow_namespace(root):
    return bool(_shadow_dirs(root))


def _module_origin_matches(name, module, path):
    return (
        sys.modules.get(name) is module and Path(str(module.__file__)).resolve() == path.resolve()
    )


def test_w1353_namespace_has_no_shadow_directories(tmp_path):
    assert not _has_shadow_namespace(Path(__file__).parent)
    tests_dir = tmp_path / "tests"
    (tests_dir / "nested" / "_w1353_allow").mkdir(parents=True)
    assert _has_shadow_namespace(tests_dir)


def test_w1353_shadow_directory_mutation_control(tmp_path, monkeypatch):
    tests_dir = tmp_path / "tests"
    (tests_dir / "_w1353_allow").mkdir(parents=True)
    assert _has_shadow_namespace(tests_dir)
    monkeypatch.setattr(sys.modules[__name__], "_shadow_dirs", lambda _root: [])
    assert not _has_shadow_namespace(tests_dir)


def test_w1353_pin_modules_import_from_their_reviewed_files():
    module_names = {Path(path).stem for path in pins.TRUSTED_HELPER_PINS}
    module_names.add("_w1353_allow_pins")
    for short_name in sorted(module_names):
        name = "tests." + short_name
        module = importlib.import_module(name)
        expected = (Path(__file__).parent / (short_name + ".py")).resolve()
        assert Path(str(module.__file__)).resolve() == expected, name
        assert sys.modules[name] is module
        assert _module_origin_matches(name, module, expected), name


def test_w1353_module_origin_rejects_foreign_sys_modules_entry(monkeypatch, tmp_path):
    name = "tests._w1353_probe"
    foreign = type(sys)(name)
    foreign.__file__ = str(tmp_path / "_w1353_probe.py")
    monkeypatch.setitem(sys.modules, name, foreign)
    expected = Path(__file__).with_name("_w1353_probe.py")
    assert not _module_origin_matches(name, foreign, expected)


def test_w1353_witness_objects_come_from_pinned_modules():
    from tests import test_1039_1353_witnesses as witnesses

    expected = {
        "tests._w1353_allow": Path(__file__).with_name("_w1353_allow.py").resolve(),
        "tests._w1353_allow_pins": Path(__file__).with_name("_w1353_allow_pins.py").resolve(),
        "tests._w1353_allow_rules": Path(__file__).with_name("_w1353_allow_rules.py").resolve(),
    }
    objects = [
        witnesses.allow_check,
        pins.module_pin_mismatch,
        pins.raw_digest,
    ]
    for obj in objects:
        source = Path(str(inspect.getsourcefile(obj))).resolve()
        assert source == expected["tests." + obj.__module__.split(".")[-1]]
    assert Path(allow_rules.__file__).resolve() == expected["tests._w1353_allow_rules"]
    assert (
        Path(str(inspect.getsourcefile(allow_rules.scan))).resolve()
        == expected["tests._w1353_allow_rules"]
    )
    assert sys.modules["tests._w1353_allow_rules"] is allow_rules
    assert allow_rules.SINK_PINS is sys.modules["tests._w1353_allow_rules"].SINK_PINS


def test_witness_exposes_every_round_eight_guard():
    from tests import test_1039_1353_witnesses as witnesses

    exposed = {name for name in vars(witnesses) if name.startswith("test_w1353_")}
    assert exposed >= ROUND_EIGHT_GUARD_TEST_NAMES
