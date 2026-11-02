"""Verifier ids follow Core's id rule before anything runs (#490).

Core refuses a required-id list that is empty, has an empty or whitespace-padded id, an id with
NUL or over 512 UTF-8 bytes, or a duplicate. Submit, the shadow check and verify refuse such ids
with the same rule (cairnwork_protocol.ids, shared with offline verify) instead of running a
worker or verifiers for a task Core could never verify. An id P2P widening gives its own verifier,
``p2p-<mode>``, is reserved like ``blackbox:*`` and ``protection`` (#644): submit and the shadow
check refuse it before binding, not p2p.py after the worker has run."""

import json
import subprocess
import sys

import pytest
from test_intake import FakePopen, payload
from test_intake import intake as intake  # noqa: F401 - fixture
from test_intake import repo as repo  # noqa: F401 - fixture
from test_pipeline import VERIFY, CrashWorker, go
from test_pipeline import project as project  # noqa: F401 - fixture
from test_pipeline import stores as stores  # noqa: F401 - fixture

from cairnwork import verify_candidate as vc
from cairnwork.intake import Intake, IntakeRefused, task_from_payload
from cairnwork.pipeline import TaskRejected
from cairnwork_protocol.ids import core_ids

ARGV = ["python3", "-m", "pytest", "tests/test_a.py"]
BAD_IDS = {
    "empty": [""],
    "leading-space": [" pytest"],
    "trailing-space": ["pytest "],
    "newline": ["pytest\n"],
    "nul": ["py\x00test"],
    "513-bytes": ["a" * 513],
    "514-utf8-bytes": ["é" * 257],
    "lone-surrogate": ["\ud800"],
    "duplicate": ["pytest", "pytest"],
}
NO_IDS = {**BAD_IDS, "none": []}
P2P_IDS = ["p2p-whole-suite", "p2p-module-dir"]  # every mode that adds a verifier (#644)


def _with_ids(repo, ids):  # noqa: F811
    return {**payload(repo), "verifiers": [[i, ARGV] for i in ids]}


@pytest.mark.parametrize("ids", BAD_IDS.values(), ids=BAD_IDS.keys())
def test_task_from_payload_refuses_ids_core_would_refuse(repo, ids):  # noqa: F811
    with pytest.raises(IntakeRefused, match="BAD_REQUEST.*verifier ids must be distinct"):
        task_from_payload(_with_ids(repo, ids))


@pytest.mark.parametrize("ids", BAD_IDS.values(), ids=BAD_IDS.keys())
def test_submit_refuses_bad_ids_and_binds_nothing(intake, repo, ids):  # noqa: F811
    with pytest.raises(IntakeRefused, match="BAD_REQUEST"):
        intake.submit(_with_ids(repo, ids))
    assert intake.in_flight()["count"] == 0 and FakePopen.held == []
    assert not list((intake.state / "requests").glob("*.json"))


@pytest.mark.parametrize("ids", NO_IDS.values(), ids=NO_IDS.keys())
def test_verify_refuses_bad_ids_before_anything_runs(tmp_path, repo, ids):  # noqa: F811
    state = Intake(tmp_path / "state")
    with pytest.raises(TaskRejected, match="BAD_VERIFIER.*verifier ids must be distinct"):
        vc.verify_candidate(
            state, repo=repo, base="0" * 40, candidate="1" * 40, allowed_files=("src/a.py",),
            verifiers=tuple((i, tuple(ARGV)) for i in ids),
        )  # fmt: skip


@pytest.mark.parametrize("ids", NO_IDS.values(), ids=NO_IDS.keys())
def test_a_task_built_directly_is_refused_before_the_worker_runs(project, stores, tmp_path, ids):  # noqa: F811
    argv = VERIFY[0][1]
    with pytest.raises(TaskRejected, match="BAD_VERIFIER.*verifier ids must be distinct"):
        go(project, stores, tmp_path, CrashWorker(), verifiers=tuple((i, argv) for i in ids))
    assert not (tmp_path / "runs").exists()


def test_shadow_check_refuses_bad_ids_as_submit_does(tmp_path, repo):  # noqa: F811
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))
    for ids in BAD_IDS.values():
        done = subprocess.run(
            [sys.executable, "-I", "-m", "cairnwork.route", "--state", str(state), "check"],
            input=json.dumps(_with_ids(repo, ids)), capture_output=True, text=True,
            timeout=120, check=False,
        )  # fmt: skip
        assert done.returncode == 2 and json.loads(done.stdout)["error"] == "BAD_REQUEST", ids
    assert sorted(p.name for p in state.iterdir()) == ["config.json"]


@pytest.mark.parametrize("verifier_id", P2P_IDS)
def test_submit_refuses_a_p2p_verifier_id_and_binds_nothing(intake, repo, verifier_id):  # noqa: F811
    with pytest.raises(TaskRejected, match=f"BAD_VERIFIER.*{verifier_id}.* reserved"):
        intake.submit(_with_ids(repo, ["pytest", verifier_id]))
    assert intake.in_flight()["count"] == 0 and FakePopen.held == []
    assert not list((intake.state / "requests").glob("*.json"))


def test_shadow_check_refuses_a_p2p_verifier_id_as_submit_does(tmp_path, repo):  # noqa: F811
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "config.json").write_text(json.dumps({"verifier_account": False}))
    for verifier_id in P2P_IDS:
        done = subprocess.run(
            [sys.executable, "-I", "-m", "cairnwork.route", "--state", str(state), "check"],
            input=json.dumps(_with_ids(repo, [verifier_id])), capture_output=True, text=True,
            timeout=120, check=False,
        )  # fmt: skip
        assert done.returncode == 2 and json.loads(done.stdout)["error"] == "BAD_VERIFIER", done
    assert sorted(p.name for p in state.iterdir()) == ["config.json"]


def test_ids_core_accepts_still_pass(repo):  # noqa: F811
    ok = ["pytest", "a" * 512, "é" * 256, "p2p check"]
    assert core_ids(ok)
    assert [v for v, _ in task_from_payload(_with_ids(repo, ok)).verifiers] == ok
