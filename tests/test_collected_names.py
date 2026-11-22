"""Tests for the reader that turns the fixture recorder's record.json into a name set."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cairnwork.collected_names import collected_names

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "digest-1"
SHA = "base-1"
STAGES = ("itemcollected", "modifyitems_before", "modifyitems_after", "finish")


def good_record() -> dict[str, Any]:
    stage = {"names": ["a", "tmp_path"], "count": 2}
    return {
        "recorder_version": 2,
        "complete": True,
        "settings_digest": DIGEST,
        "base_sha": SHA,
        "sys_path": ["/x"],
        "sys_path_finish": ["/x", "/y"],
        "plugin_modules": [{"name": "p", "file": None}, {"name": "q", "file": "/q.py"}],
        "stages": {key: copy.deepcopy(stage) for key in STAGES},
        "names": ["a", "tmp_path"],
    }


def write_record(tmp_path: Path, payload: Any) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (run_dir / "record.json").write_text(text, encoding="utf-8")
    return run_dir


def read(run_dir: Path, *, code: object = 0, digest: str = DIGEST, sha: str = SHA) -> Any:
    return collected_names(run_dir, exit_code=code, settings_digest=digest, base_sha=sha)


def test_good_record_returns_names(tmp_path: Path) -> None:
    run_dir = write_record(tmp_path, good_record())
    assert read(run_dir) == frozenset({"a", "tmp_path"})
    assert read(run_dir, code=5) == frozenset({"a", "tmp_path"})


def test_empty_names_is_a_valid_empty_set(tmp_path: Path) -> None:
    rec = good_record()
    empty = {"names": [], "count": 0}
    rec["stages"] = {key: dict(empty) for key in STAGES}
    rec["names"] = []
    assert read(write_record(tmp_path, rec), code=5) == frozenset()


@pytest.mark.parametrize("code", [1, 2, 3, 4, 6, -1, True, False, "0", None, 0.0])
def test_exit_code_outside_zero_and_five_returns_none(tmp_path: Path, code: object) -> None:
    run_dir = write_record(tmp_path, good_record())
    assert read(run_dir, code=code) is None


def test_missing_record_returns_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert read(run_dir) is None


def test_symlink_to_good_record_returns_none(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps(good_record()), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    os.symlink(real, run_dir / "record.json")
    assert read(run_dir) is None


def test_oversize_record_returns_none(tmp_path: Path) -> None:
    rec = good_record()
    rec["pad"] = "x" * (4 * 1024 * 1024 + 1)
    assert read(write_record(tmp_path, rec)) is None


@pytest.mark.parametrize(
    "payload",
    ["{not json", "[" * 200000, "", "null", "[]", '"text"', "42"],
)
def test_unparseable_or_non_dict_returns_none(tmp_path: Path, payload: str) -> None:
    assert read(write_record(tmp_path, payload)) is None


def test_invalid_utf8_returns_none(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "record.json").write_bytes(b"\xff\xfe\x00{")
    assert read(run_dir) is None


def _drop(key: str) -> Any:
    return lambda rec: rec.pop(key)


MUTATIONS: dict[str, Any] = {
    "version 1": lambda r: r.update(recorder_version=1),
    "version True": lambda r: r.update(recorder_version=True),
    "version missing": _drop("recorder_version"),
    "complete False": lambda r: r.update(complete=False),
    "complete missing": _drop("complete"),
    "complete string": lambda r: r.update(complete="true"),
    "digest other": lambda r: r.update(settings_digest="other"),
    "digest missing": _drop("settings_digest"),
    "digest not str": lambda r: r.update(settings_digest=None),
    "base other": lambda r: r.update(base_sha="other"),
    "base missing": _drop("base_sha"),
    "stage missing": lambda r: r["stages"].pop("finish"),
    "stage extra": lambda r: r["stages"].update(extra={"names": [], "count": 0}),
    "stage names bad": lambda r: r["stages"]["finish"].update(names=[1]),
    "stage count bool": lambda r: r["stages"]["finish"].update(count=True),
    "stage extra key": lambda r: r["stages"]["finish"].update(extra=1),
    "stage not dict": lambda r: r["stages"].update(finish=["a"]),
    "names disagree": lambda r: r.update(names=["a"]),
    "names missing": _drop("names"),
    "names non-str": lambda r: r.update(names=["a", 1]),
    "sys_path bad": lambda r: r.update(sys_path=[1]),
    "sys_path_finish bad": lambda r: r.update(sys_path_finish="x"),
    "plugins not list": lambda r: r.update(plugin_modules={}),
    "plugin name bad": lambda r: r.update(plugin_modules=[{"name": 1, "file": None}]),
    "plugin file bad": lambda r: r.update(plugin_modules=[{"name": "p", "file": 3}]),
    "plugin extra key": lambda r: r.update(plugin_modules=[{"name": "p", "file": None, "x": 1}]),
}


@pytest.mark.parametrize("mutate", list(MUTATIONS.values()), ids=list(MUTATIONS))
def test_each_failing_condition_returns_none(tmp_path: Path, mutate: Any) -> None:
    rec = good_record()
    mutate(rec)
    assert read(write_record(tmp_path, rec)) is None


def test_good_record_is_the_control_for_mutations(tmp_path: Path) -> None:
    assert read(write_record(tmp_path, good_record())) == frozenset({"a", "tmp_path"})


@pytest.mark.parametrize("digest,sha", [("", SHA), (DIGEST, ""), ("", "")])
def test_empty_binding_arguments_return_none(tmp_path: Path, digest: str, sha: str) -> None:
    rec = good_record()
    rec["settings_digest"] = digest
    rec["base_sha"] = sha
    assert read(write_record(tmp_path, rec), digest=digest, sha=sha) is None


@pytest.mark.parametrize("digest,sha", [("other", SHA), (DIGEST, "other")])
def test_binding_mismatch_returns_none(tmp_path: Path, digest: str, sha: str) -> None:
    run_dir = write_record(tmp_path, good_record())
    assert read(run_dir, digest=digest, sha=sha) is None


def test_binding_argument_must_be_a_string(tmp_path: Path) -> None:
    run_dir = write_record(tmp_path, good_record())
    assert collected_names(run_dir, exit_code=0, settings_digest=None, base_sha=SHA) is None  # type: ignore[arg-type]


# ---- integration: real recorder plugin on a tiny project ----


def run_collection(project: Path, record_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    env["CAIRNWORK_FIXTURE_RECORD_DIR"] = str(record_dir)
    env["CAIRNWORK_SETTINGS_DIGEST"] = DIGEST
    env["CAIRNWORK_BASE_SHA"] = SHA
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "cairnwork.fixture_record",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    for name, contents in files.items():
        (project / name).write_text(contents, encoding="utf-8")
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    return record_dir


def collect(tmp_path: Path, files: dict[str, str]) -> tuple[int, Any]:
    record_dir = make_project(tmp_path, files)
    result = run_collection(tmp_path / "proj", record_dir)
    return result.returncode, collected_names(
        record_dir, exit_code=result.returncode, settings_digest=DIGEST, base_sha=SHA
    )


def test_real_recorder_names_include_conftest_fixture_and_builtin(tmp_path: Path) -> None:
    files = {
        "conftest.py": "import pytest\n@pytest.fixture\ndef shared(): pass\n",
        "test_example.py": "def test_one(shared, tmp_path): pass\n",
    }
    code, names = collect(tmp_path, files)
    assert code == 0
    assert names is not None
    assert {"shared", "tmp_path"} <= names


def test_real_recorder_collection_error_returns_none(tmp_path: Path) -> None:
    code, names = collect(tmp_path, {"test_broken.py": "def test_broken(: pass\n"})
    assert code != 0
    assert names is None


def test_real_recorder_no_tests_returns_empty_set(tmp_path: Path) -> None:
    code, names = collect(tmp_path, {"helper.py": "X = 1\n"})
    assert code == 5
    assert names == frozenset()
