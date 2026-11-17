"""Whole-module pin witnesses for the trusted helper boundary."""

from pathlib import Path

import pytest

from tests import _w1353_allow_round6_cases as cases
from tests._w1353_allow import check as allow_check
from tests._w1353_allow_pins import TRUSTED_HELPER_PINS, module_pin_mismatch, raw_digest


@pytest.mark.parametrize("path", TRUSTED_HELPER_PINS)
def test_trusted_helper_modules_match_reviewed_pins(path):
    source = Path(path).read_bytes()
    assert not module_pin_mismatch(source, path), (
        f"{path}: trusted witness helper changed: update the pin only with reviewer sign-off"
    )


def test_every_trusted_helper_pin_entry_exists():
    assert all(Path(path).is_file() for path in TRUSTED_HELPER_PINS)


def test_one_file_edit_has_exactly_one_mismatching_path(tmp_path):
    for path in TRUSTED_HELPER_PINS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(path).read_bytes())
    changed = next(iter(TRUSTED_HELPER_PINS))
    target = tmp_path / changed
    target.write_bytes(target.read_bytes() + b"# one-file edit\n")
    mismatches = [
        path
        for path in TRUSTED_HELPER_PINS
        if module_pin_mismatch((tmp_path / path).read_bytes(), path, tmp_path)
    ]
    assert mismatches == [changed]


@pytest.mark.parametrize("case_id", cases.ROUND_SIX_MUTATIONS)
def test_round_six_mutations_trip_whole_module_pin(case_id, monkeypatch, tmp_path):
    path, _anchor = cases.ROUND_SIX_MUTATIONS[case_id]
    original = Path(path).read_bytes()
    mutated = cases.mutate_case(original.decode("utf-8"), case_id).encode("utf-8")
    copy = tmp_path / Path(path).name
    copy.write_bytes(mutated)
    assert module_pin_mismatch(copy.read_bytes(), path), (
        f"{path}: trusted witness helper changed: update the pin only with reviewer sign-off"
    )
    if case_id == "c10_sys_modules_update":
        assert allow_check(copy.read_text(), path), case_id
    monkeypatch.setitem(TRUSTED_HELPER_PINS, path, raw_digest(mutated))
    assert not module_pin_mismatch(copy.read_bytes(), path), case_id


@pytest.mark.parametrize("mutation", [b"# -*- coding: latin-1 -*-\n", b"# comment-only edit\n"])
def test_byte_edits_trip_pin_and_name_the_file(mutation):
    path = "tests/_w1353_probe.py"
    source = Path(path).read_bytes()
    edited = mutation + source
    assert module_pin_mismatch(edited, path)
    with pytest.raises(
        AssertionError,
        match="trusted witness helper changed: update the pin with reviewer sign-off",
    ):
        assert not module_pin_mismatch(edited, path), (
            f"{path}: trusted witness helper changed: update the pin with reviewer sign-off"
        )


@pytest.mark.parametrize(
    "path",
    [
        "./tests/_w1353_probe.py",
        str(Path("tests/_w1353_probe.py").resolve()),
        "tests//_w1353_probe.py",
        "tests/_w1353_probe.py ",
    ],
)
def test_path_spellings_do_not_skip_pin_check(path):
    source = Path("tests/_w1353_probe.py").read_bytes() + b"# changed\n"
    assert module_pin_mismatch(source, path)


def test_missing_pinned_file_is_a_mismatch(tmp_path):
    path = "tests/_w1353_probe.py"
    assert module_pin_mismatch(b"", path, tmp_path)


def test_new_pin_helper_passes_witness_grammar():
    path = Path("tests/_w1353_allow_pins.py")
    assert not allow_check(path.read_text(), str(path))
