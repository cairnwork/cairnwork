"""The host grade of a verdict comes from the binding's record, not from the host that reads it
(#709). Both grades are pinned with the platform check forced either way, so a `_level` that
ignores the binding fails on macOS and on Linux alike (before this, only Linux CI caught it)."""

import pytest

from cairnwork import protection
from cairnwork.notary import _level

IDS = ["blackbox:x", "pytest"]
REQUEST = {"acceptance_contract": {"required_verifier_ids": IDS}}


@pytest.mark.parametrize("reader_is_macos", [True, False])
@pytest.mark.parametrize("recorded", ["partial", "partial_macos"])
def test_the_binding_host_grade_wins_over_the_reading_host(monkeypatch, reader_is_macos, recorded):
    monkeypatch.setattr(protection, "on_macos", lambda: reader_is_macos)
    assert _level(REQUEST, {"protection": recorded}) == recorded
