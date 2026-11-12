"""Pure checks for environment skip evidence and its trusted base-file requirement."""

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change, git

from cairnwork import pipeline_judge, report_guard
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.skip_evidence import ENV_SKIP_REASONS, env_skip_comparison

NODE = "tests/test_env.py::test_needs_sandbox"


def _run(**changes: Any) -> SimpleNamespace:
    values = {
        "spec": SimpleNamespace(verifier_id="v1", collect_only=False),
        "returncode": 0,
        "recorded": True,
        "timed_out": False,
        "mutated": (),
        "survivors": 0,
        "log_too_big": False,
        "report_too_big": False,
        "collection_finished": True,
        "collected": (NODE,),
        "outcomes": {NODE: "skipped"},
        "skips": {NODE: ("marker", ENV_SKIP_REASONS[0])},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _compare(run, repo, base, judged, *, base_skips=None):
    return env_skip_comparison(
        [run],
        repo,
        base,
        judged,
        {run.spec.verifier_id: run.skips} if base_skips is None else base_skips,
        base_nodes={run.spec.verifier_id: run.collected},
    )


@pytest.fixture
def commits(repo):
    commit_change(
        repo,
        {
            "tests/test_env.py": "def test_needs_sandbox(): assert False\n",
            "src/value.py": "value = 1\n",
        },
    )
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "--detach", base)
    commit_change(repo, {"src/value.py": "value = 2\n"})
    judged = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    return repo, base, judged


def test_all_environment_marker_skips_are_uncomparable(commits):
    repo, base, judged = commits
    result = _compare(_run(), repo, base, judged)
    assert result == Comparison(
        "UNCOMPARABLE",
        "TESTS_SKIPPED_ENVIRONMENT",
        "v1: 1 skipped (needs macOS sandbox-exec; tests/test_env.py)",
    )


def test_detail_never_includes_host_paths_from_verifier_ids(commits):
    repo, base, judged = commits
    run = _run(spec=SimpleNamespace(verifier_id="/Users/private/verifier"))
    result = _compare(run, repo, base, judged)
    assert result is not None and "/Users/" not in result.detail


@pytest.mark.parametrize(
    "evidence",
    [
        ("marker", "needs macOS sandbox-exec extra"),
        ("runtime", "needs macOS sandbox-exec"),
        ("marker", "flaky on tuesdays"),
    ],
)
def test_untrusted_skip_kind_or_reason_stays_failed(commits, evidence):
    repo, base, judged = commits
    run = _run(skips={NODE: evidence})
    assert _compare(run, repo, base, judged) is None


def test_changed_test_file_cannot_supply_environment_skip(commits):
    repo, base, judged = commits
    git(repo, "checkout", "-q", "--detach", judged)
    commit_change(repo, {"tests/test_env.py": "def test_needs_sandbox(): assert False\n# edit\n"})
    edited = git(repo, "rev-parse", "HEAD").strip()
    assert _compare(_run(), repo, base, edited) is None


def test_runtime_skip_in_a_candidate_added_test_is_not_environment_evidence(commits):
    repo, base, judged = commits
    git(repo, "checkout", "-q", "--detach", judged)
    commit_change(repo, {"tests/test_added.py": "def test_added(): assert False\n"})
    added = git(repo, "rev-parse", "HEAD").strip()
    node = "tests/test_added.py::test_added"
    run = _run(
        collected=(node,),
        outcomes={node: "skipped"},
        skips={node: ("runtime", ENV_SKIP_REASONS[0])},
    )
    assert _compare(run, repo, base, added) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"collected": ()},
        {"outcomes": {NODE: "passed"}},
        {"outcomes": {NODE: "failed"}},
        {"returncode": 1},
        {"timed_out": True},
        {"recorded": False},
        {"collection_finished": False},
        {"collect_errors": ("tests/test_env.py",)},
        {"collect_skipped": ("tests/test_env.py",)},
        {"skipped_deps": ("missing_dep",)},
        {"mutated": ("x",)},
        {"survivors": 1},
        {"log_too_big": True},
        {"report_too_big": True},
    ],
)
def test_incomplete_or_bad_run_stays_failed(commits, changes):
    repo, base, judged = commits
    assert _compare(_run(**changes), repo, base, judged) is None


