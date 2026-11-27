"""Focused pins for namespace-writing call shapes in deferred_attached._call_stores."""

import ast

import pytest

from cairnwork.deferred_attached import _call_stores


def _pairs(source: str) -> list[tuple[str | None, str]]:
    call = ast.parse(source, mode="eval").body
    assert isinstance(call, ast.Call)
    return [(name, ast.unparse(value)) for name, value in _call_stores(call)]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("globals().update({'test_a': a, 'test_b': b})", [("test_a", "a"), ("test_b", "b")]),
        (
            "globals().update({'test_a': a, 'test_b': b, 'test_c': c, "
            "'test_d': d, 'test_e': e, 'test_f': f})",
            [(f"test_{c}", c) for c in "abcdef"],
        ),
        ("globals().update(test_a=a, test_b=b)", [("test_a", "a"), ("test_b", "b")]),
        ("globals().update(other=a)", [("", "a")]),
    ],
)
def test_update_tracks_literal_keys_and_values(source, expected):
    assert _pairs(source) == expected


def test_update_expands_multiple_dictionary_items():
    source = (
        "globals().update({'test_a': a, 'test_b': b, 'test_c': c, "
        "'test_d': d, 'test_e': e, 'test_f': f})"
    )
    assert _pairs(source) == [(f"test_{c}", c) for c in "abcdef"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("globals().setdefault('test_x', value)", [("test_x", "value")]),
        ("globals().setdefault('test_x')", [("test_x", "globals().setdefault('test_x')")]),
        ("globals().setdefault()", [(None, "globals().setdefault()")]),
        ("ns['test_x'](value)", []),
    ],
)
def test_non_update_namespace_writes_capture_key_and_optional_value(source, expected):
    # The final row is deliberately outside the write-method grammar.
    assert _pairs(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("globals().__setitem__('test_x', value)", [("test_x", "value")]),
        ("globals().__setitem__('test_x')", [("test_x", "globals().__setitem__('test_x')")]),
        ("globals().__setitem__()", [(None, "globals().__setitem__()")]),
    ],
)
def test_setitem_tracks_key_and_optional_value(source, expected):
    assert _pairs(source) == expected


def test_update_positional_mapping_is_computed():
    assert _pairs("globals().update(mapping)") == [(None, "mapping")]


def test_builtin_calls_do_not_mark_arguments_as_namespace_writes():
    assert _pairs("dict(globals())") == []


def test_non_builtin_calls_treat_namespace_arguments_as_computed_writes():
    assert _pairs("helper(globals(), other)") == [(None, "globals()")]


def test_non_builtin_keywords_are_also_possible_writes():
    assert _pairs("helper(value=globals())") == [(None, "globals()")]
