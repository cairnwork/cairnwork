"""Alias-tracking cases for the closed witness grammar (review of PR #1411 head 2d96c49f).

Each must-block snippet is inserted into _normal_row in tests/_w1353_extra.py right after the
verify() call. The witness checker must report a finding. Each must-pass snippet is a benign
local pattern the grammar must keep allowing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._w1353_allow import check as allow_check

_EXTRA = (Path(__file__).parent / "_w1353_extra.py").read_text(encoding="utf-8")
_ANCHOR = "    result = verify(root, state, base, candidate)\n"

MUST_BLOCK = {
    "alias-subscript-store": ["alias = result", 'alias["status"] = "VERIFIED"'],
    "alias-update": ["alias = result", 'alias.update(status="VERIFIED")'],
    "list-box-subscript-store": ["box = [result]", 'box[0]["status"] = "VERIFIED"'],
    "walrus-alias-subscript-store": ['(alias := result)["status"] = "VERIFIED"'],
    "alias-ior": ["alias = result", 'alias |= {"status": "VERIFIED"}'],
    "module-alias-attr-store": ["m = verify_cli", "m.SOME_GLOBAL = None"],
    "from-imported-module-attr-store": ["blackbox.parse = json.loads"],
}

MUST_PASS = {
    "local-dict-subscript-store": ["box = {}", 'box["k"] = 1'],
    "reason-read-via-get": ['reason = result.get("reason") or ""'],
}


def _with(snippet: list[str]) -> str:
    assert _ANCHOR in _EXTRA, "anchor line missing from _w1353_extra.py"
    body = "".join(f"    {line}\n" for line in snippet)
    return _EXTRA.replace(_ANCHOR, _ANCHOR + body, 1)


@pytest.mark.parametrize("name", sorted(MUST_BLOCK))
def test_alias_grammar_must_block(name: str) -> None:
    assert allow_check(_with(MUST_BLOCK[name]), "tests/_w1353_extra.py"), name


@pytest.mark.parametrize("name", sorted(MUST_PASS))
def test_alias_grammar_must_pass(name: str) -> None:
    assert not allow_check(_with(MUST_PASS[name]), "tests/_w1353_extra.py"), name