def test_each_verifier_must_qualify(commits):
    repo, base, judged = commits
    assert (
        env_skip_comparison([_run(), _run(returncode=1)], repo, base, judged, {"v1": _run().skips})
        is None
    )


def test_base_skip_mismatch_stays_failed(commits):
    repo, base, judged = commits
    assert _compare(_run(), repo, base, judged, base_skips={"v1": {}}) is None


def test_missing_base_run_stays_failed(commits):
    repo, base, judged = commits
    assert env_skip_comparison([_run()], repo, base, judged) is None


def test_identical_base_skips_are_uncomparable(commits):
    repo, base, judged = commits
    assert _compare(_run(), repo, base, judged) == Comparison(
        "UNCOMPARABLE",
        "TESTS_SKIPPED_ENVIRONMENT",
        "v1: 1 skipped (needs macOS sandbox-exec; tests/test_env.py)",
    )


def test_each_deferred_file_must_be_collected(commits):
    repo, base, judged = commits
    assert (
        env_skip_comparison(
            [_run()], repo, base, judged, {"v1": _run().skips}, ("tests/test_other.py",)
        )
        is None
    )


def test_base_node_set_must_match_candidate_collection(commits):
    repo, base, judged = commits
    nodes = {"v1": (NODE, "tests/test_env.py::extra")}
    assert (
        env_skip_comparison([_run()], repo, base, judged, {"v1": _run().skips}, base_nodes=nodes)
        is None
    )


def test_failed_blackbox_check_prevents_downgrade(commits):
    repo, base, judged = commits
    failed = SimpleNamespace(observation=SimpleNamespace(passed=False))
    assert (
        env_skip_comparison([_run()], repo, base, judged, {"v1": _run().skips}, checks=(failed,))
        is None
    )


def test_worker_judge_keeps_identical_all_skips_failed(commits, monkeypatch):
    repo, base, judged = commits
    run = _run()
    files = SimpleNamespace(base_skips={"v1": run.skips}, base_nodes={"v1": run.collected})
    task = SimpleNamespace(repo=repo, base_commit=base)
    result = SimpleNamespace(reason="")

    def judge(*args, **kwargs):
        pipeline_judge.raise_for_env_skips(
            [run], task, judged, files, enabled=kwargs.get("env_skip_downgrade", False)
        )
        result.reason = "FAILED"

    monkeypatch.setattr(pipeline_judge, "judge_candidate", judge)
    pipeline_judge.judge_for(SimpleNamespace(isolated=True), task, None, None, None, result, files)
    assert result.reason == "FAILED"


def test_report_plugin_distinguishes_marker_and_runtime_skips(tmp_path):
    (tmp_path / "test_skips.py").write_text(
        "import pytest\n"
        "@pytest.mark.skipif(True, reason='needs macOS sandbox-exec')\n"
        "def test_marker(): pass\n"
        "def test_runtime(): pytest.skip('runtime reason')\n"
    )
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    path = tmp_path / "record.json"
    env = {
        **os.environ,
        report_guard.PATH_ENV: str(path),
        report_guard.NONCE_ENV: "nonce",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
    }
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", report_guard.MODULE, "test_skips.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    verdict = report_guard.read_verdict(path, "nonce")
    assert verdict.skips == {
        "test_skips.py::test_marker": ("marker", "needs macOS sandbox-exec"),
        "test_skips.py::test_runtime": ("runtime", "runtime reason"),
    }


def _raise(monkeypatch, repo, base, judged, base_run, tmp_path, runs=None, test_files=None):
    """raise_for_env_skips on the verify path with no deferred tests: ``base_run`` is what the
    one base run returns (an Exception instance is raised instead). Returns the recorded base-run
    calls and the code of the BaseUncomparable it raised (None when it raised nothing); the fake
    never raises an assertion itself, because the code under test swallows base-run errors."""
    from cairnwork import skip_evidence

    calls: list[tuple] = []

    def fake(ctx, commit, specs, attempt, deny=False):
        calls.append((commit, deny, attempt))
        if isinstance(base_run, Exception):
            raise base_run
        return [base_run]

    monkeypatch.setattr(skip_evidence, "run_verifiers", fake)
    task = SimpleNamespace(repo=repo, base_commit=base)
    try:
        skip_evidence.raise_for_env_skips(
            runs or [_run()],
            task,
            judged,
            test_files,
            (),
            True,
            (SimpleNamespace(attempt_id="a"), ()),
        )
    except BaseUncomparable as raised:
        return calls, raised.comparison.code
    return calls, None


