"""The collected-test split runs under a strict wall-clock limit."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import pytest
from conftest import commit_change
from test_p2p_nodes import CALC, CALC_TESTS, CONFTEST, MUL_TEST, SUB_OFF, _run

from cairnwork import p2p_collect
from cairnwork.deferred_tests import TestCommitRejected as Rejected


def _stub(code: str, *args: str) -> list[str]:
    return [sys.executable, "-c", code, *args]


def test_normal_classification_preserves_removed_and_dropped_sets():
    side = p2p_collect.classify(
        {"t.py::test_gone", "t.py::test_kept", "t.py::test_hidden"},
        {"t.py::test_kept"},
        {"t.py"},
        lambda _: "def test_kept(): pass\nif False:\n    def test_hidden(): pass\n",
        lambda _: "def test_gone(): pass\ndef test_kept(): pass\ndef test_hidden(): pass\n",
    )
    assert side.removed == {"t.py::test_gone"}
    assert side.dropped == {"t.py::test_hidden"}


def test_slow_child_is_killed_and_refused(monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    code = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)"
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code, str(pid_file)))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 0.1)
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"
    assert "time" in str(caught.value)
    child_pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(child_pid, 0)


def test_same_child_finishes_within_limit(monkeypatch):
    code = 'import time; time.sleep(.02); print(\'{"removed":[],"dropped":["t.py::test_a"]}\')'
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 1)
    side = p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_child_cannot_return_ids_outside_the_base(monkeypatch):
    code = 'print(\'{"removed":["t.py::extra"],"dropped":[]}\')'
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::test_a"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"


@pytest.mark.sandbox
def test_pipeline_analysis_timeout_is_refused_without_verification(monkeypatch, repo, tmp_path):
    code = "import time; time.sleep(30)"
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 0.1)
    commit_change(
        repo,
        {"src/calc.py": CALC, "tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC_TESTS},
    )
    check = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py::test_mul")),)
    result = _run(
        repo,
        tmp_path,
        ("src/calc.py",),
        {"src/calc.py": SUB_OFF},
        {"tests/conftest.py": CONFTEST, "tests/test_calc.py": CALC + MUL_TEST},
        check,
    )
    assert result.status == "FAILED" and not result.verified
    assert "TEST_COMMIT_ANALYSIS_LIMIT" in result.reason


_OK = '{"removed":["t.py::a"],"dropped":["t.py::b"]}'
_DEEP = 100_000


def _emit(text: str) -> str:
    return f"import sys; sys.stdout.write({text!r}); sys.stdout.flush(); "


_FAILURES: dict[str, str] = {
    "result-then-nonzero-exit": _emit(_OK) + "sys.exit(3)",
    "result-then-self-signal": _emit(_OK)
    + "import os,signal; os.kill(os.getpid(), signal.SIGKILL)",
    "result-padded-past-output-cap": _emit(_OK)
    + f"sys.stdout.write(' ' * ({p2p_collect.CLASSIFY_OUTPUT_CAP} + 10))",
    "deep-json-top-level": f"import sys; sys.stdout.write('[' * {_DEEP} + ']' * {_DEEP})",
    "deep-json-in-removed": "import sys; sys.stdout.write('{\"removed\":' + '[' * "
    f"{_DEEP} + ']' * {_DEEP} + ',\"dropped\":[]}}')",
    "missing-expected-id": _emit('{"removed":["t.py::a"],"dropped":[]}'),
    "id-not-in-base": _emit('{"removed":["t.py::a","t.py::z"],"dropped":["t.py::b"]}'),
    "id-in-both-lists": _emit('{"removed":["t.py::a","t.py::b"],"dropped":["t.py::b"]}'),
    "duplicate-id": _emit('{"removed":["t.py::a","t.py::a"],"dropped":["t.py::b"]}'),
    "non-string-member": _emit('{"removed":["t.py::a",1],"dropped":["t.py::b"]}'),
    "top-level-non-object": _emit("[]"),
    "extra-key": _emit('{"removed":["t.py::a"],"dropped":["t.py::b"],"x":[]}'),
    "non-utf8": "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')",
    "empty-output": "pass",
}


@pytest.mark.parametrize("code", _FAILURES.values(), ids=_FAILURES.keys())
def test_child_failure_modes_are_analysis_limit(monkeypatch, code):
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 20)
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::a", "t.py::b"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"


def test_timeout_kills_child_group_promptly(monkeypatch, tmp_path):
    pid_file = tmp_path / "grandchild"
    code = (
        "import subprocess,sys,time; "
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "open(sys.argv[1], 'w').write(str(g.pid)); time.sleep(60)"
    )
    monkeypatch.setattr(p2p_collect, "_classify_argv", lambda: _stub(code, str(pid_file)))
    monkeypatch.setattr(p2p_collect, "CLASSIFY_TIMEOUT_SECONDS", 1.5)
    started = time.monotonic()
    with pytest.raises(Rejected) as caught:
        p2p_collect.classify({"t.py::a"}, set(), set(), lambda _: "", lambda _: "")
    assert caught.value.code == "TEST_COMMIT_ANALYSIS_LIMIT"
    assert time.monotonic() - started < 4
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("grandchild survived the group kill")


_GONE = "def test_gone(): pass\ndef test_hidden(): pass\n"
_REAL_CASES: dict[str, tuple[set[str], Any, Any]] = {
    "removed-and-dropped-both-non-empty": (
        {"t.py::test_gone", "t.py::test_hidden"},
        {"t.py::test_gone"},
        {"t.py::test_hidden"},
    ),
    "empty-input": (set(), set(), set()),
}


@pytest.mark.parametrize("case", _REAL_CASES.values(), ids=_REAL_CASES.keys())
def test_real_child_runs_isolated(monkeypatch, tmp_path, case):
    base_ids, removed, dropped = case
    monkeypatch.setenv("CW_PARENT_SENTINEL", "leaked")
    real: list[str] = p2p_collect._classify_argv()
    probe = tmp_path / "probe.json"
    spy = (
        "import sys, os, json; "
        "open(sys.argv[2], 'w').write(json.dumps("
        "{'env': dict(os.environ), 'isolated': sys.flags.isolated, 'path0': sys.path[0]})); "
        f"exec({real[5]!r})"
    )
    monkeypatch.setattr(
        p2p_collect, "_classify_argv", lambda: [*real[:5], spy, real[6], str(probe)]
    )
    side = p2p_collect.classify(
        base_ids,
        {"t.py::test_kept"} if base_ids else set(),
        {"t.py"},
        lambda _: "if False:\n    def test_hidden(): pass\n",
        lambda _: _GONE,
    )
    assert (side.removed, side.dropped) == (removed, dropped)
    seen = json.loads(probe.read_text())
    assert "CW_PARENT_SENTINEL" not in seen["env"] and "PATH" not in seen["env"]
    assert seen["isolated"] == 1
    assert real[1] == "-I"
