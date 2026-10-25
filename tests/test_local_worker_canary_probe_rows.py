"""#88: a probe whose output has a row without a kind (a Python 3.14 pyroot crashed the canary
with KeyError: 'kind') is a failed probe run with a clear row, never a crash and never a pass."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from experimental.local_worker import canary, escapes, probe


class _Tools:
    mode = "direct"

    def __init__(self, lines: list[str], exit_code: int = 0):
        self.output = "\n".join(lines)
        self.exit_code = exit_code

    def write_file(self, *_a):
        return None

    def run_tests(self, _args):
        return SimpleNamespace(output=self.output, exit_code=self.exit_code)


def _row(**fields) -> str:
    return probe.MARK + json.dumps(fields)


def _run(monkeypatch, lines: list[str]):
    monkeypatch.setattr(escapes, "start_sibling_victim", lambda *a: (None, 0))
    monkeypatch.setattr(probe, "source", lambda *a: "")
    fx = SimpleNamespace(
        read_targets={}, write_targets={}, run_root=SimpleNamespace(name="r" * 8),
        escape_markers={}, secret="s3cret",
    )  # fmt: skip
    return canary._code_probes(cast(Any, _Tools(lines)), cast(Any, fx))


def test_a_well_formed_probe_run_passes(monkeypatch):
    rows, identity, run = _run(
        monkeypatch,
        [_row(kind="identity", uid=1), _row(kind="read", probe="p", **{"pass": True})],
    )
    assert run["pass"] and run["malformed_rows"] == 0
    assert identity["uid"] == 1 and [r["kind"] for r in rows] == ["read"]


@pytest.mark.parametrize(
    "bad",
    [_row(probe="x", **{"pass": True}), probe.MARK + "[1]", probe.MARK + "{broken", _row(kind=3)]
    + [_row(kind="read", **{"pass": True}), _row(kind="read", probe="p"), _row(kind="net")]
    + [_row(kind="read", probe="p", **{"pass": "yes"}), _row(kind="read", probe=1, **{"pass": 1})],
)
def test_a_row_without_a_kind_fails_the_run_instead_of_crashing(monkeypatch, bad):
    rows, identity, run = _run(monkeypatch, [_row(kind="identity", uid=1), bad])
    assert identity and rows == []
    assert run["pass"] is False and run["malformed_rows"] == 1


def test_a_probe_that_prints_nothing_fails_the_run(monkeypatch):
    rows, identity, run = _run(monkeypatch, ["collected 0 items"])
    assert rows == [] and identity == {} and run["malformed_rows"] == 0
    assert run["pass"] is False  # no identity row: the probe did not run


def test_a_check_needs_pass_to_be_true_not_just_truthy():
    good = {"probe": "p", "pass": True}
    assert canary._check([good])["pass"] is True
    assert canary._check([good, {"probe": "q", "pass": 1}])["pass"] is False
    assert canary._check([{"probe": "q", "pass": "yes"}])["pass"] is False
    assert canary._check([])["pass"] is False
