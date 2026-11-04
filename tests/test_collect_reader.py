"""#793 Step 1: a collect-only verifier run gives the node ids pytest collected, never a pass.

End to end (sandbox): the same pinned ini, guard and sandbox as any verifier run, with
``--collect-only``. Then the reader on its own: which exit codes and records it accepts.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change

from cairnwork.collect_reader import Collected, read_collected
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext

OK = "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
# a shape the static reader (deferred_nodes) does not see: an aliased TestCase base
ALIAS = (
    "from unittest import TestCase as Base\n\n"
    "class Checks(Base):\n"
    "    def test_alias(self):\n"
    "        self.assertTrue(True)\n"
)
FAILING = "def test_red():\n    assert False\n"
BROKEN = "import no_such_module_793\n\ndef test_never():\n    pass\n"
EMPTY = "X = 1\n"


def collect(repo, tmp_path, files, *, collect_only=True):
    head = commit_change(repo, files)
    spec = VerifierSpec("collect", tuple(sorted(files)), collect_only=collect_only)
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    (run,) = run_verifiers(ctx, head, (spec,), "r1")
    return run


@pytest.mark.sandbox
def test_collect_only_reads_ids_and_is_never_a_pass(repo, tmp_path):
    files = {"tests/test_ok.py": OK, "tests/test_alias.py": ALIAS}
    run = collect(repo, tmp_path, files)
    assert run.returncode == 0
    assert not run.observation.passed and not run.ran  # AC-G: no test ran, so never a pass
    assert read_collected(run) == Collected(
        ids=(
            "tests/test_alias.py::Checks::test_alias",
            "tests/test_ok.py::test_one",
            "tests/test_ok.py::test_two",
        )
    )


@pytest.mark.sandbox
def test_control_the_same_files_pass_without_collect_only(repo, tmp_path):
    run = collect(repo, tmp_path, {"tests/test_ok.py": OK}, collect_only=False)
    assert run.observation.passed
    assert run.collected == ("tests/test_ok.py::test_one", "tests/test_ok.py::test_two")


@pytest.mark.sandbox
def test_a_failing_test_is_collected_not_run(repo, tmp_path):
    run = collect(repo, tmp_path, {"tests/test_red.py": FAILING})
    assert not run.observation.passed
    assert read_collected(run).ids == ("tests/test_red.py::test_red",)


@pytest.mark.sandbox
def test_a_collect_error_names_its_file_and_keeps_the_others(repo, tmp_path):
    run = collect(repo, tmp_path, {"tests/test_ok.py": OK, "tests/test_broken.py": BROKEN})
    assert run.returncode == 2
    got = read_collected(run)
    assert got.unusable == ""
    assert got.error_files == ("tests/test_broken.py",)  # unknown, never read as "no tests"
    assert got.ids == ("tests/test_ok.py::test_one", "tests/test_ok.py::test_two")


@pytest.mark.sandbox
def test_nothing_collected_is_the_empty_set(repo, tmp_path):
    run = collect(repo, tmp_path, {"tests/test_empty.py": EMPTY})
    assert run.returncode == 5
    assert read_collected(run) == Collected()


def fake(**kw) -> Any:
    base: dict[str, Any] = dict(
        spec=VerifierSpec("c", ("tests/test_a.py",), collect_only=True), returncode=0,
        forged=(), timed_out=False, survivors=0, mutated=(), report_too_big=False,
        log_too_big=False, recorded=True, outcomes={}, collected=("tests/test_a.py::test_x",),
        collect_errors=(), collect_skipped=(), log_path=Path("/nonexistent"),
    )  # fmt: skip
    return SimpleNamespace(**(base | kw))


def test_a_clean_rc0_record_is_read():
    assert read_collected(fake()).ids == ("tests/test_a.py::test_x",)


def test_only_a_collect_only_run_is_read():
    with pytest.raises(ValueError):
        read_collected(fake(spec=VerifierSpec("c", ("tests/test_a.py",))))


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"returncode": None}, "collect-not-run"),
        ({"forged": ("x",)}, "collect-forged"),
        ({"timed_out": True}, "collect-timeout"),
        ({"survivors": 1}, "collect-survivors"),
        ({"mutated": ("src/a.py",)}, "collect-mutated"),
        ({"log_too_big": True}, "collect-over-cap"),
        ({"report_too_big": True}, "collect-over-cap"),
        ({"recorded": False}, "collect-not-recorded"),
        ({"outcomes": {"tests/test_a.py::test_x": "passed"}}, "collect-ran-tests"),
        ({"collected": ("tests/test_b.py::test_y",)}, "collected-outside-the-targets"),
        ({"returncode": 2, "collect_errors": ("tests",)}, "collect-error-outside-a-target"),
        ({"returncode": 1}, "collect-rc-1"),
        ({"returncode": 4}, "collect-rc-4"),
        ({"returncode": 5}, "collect-rc-5"),  # rc 5 with ids recorded: code and record disagree
        ({"collected": ()}, "collect-rc-0"),  # rc 0 with nothing recorded
        ({"returncode": 2}, "collect-rc-2"),  # rc 2 without a failed collector: not per file
        ({"collect_errors": ("tests/test_a.py",)}, "collect-rc-0"),
    ],
)
def test_an_unreadable_run_gives_no_ids(change, why):
    got = read_collected(fake(**change))
    assert got == Collected(unusable=why)