def _base_run(tmp_path, **changes):
    (tmp_path / "b").mkdir()
    return _run(log_path=tmp_path / "b" / "verifier.log", **changes)


def test_no_deferred_tests_an_identical_base_skip_set_is_uncomparable(
    commits, monkeypatch, tmp_path
):
    repo, base, judged = commits
    calls, code = _raise(monkeypatch, repo, base, judged, _base_run(tmp_path), tmp_path)
    assert calls == [(base, True, "a-eb")]  # exactly one base run, denied, on the base commit
    assert code == "TESTS_SKIPPED_ENVIRONMENT"
    assert not (tmp_path / "b").exists()  # the base run's logs are removed


@pytest.mark.parametrize(
    "changes",
    [
        {"outcomes": {NODE: "passed"}},  # the base ran the test: the candidate's skip is its own
        {"skips": {NODE: ("marker", "needs the cairnwork-verifier account")}},  # other reason
        {"collected": (NODE, "tests/test_env.py::test_other"), "outcomes": {NODE: "skipped"}},
        {"returncode": 1},  # an unclean base run is no evidence
        {"recorded": False},
    ],
)
def test_no_deferred_tests_a_base_that_differs_keeps_failed(
    commits, monkeypatch, tmp_path, changes
):
    repo, base, judged = commits
    calls, code = _raise(monkeypatch, repo, base, judged, _base_run(tmp_path, **changes), tmp_path)
    assert calls == [(base, True, "a-eb")]
    assert code is None  # nothing raised: the candidate stays FAILED


def test_no_deferred_tests_a_base_run_that_cannot_be_made_keeps_failed(
    commits, monkeypatch, tmp_path
):
    repo, base, judged = commits
    calls, code = _raise(monkeypatch, repo, base, judged, RuntimeError("busy"), tmp_path)
    assert len(calls) == 1 and code is None


def _deferred_record():
    record = SimpleNamespace(
        base_skips={"v1": {NODE: ("marker", ENV_SKIP_REASONS[0])}}, base_nodes={"v1": (NODE,)}
    )
    return type(
        "Files", (tuple,), {"base_skips": record.base_skips, "base_nodes": record.base_nodes}
    )()


_V2 = SimpleNamespace(verifier_id="v2", collect_only=False)


@pytest.mark.parametrize(
    "runs",
    [
        [_run(outcomes={NODE: "passed"})],  # real test results
        [_run(skips={NODE: ("runtime", ENV_SKIP_REASONS[0])})],  # a runtime skip is not a marker
        [_run(skips={NODE: ("marker", "flaky")})],  # a reason outside the whitelist
        [_run(returncode=1)],  # an unclean candidate run
        [_run(collected=(), outcomes={}, skips={})],  # nothing collected (cairnwork#44)
        [_run(), _run(spec=_V2, outcomes={NODE: "passed"})],  # one verifier has real results
    ],
)
def test_no_base_run_when_a_candidate_run_is_not_all_environment_skips(
    commits, monkeypatch, tmp_path, runs
):
    repo, base, judged = commits
    calls, code = _raise(monkeypatch, repo, base, judged, _base_run(tmp_path), tmp_path, runs=runs)
    assert calls == [] and code is None


def test_no_extra_base_run_when_a_deferred_test_base_record_exists(commits, monkeypatch, tmp_path):
    repo, base, judged = commits
    calls, _ = _raise(
        monkeypatch,
        repo,
        base,
        judged,
        _base_run(tmp_path),
        tmp_path,
        test_files=_deferred_record(),
    )
    assert calls == []  # the deferred-test base record is used; no second base run is spent
