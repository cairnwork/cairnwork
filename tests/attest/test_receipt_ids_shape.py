"""#445: offline verify refuses required verifier ids that Core itself would refuse.

nexus-core's ``_require_ids`` (product/evidence) refuses, for the contract's and for the plan's
``required_verifier_ids`` alike, a list that is empty, names an id twice, or holds an id that is
empty, padded with whitespace or carries a NUL. Core never answers VERIFIED to such a request, so a
receipt with one is not its verdict: RECEIPT_INCONSISTENT, as for the other #440 checks. Comparing
the two lists as sets alone let these by. Ids Core accepts (inner spaces, non-ASCII, any order)
still verify: the rule matches Core's, it is not stricter. Synthetic receipts, re-signed with the
producer's key after a fake Core said VERIFIED (a buggy producer), as in test_receipt_consistency.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.attest.test_receipt_consistency import Edit, _chained, _plan_ids, _v01, _v02

INCONSISTENT = "RECEIPT_INCONSISTENT"


def _ids(contract: Any, plan: Any) -> Edit:
    def edit(predicate: dict[str, Any]) -> None:
        predicate["request"]["acceptance_contract"]["required_verifier_ids"] = contract
        predicate["request"]["verification_plan"]["required_verifier_ids"] = plan
        _chained(predicate["request"])

    return edit


def _refused(out: Any) -> None:
    assert out.code == INCONSISTENT, out
    assert "required_verifier_ids" in out.detail, out


DUPLICATED = ["pytest", "pytest"]
BOTH_REFUSED = {  # the same list in contract and plan, so the sets agree
    "duplicated": DUPLICATED,
    "empty": [],
    "leading_space": [" pytest"],
    "trailing_space": ["pytest "],
    "empty_id": [""],
    "nul": ["pytest\x00"],
}


@pytest.mark.parametrize("ids", BOTH_REFUSED.values(), ids=BOTH_REFUSED.keys())
def test_ids_core_refuses_in_both_lists_refuse_v01(tmp_path, repo, ids):
    _refused(_v01(tmp_path, repo, _ids(ids, list(ids))))


V02 = {k: BOTH_REFUSED[k] for k in ("duplicated", "empty", "leading_space")}


@pytest.mark.parametrize("ids", V02.values(), ids=V02.keys())
def test_ids_core_refuses_in_both_lists_refuse_v02(tmp_path, ids):
    _refused(_v02(tmp_path, _ids(ids, list(ids)), ("pytest-0",)))


def test_a_duplicate_in_the_contract_only_refuses(tmp_path, repo):
    """The sets agree ({pytest}); Core refuses the contract's list on its own."""
    _refused(_v01(tmp_path, repo, _ids(DUPLICATED, ["pytest"])))


def test_a_duplicate_in_the_plan_only_refuses(tmp_path, repo):
    """Each list is checked on its own, not only the first one or the union."""
    _refused(_v01(tmp_path, repo, _plan_ids(DUPLICATED)))


@pytest.mark.parametrize("ids", ["pytest", {"pytest": 1}], ids=repr)
def test_ids_that_are_not_a_list_refuse_without_raising(tmp_path, repo, ids):
    _refused(_v01(tmp_path, repo, _ids(ids, ids)))


@pytest.mark.parametrize("ids", [["py test"], ["pytést"]], ids=repr)
def test_ids_core_accepts_still_verify(tmp_path, repo, ids):
    """Control: inner spaces and non-ASCII are normalized text to Core; not stricter than Core."""
    out = _v01(tmp_path, repo, _ids(ids, list(ids)))
    assert out.code == "OK", out


def test_the_unedited_receipt_verifies_v01(tmp_path, repo):
    """Control: an honest producer's receipt, re-chained and re-signed unchanged."""
    assert _v01(tmp_path, repo, _chained_only).code == "OK"


def test_the_unedited_receipt_verifies_v02(tmp_path):
    assert _v02(tmp_path, _chained_only, ("pytest-0", "pytest-1")).code == "OK"


def _chained_only(predicate: dict[str, Any]) -> None:
    _chained(predicate["request"])
