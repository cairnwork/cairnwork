"""#850 PR2: real verifier runs (Seatbelt) with a declared test data file (data_exec_hook.py).
No producer declares data yet, so the run's plugin is installed with ``data_paths`` here."""

import sys
from functools import partial

import pytest
from conftest import commit_change

from cairnwork import report_guard
from cairnwork.deferred_tests import base_comparison
from cairnwork.verifiers import VerifierSpec, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox
DATA = "tests/data/x.py"
HEAD = (
    "import importlib, os, runpy, subprocess, sys, types\n"
    "import pytest\n"
    f"P = {DATA!r}\n"
    "ABS = os.path.abspath(P)\n"
    "def refused(run):\n"
    "    with pytest.raises(BaseException) as e:\n"
    "        run()\n"
    "    assert type(e.value).__name__ == 'DataExecRefused', e.value\n"
    "    assert not isinstance(e.value, Exception)\n"
    "def load(entry):\n"
    "    sys.modules.pop('x', None)\n"
    "    sys.path.insert(0, entry)\n"
    "    importlib.invalidate_caches()\n"
    "    try:\n"
    "        importlib.import_module('x')\n"
    "    finally:\n"
    "        sys.path.remove(entry)\n"
    "def source(name):\n"
    "    return compile(open(P).read(), name, 'exec')\n"
)
SPELLINGS = {
    "rel": "tests/data/x.py",
    "dot": "./tests/data/x.py",
    "abs": "ABS",
    "case": "Tests/DATA/X.py",  # macOS: the file system folds case too
    "dslash": "tests//data/x.py",
}
WAYS = {
    "import": "refused(lambda: load(os.path.dirname({p})))",
    "runpy": "refused(lambda: runpy.run_path({p}))",
    "exec": "refused(lambda: exec(source({p}), {{}}))",
    "function": "refused(lambda: types.FunctionType(source({p}), {{}}))",
}


def _name(spelling):
    return spelling if spelling == "ABS" else repr(spelling)


def variants() -> str:
    return "".join(
        f"def test_{way}_{key}():\n    {line.format(p=_name(spelling))}\n"
        for way, line in WAYS.items()
        for key, spelling in SPELLINGS.items()
    )


def verify(repo, tmp_path, monkeypatch, test_text, data=(DATA,)):
    """One sandboxed verifier run of tests/test_d.py with ``data`` declared."""
    monkeypatch.setattr(
        report_guard, "install_plugin", partial(report_guard.install_plugin, data_paths=data)
    )
    head = commit_change(repo, {"tests/test_d.py": test_text, DATA: "X = 1\n"})
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    (run,) = run_verifiers(ctx, head, (VerifierSpec("d", ("tests/test_d.py",)),), "r1")
    return run


def test_ac_h2_every_way_and_spelling_is_refused_and_the_run_is_not_a_pass(
    repo, tmp_path, monkeypatch
):
    run = verify(repo, tmp_path, monkeypatch, HEAD + variants())
    assert len(run.outcomes) == len(WAYS) * len(SPELLINGS)
    assert set(run.outcomes.values()) == {"passed"}, run.outcomes  # each one was refused
    assert not run.observation.passed and not run.ran
    assert len(run.data_refused) == len(run.outcomes) and "DATA_EXEC_REFUSED" in run.why


@pytest.mark.parametrize(
    "catch",
    ["except Exception:\n        pass", "except BaseException:\n        pass"],
    ids=["exception", "base_exception"],
)
def test_ac_h3_a_caught_refusal_still_fails_the_run(repo, tmp_path, monkeypatch, catch):
    text = HEAD + f"def test_caught():\n    try:\n        load('tests/data')\n    {catch}\n"
    run = verify(repo, tmp_path, monkeypatch, text)
    want = "passed" if "Base" in catch else "failed"  # Exception does not catch it
    assert run.outcomes == {"tests/test_d.py::test_caught": want}
    assert not run.observation.passed and not run.ran and run.data_refused


def test_ac_h4_h5_reading_the_data_and_an_unrelated_child_pass(repo, tmp_path, monkeypatch):
    text = HEAD + (
        "import ast\n"
        "def test_reads():\n"
        "    assert ast.parse(open(P).read()) and open(ABS).read() == 'X = 1\\n'\n"
        "    subprocess.run([sys.executable, '-c', 'print(1)'], check=True)\n"
    )
    run = verify(repo, tmp_path, monkeypatch, text)
    assert run.observation.passed and run.ran and run.data_refused == (), run.why


def test_ac_h5_a_child_that_names_the_data_is_refused(repo, tmp_path, monkeypatch):
    text = HEAD + (
        "def test_child():\n"
        "    refused(lambda: subprocess.run([sys.executable, P]))\n"
        "    refused(lambda: subprocess.run(f'{sys.executable} ./' + P, shell=True))\n"
        "    refused(lambda: os.posix_spawn(sys.executable, [sys.executable, ABS], {}))\n"
    )
    run = verify(repo, tmp_path, monkeypatch, text)
    assert run.outcomes == {"tests/test_d.py::test_child": "passed"}, run.why
    assert not run.observation.passed and len(run.data_refused) == 3


def test_ac_h6_a_base_failing_only_by_the_refusal_is_uncomparable(repo, tmp_path, monkeypatch):
    text = HEAD + "def test_regression():\n    load('tests/data')\n"
    run = verify(repo, tmp_path, monkeypatch, text)
    assert run.outcomes == {"tests/test_d.py::test_regression": "failed"}
    got = base_comparison([run], frozenset({"tests/test_d.py::test_regression"}))
    assert (got.kind, got.code) == ("UNCOMPARABLE", "DATA_EXEC_REFUSED")
