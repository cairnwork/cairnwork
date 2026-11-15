"""Test-strength sweep 2: provenance.py survivors.

A vouched field is one plain ASCII token; the commit message is read back only when it is
exactly the five-line shape Cairnwork writes, and a misparse must never produce a value.
"""

from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cairnwork import provenance

SUBJ = "Cairnwork attempt abc"


def _msg(w="w1", r="m1", o="m2", subject=SUBJ):
    return f"{subject}\n\nWorker: {w}\nRequested-Model: {r}\nObserved-Model: {o}\n"


@pytest.mark.parametrize("bad", ["x\n", "K", "ſ", "café", ".x", "_x", "a b", "", "-x"])
def test_odd_values_are_unknown(bad):
    assert provenance.clean(bad) == provenance.UNKNOWN


@settings(
    max_examples=200,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(st.text(max_size=100))
def test_clean_only_passes_plain_ascii_tokens(value):
    out = provenance.clean(value)
    if out != provenance.UNKNOWN:
        assert out == value and value.isascii() and "\n" not in value and 1 <= len(value) <= 80
        assert value[0].isalnum()


def test_length_boundary():
    assert provenance.clean("a" * 80) == "a" * 80
    assert provenance.clean("a" * 81) == provenance.UNKNOWN


@pytest.mark.parametrize(
    "message",
    [
        _msg(subject="x Cairnwork attempt y"),
        _msg(subject="Cairnwork attempts"),
        _msg().replace("Worker: w1", "Worker:w1"),
        _msg().replace("Worker: w1", "xWorker: w1"),
        _msg().replace("Worker: w1", "Foo Worker: w1"),
    ],
)
def test_a_message_not_in_exact_shape_is_all_unknown(message):
    assert provenance.parse(message) == dict(provenance.UNKNOWN3)


def test_the_exact_shape_round_trips():
    assert provenance.parse(_msg()) == {
        "worker": "w1",
        "requested_model": "m1",
        "observed_model": "m2",
    }
    assert provenance.parse(
        SUBJ + provenance.trailers(provenance.make("w1", "m1", "m2"))
    ) == provenance.parse(_msg())


def test_the_unknown_result_is_a_fresh_dict():
    first = provenance.parse("a\rb")
    first["worker"] = "forged"
    assert provenance.parse("a\rb")["worker"] == provenance.UNKNOWN


def test_non_dict_info_is_unknown_not_a_crash():
    assert provenance.trailers(cast(Any, "worker")) == provenance.trailers(None)
    assert provenance.trailers(cast(Any, ["x"])) == provenance.trailers({})
