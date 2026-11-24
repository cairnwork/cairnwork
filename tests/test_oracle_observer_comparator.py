"""Oracle comparator and intake (Issue #1039 step 2): AC3 and AC8, no sandbox needed."""

import json

import pytest

from experimental.oracle_observer import FAIL, MISMATCH, NO_OUTPUT, OUTPUT_INVALID, PASS
from experimental.oracle_observer.comparator import Invalid, parse_document, parse_strict
from experimental.oracle_observer.expected import Expected, Refused, load_expected, strong_keys
from experimental.oracle_observer.run import ProbeRun, judge

EXP = Expected({"k": 600, "name": "alpha"}, {"r": "builtins.KeyError"}, {})


def run_with(output, code=0, timed_out=False, survivors=0):
    return ProbeRun(code, timed_out, survivors, output)


def doc(values=None, raises=None) -> bytes:
    v = {"k": 600, "name": "alpha"} if values is None else values
    r = {"r": "builtins.KeyError"} if raises is None else raises
    return json.dumps({"v": v, "r": r}).encode()


def test_a_matching_document_passes_and_a_wrong_value_is_a_mismatch():
    assert judge(run_with(doc()), EXP).verdict == PASS
    wrong = judge(run_with(doc({"k": 601, "name": "alpha"})), EXP)
    assert (wrong.verdict, wrong.code) == (FAIL, MISMATCH)


@pytest.mark.parametrize(
    "values",
    [
        {"k": 600.0, "name": "alpha"},  # int vs float
        {"k": True, "name": "alpha"},
        {"k": 600},  # missing key
        {"k": 600, "name": "alpha", "extra": 1},  # extra key
    ],
)
def test_a_type_or_key_set_difference_never_passes(values):
    assert judge(run_with(doc(values)), EXP).verdict == FAIL


def test_bool_int_and_float_never_equal_each_other():
    exp = Expected({"a": 1, "b": 1.0, "c": True}, {}, {})
    for values in (
        {"a": True, "b": 1.0, "c": True},
        {"a": 1, "b": 1, "c": True},
        {"a": 1, "b": 1.0, "c": 1},
    ):
        assert judge(run_with(doc(values, {})), exp).verdict == FAIL
    assert judge(run_with(doc({"a": 1, "b": 1.0, "c": True}, {})), exp).verdict == PASS


@pytest.mark.parametrize(
    "raw",
    [
        b'{"v": {"a": 1, "a": 2}, "r": {}}',  # duplicate key at the top
        b'{"v": {"a": {"b": 1, "b": 2}}, "r": {}}',  # nested
        b'{"v": {"a": NaN}, "r": {}}',
        b'{"v": {"a": Infinity}, "r": {}}',
        b'{"v": {"a": -Infinity}, "r": {}}',
        b'{"v": {"a": 1e999}, "r": {}}',
        b'{"v": {"a": ' + b"1" * 65 + b'}, "r": {}}',  # 65-digit int
        b'{"v": {"a": ' + b"[" * 33 + b"]" * 33 + b'}, "r": {}}',  # nesting depth 33 below v
        b'{"v": {}, "r": {}} x',  # trailing bytes
        b'{"v": {}, "r": {}}{"v": {}, "r": {}}',
        b"[1]",
        b'{"v": {}}',
        b"\xff",
        b"x" * ((1 << 20) + 1),  # over the size cap
        b"[" * 200000,  # recursion bomb
    ],
)
def test_an_invalid_document_is_output_invalid(raw):
    result = judge(run_with(raw), Expected({"a": 1}, {}, {}))
    assert (result.verdict, result.code) == (FAIL, OUTPUT_INVALID)


