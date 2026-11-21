"""Python 3.11 tokenizer compatibility for valid identifier characters (#612)."""

import ast
import sys

import pytest

from cairnwork.tamper_scan_tokens import file_views


@pytest.mark.parametrize("source", ["x·y = 1\n", "℘ = 1\n", "ᢅ = 1\n"])
def test_compilable_identifier_characters_have_lexical_views(source: str) -> None:
    ast.parse(source)
    views = file_views(source.encode())

    assert views is not None
    assert views[(1, 0)].code == source.rstrip("\n")


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="3.12+ tokenizes this source")
def test_other_error_tokens_still_fail_closed() -> None:
    assert file_views("x = 😀\n".encode()) is None
