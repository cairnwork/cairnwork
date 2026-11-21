"""Integration tests for the fixture collection recorder plugin."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_collection(project: Path, record_dir: Path | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    if record_dir is None:
        env.pop("CAIRNWORK_FIXTURE_RECORD_DIR", None)
    else:
        env["CAIRNWORK_FIXTURE_RECORD_DIR"] = str(record_dir)
    env["CAIRNWORK_SETTINGS_DIGEST"] = "settings"
    env["CAIRNWORK_BASE_SHA"] = "base"
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


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, contents in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return tmp_path


def read_record(record_dir: Path) -> dict:
    return json.loads((record_dir / "record.json").read_text(encoding="utf-8"))


def test_records_fixture_sources_and_all_stages(tmp_path: Path) -> None:
    project(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n@pytest.fixture(autouse=True)\ndef auto(): pass\n"
                "@pytest.fixture\ndef dep(): pass\n@pytest.fixture\ndef requested(dep): pass\n"
            ),
            "test_example.py": (
                "import pytest\npytestmark = pytest.mark.usefixtures('marked')\n"
                "@pytest.fixture\ndef marked(): pass\n"
                "@pytest.mark.parametrize('value', [1, 2])\n"
                "def test_parameter(value, requested): pass\n"
                "@pytest.mark.usefixtures('class_marked')\nclass TestGroup:\n"
                "    @pytest.fixture\n    def class_marked(self): pass\n"
                "    def test_method(self): pass\n"
            ),
        },
    )
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    result = run_collection(tmp_path, record_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    record = read_record(record_dir)
    assert record["complete"] is True
    assert set(record["stages"]) == {
        "itemcollected",
        "modifyitems_before",
        "modifyitems_after",
        "finish",
    }
    assert {"value", "requested", "dep", "auto", "marked", "class_marked"} <= set(record["names"])
    assert record["recorder_version"] == 1
    assert record["settings_digest"] == "settings"
    assert record["base_sha"] == "base"


def test_deselected_fixture_remains_recorded(tmp_path: Path) -> None:
    project(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\ndef pytest_collection_modifyitems(items):\n"
                "    items[:] = [i for i in items if i.name != 'test_removed']\n"
            ),
            "test_example.py": (
                "import pytest\n@pytest.fixture\ndef X(): pass\n"
                "def test_removed(X): pass\ndef test_kept(): pass\n"
            ),
        },
    )
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    result = run_collection(tmp_path, record_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    record = read_record(record_dir)
    assert "X" in record["names"]
    assert record["stages"]["itemcollected"]["count"] == 2
    assert record["stages"]["modifyitems_after"]["count"] == 1


def test_first_and_last_hooks_capture_item_changes(tmp_path: Path) -> None:
    project(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "@pytest.hookimpl(tryfirst=True)\n"
                "def pytest_collection_modifyitems(items):\n"
                "    items.append(items[0])\n"
                "@pytest.hookimpl(trylast=True, specname='pytest_collection_modifyitems')\n"
                "def pytest_collection_modifyitems_last(items):\n"
                "    items[:] = [item for item in items if item.name != 'test_one']\n"
            ),
            "test_example.py": (
                "import pytest\n@pytest.fixture\ndef added(): pass\n"
                "def test_one(added): pass\ndef test_other(): pass\n"
            ),
        },
    )
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    result = run_collection(tmp_path, record_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    record = read_record(record_dir)
    assert record["stages"]["modifyitems_before"]["count"] == 3
    assert record["stages"]["modifyitems_after"]["count"] == 2


def test_unset_directory_writes_nothing_and_preserves_exit(tmp_path: Path) -> None:
    project(tmp_path, {"test_example.py": "def test_ok(): pass\n"})
    result = run_collection(tmp_path, None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "record.json").exists()


def test_collection_error_does_not_mark_record_complete(tmp_path: Path) -> None:
    project(tmp_path, {"test_example.py": "def test_broken(: pass\n"})
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    result = run_collection(tmp_path, record_dir)
    assert result.returncode != 0
    path = record_dir / "record.json"
    assert not path.exists() or read_record(record_dir)["complete"] is False