def test_the_depth_and_digit_limits_are_exact():
    parse_strict(b'{"v": ' + b"[" * 31 + b"]" * 31 + b"}")  # depth 32 in all
    with pytest.raises(Invalid):
        parse_strict(b'{"v": ' + b"[" * 32 + b"]" * 32 + b"}")
    assert parse_strict(b"1" * 64) == int("1" * 64)
    with pytest.raises(Invalid):
        parse_strict(b"-" + b"1" * 65)


@pytest.mark.parametrize(
    "kwargs",
    [{"code": 1}, {"code": -9}, {"code": None}, {"timed_out": True}, {"survivors": 1}],
)
def test_a_run_that_did_not_end_cleanly_is_no_output_even_with_a_correct_document(kwargs):
    result = judge(run_with(doc(), **kwargs), EXP)
    assert (result.verdict, result.code) == (FAIL, NO_OUTPUT)


@pytest.mark.parametrize("output", [None, b""])
def test_no_document_is_no_output(output):
    assert judge(run_with(output), EXP).code == NO_OUTPUT


def test_a_float_compares_within_its_absolute_tolerance_only():
    exp = Expected({"x": 1.0, "n": 600}, {}, {"x": 0.01})
    close = doc({"x": 1.005, "n": 600}, {})
    far = doc({"x": 1.02, "n": 600}, {})
    assert judge(run_with(close), exp).verdict == PASS
    assert judge(run_with(far), exp).verdict == FAIL
    assert judge(run_with(doc({"x": 1, "n": 600}, {})), exp).verdict == FAIL  # int is not a float
    assert judge(run_with(doc({"x": 1.0, "n": 600.0}, {})), exp).verdict == FAIL


def test_negative_zero_equals_zero_for_a_float_with_a_tolerance():
    exp = Expected({"x": 0.0, "n": 600}, {}, {"x": 0.0})
    assert judge(run_with(doc({"x": -0.0, "n": 600}, {})), exp).verdict == PASS


@pytest.mark.parametrize("tolerance", [-1, -0.0001, 1e999, "0.1", True, None, [1]])
def test_a_bad_tolerance_is_refused_at_intake(tolerance):
    raw = json.dumps({"values": {"k": 600, "x": 1.0}, "tolerance": {"x": tolerance}})
    with pytest.raises(Refused):
        load_expected(raw.encode())


def test_a_tolerance_for_an_unknown_key_is_refused():
    raw = json.dumps({"values": {"k": 600}, "tolerance": {"nope": 0.1}})
    with pytest.raises(Refused):
        load_expected(raw.encode())


@pytest.mark.parametrize(
    "values",
    [
        {"a": True},
        {"a": None},
        {"a": 16, "b": -16, "c": 0},
        {"a": "x", "b": ""},
        {"a": [], "b": {}},
        {"a": True, "b": [], "c": 3},
    ],
)
def test_a_target_with_only_weak_keys_is_refused_at_intake(values):
    with pytest.raises(Refused, match="weak"):
        load_expected(json.dumps({"values": values}).encode())


def test_an_observe_raises_key_is_never_strong_and_strong_values_are():
    raw = json.dumps(
        {
            "values": {"a": 17, "b": "ab", "c": [0], "d": 0.5, "e": -17, "f": True},
            "raises": {"r": "builtins.KeyError"},
        }
    )
    assert set(strong_keys(load_expected(raw.encode()))) == {"a", "b", "c", "d", "e"}
    only_raises = json.dumps({"values": {}, "raises": {"r": "builtins.KeyError"}})
    with pytest.raises(Refused):
        load_expected(only_raises.encode())


def test_the_expected_file_is_parsed_as_strictly_as_the_output():
    for raw in (
        b'{"values": {"a": 100, "a": 200}}',
        b'{"values": {"a": NaN}}',
        b'{"values": {"a": 100}} x',
    ):
        with pytest.raises(Refused):
            load_expected(raw)


def test_parse_document_rejects_a_key_in_both_maps():
    with pytest.raises(Invalid):
        parse_document(b'{"v": {"a": 1}, "r": {"a": "x"}}')
