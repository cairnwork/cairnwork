"""Pure checks for environment skip evidence and its trusted base-file requirement."""

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change, git

from cairnwork import report_guard
from cairnwork.deferred_tests import Comparison
from cairnwork.skip_evidence import ENV_SKIP_REASONS, env_skip_comparison

NODE = "tests/test_env.py::test_needs_sandbox"


def _run(**changes: Any) -> SimpleNamespace:
    values = {
        "spec": SimpleNamespace(verifier_id="v1"),
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
