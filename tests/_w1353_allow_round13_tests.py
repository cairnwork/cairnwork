"""Round-thirteen syntax whitelist tests, re-exported by the witness suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _w1353_allow_syntax as syntax
from tests._w1353_allow import check
from tests._w1353_allow_round13_cases import SYNTAX_BLOCK, SYNTAX_MUTATION_IDS

LEGACY_CAUGHT_IDS = {
    "global_raw",
    "module_function_oracle",
    "nonlocal_raw",
    "two_raw_compare",
    "two_raw_expressions",
    "unpack_rebind",
}

__all__ = (
    "test_round_thirteen_syntax_rejects_new_escape_families",
    "test_round_thirteen_syntax_mutation_is_live",
    "test_round_thirteen_syntax_allows_benign_forms",
    "test_round_thirteen_error_fallback_is_observed_by_all_rows",
)


def _source(snippet: str) -> str:
    indented = "\n".join("    " + line if line else "" for line in snippet.splitlines())
    return (
        "from _w1353_probe import verify, row, blackbox_result\n"
        'def _fixture():\n    result = verify(1)\n    status = result.get("status")\n' + indented
    )


def _case_source(case_id):
    if case_id == "module_function_oracle":
        return "from _w1353_probe import verify\ndef _g():\n    r = verify(1)\n    return r\n"
    if case_id in {
        "default",
        "kwonly_default",
        "class_kwargs",
        "class_keyword",
    }:
        return "from _w1353_probe import verify\n" + SYNTAX_BLOCK[case_id]
    return _source(SYNTAX_BLOCK[case_id])


@pytest.mark.parametrize("case_id", SYNTAX_MUTATION_IDS)
def test_round_thirteen_syntax_rejects_new_escape_families(case_id):
    assert check(_case_source(case_id), "tests/_w1353_extra.py"), case_id


def test_round_thirteen_syntax_mutation_is_live(monkeypatch):
    original = syntax.syntax_findings
    monkeypatch.setattr(syntax, "syntax_findings", lambda tree: [])
    caught = {
        case_id
        for case_id in SYNTAX_MUTATION_IDS
        if check(_case_source(case_id), "tests/_w1353_extra.py")
    }
    monkeypatch.setattr(syntax, "syntax_findings", original)
    assert caught == LEGACY_CAUGHT_IDS


@pytest.mark.parametrize(
    "snippet",
    [
        'assert status == "TIMEOUT"',
        'assert "PASS" in verdict_first.reason',
        'assert valid.code == "OK"',
        'assert status in ("A", "B")',
        "assert result is not None",
        'row("C1", "x", "r", status, "FAILED", "REFUSED")',
        "def _g(x):\n    return x + 1\ndef run():\n    return _g(2)",
    ],
)
def test_round_thirteen_syntax_allows_benign_forms(snippet):
    source = (
        "def _g(x):\n    return x + 1\ndef _fixture():\n    return _g(2)\n"
        if snippet.startswith("def _g(")
        else _source(snippet)
    )
    assert not check(source, "tests/_w1353_extra.py")


def test_round_thirteen_error_fallback_is_observed_by_all_rows(monkeypatch, tmp_path):
    import importlib.util

    path = Path(__file__).with_name("_w1353_extra.py")
    spec = importlib.util.spec_from_file_location("_w1353_extra_round13", path)
    assert spec is not None and spec.loader is not None
    extra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extra)
    result = {"error": "USAGE", "detail": "x"}
    monkeypatch.setattr(extra, "verify", lambda *args, **kwargs: result)
    blackbox = extra._blackbox_row("C1", 1, tmp_path, tmp_path, "b", "c", tmp_path)
    monkeypatch.setattr(extra, "repo", lambda *args, **kwargs: ("b", "c"))
    mutant = extra._mutant_row("C1", tmp_path, tmp_path, 1, "src", "src", "test")
    monkeypatch.setattr(extra, "cli_route", lambda *args: "UNPROTECTED")
    strict_rows = extra._c7(tmp_path, tmp_path)
    strict = next(item for item in strict_rows if item["probe"] == "b-one-tampered-pytest")
    assert [blackbox["observed"], mutant["observed"], strict["observed"]] == [
        "USAGE",
        "USAGE",
        "USAGE",
    ]
