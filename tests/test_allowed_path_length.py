"""An allowed file longer than Core's 512-byte text limit is refused before anything runs (#645).

Core's wire validator refuses a text over 512 UTF-8 bytes, so such a task could only end refused
after its worker ran. path_refusal (shared by submit, the shadow check, verify and the Issue
path) refuses it first; allowed paths are ASCII, so characters and bytes agree."""

import pytest
from test_intake import FakePopen, payload
from test_intake import intake as intake  # noqa: F401 - fixture
from test_intake import repo as repo  # noqa: F401 - fixture
from test_pipeline import CrashWorker, go
from test_pipeline import project as project  # noqa: F401 - fixture
from test_pipeline import stores as stores  # noqa: F401 - fixture

from cairnwork.pipeline import TaskRejected
from cairnwork.protected_paths import path_refusal


def _path(length: int) -> str:
    return "src/" + "a" * (length - len("src/.py")) + ".py"


def test_a_path_up_to_512_bytes_is_still_a_clean_path():
    assert len(_path(512)) == 512 and path_refusal(_path(512)) is None
    refusal = path_refusal(_path(513))
    assert refusal is not None and refusal[0] == "TASK_ALLOWED_FILES_INVALID"


def test_a_task_built_directly_is_refused_before_the_worker_runs(project, stores, tmp_path):  # noqa: F811
    with pytest.raises(TaskRejected, match="BAD_SCOPE"):
        go(project, stores, tmp_path, CrashWorker(), allowed=(_path(513),))
    assert not (tmp_path / "runs").exists()


def test_submit_refuses_it_and_binds_nothing(intake, repo):  # noqa: F811
    with pytest.raises(TaskRejected, match="BAD_SCOPE"):
        intake.submit({**payload(repo), "allowed_files": [_path(513)]})
    assert intake.in_flight()["count"] == 0 and FakePopen.held == []
    assert not list((intake.state / "requests").glob("*.json"))
