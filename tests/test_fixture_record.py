"""Integration tests for the fixture collection recorder plugin."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_collection(
    project: Path, record_dir: Path | None, extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
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
            *extra,
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
    assert record["recorder_version"] == 2
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


CUSTOM_ITEM = (
    "import pytest\n\n\nclass Odd(pytest.Item):\n    def runtest(self): pass\n\n\n"
    "def pytest_collect_file(file_path, parent):\n"
    "    if file_path.name == 'odd.txt':\n        return Odd.from_parent(parent, name='odd')\n"
)


def _exit(tmp_path: Path, files: dict[str, str], with_record: bool) -> tuple[int, Path]:
    project(tmp_path, files)
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    return run_collection(tmp_path, record_dir if with_record else None).returncode, record_dir


def test_item_without_fixturenames_keeps_exit_and_record(tmp_path: Path) -> None:
    files = {"conftest.py": CUSTOM_ITEM, "odd.txt": "x", "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    plain = tmp_path / "plain"
    plain.mkdir()
    project(plain, files)
    assert code == run_collection(plain, None).returncode == 0
    record = read_record(record_dir)
    assert record["complete"] is True
    assert record["stages"]["finish"]["count"] == 2


def test_tryfirst_removal_still_recorded(tmp_path: Path) -> None:
    conftest = (
        "import pytest\n\n\n@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_modifyitems(items):\n    items[:] = []\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(monkeypatch): pass\n"}
    _, record_dir = _exit(tmp_path, files, True)
    assert "monkeypatch" in read_record(record_dir)["names"]


def test_usage_error_record_not_complete(tmp_path: Path) -> None:
    project(tmp_path, {"test_a.py": "def test_a(): pass\n"})
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    env_run = run_collection(tmp_path, record_dir)
    assert env_run.returncode == 0
    (record_dir / "record.json").unlink()
    code = run_collection(tmp_path, record_dir, ("no_such_path",)).returncode
    assert code != 0
    path = record_dir / "record.json"
    assert not path.exists() or read_record(record_dir)["complete"] is False


def test_prewritten_record_is_replaced(tmp_path: Path) -> None:
    conftest = (
        "import json, os, pathlib\n\n\ndef pytest_configure(config):\n"
        "    d = pathlib.Path(os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'])\n"
        "    (d / 'record.json').write_text(json.dumps({'complete': True, 'names': ['forged']}))\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(tmp_path): pass\n"}
    _, record_dir = _exit(tmp_path, files, True)
    record = read_record(record_dir)
    assert "forged" not in record["names"]
    assert "tmp_path" in record["names"]


def test_directory_change_during_collection_is_not_followed(tmp_path: Path) -> None:
    conftest = (
        "import os, pathlib\n"
        "here = pathlib.Path(__file__).parent / 'elsewhere'\n"
        "os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'] = str(here)\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    (tmp_path / "elsewhere").mkdir()
    _, record_dir = _exit(tmp_path, files, True)
    assert (record_dir / "record.json").exists()
    assert not (tmp_path / "elsewhere" / "record.json").exists()


def test_recorder_fault_voids_record_but_not_the_run(tmp_path: Path) -> None:
    conftest = (
        "import pytest\n\n\nclass Boom(pytest.Item):\n    def runtest(self): pass\n\n"
        "    @property\n    def fixturenames(self):\n        raise RuntimeError('boom')\n\n\n"
        "def pytest_collect_file(file_path, parent):\n"
        "    if file_path.name == 'odd.txt':\n        return Boom.from_parent(parent, name='b')\n"
    )
    files = {"conftest.py": conftest, "odd.txt": "x", "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    path = record_dir / "record.json"
    assert not path.exists() or read_record(record_dir)["complete"] is False


def test_single_hook_fault_voids_complete_but_not_the_run(tmp_path: Path) -> None:
    conftest = (
        "import pytest\n\n\nclass Once(pytest.Item):\n    n = 0\n\n"
        "    def runtest(self): pass\n\n    @property\n    def fixturenames(self):\n"
        "        Once.n += 1\n        if Once.n == 1:\n            raise RuntimeError('once')\n"
        "        return []\n\n\ndef pytest_collect_file(file_path, parent):\n"
        "    if file_path.name == 'odd.txt':\n        return Once.from_parent(parent, name='o')\n"
    )
    files = {"conftest.py": conftest, "odd.txt": "x", "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    record = read_record(record_dir)
    assert code == 0
    assert set(record["stages"]) == {
        "itemcollected",
        "modifyitems_before",
        "modifyitems_after",
        "finish",
    }
    assert record["complete"] is False


def _record_for(
    tmp_path: Path, files: dict[str, str], extra: tuple[str, ...] = ()
) -> tuple[int, dict]:
    code, record_dir = _exit(tmp_path, files, True) if not extra else (None, None)
    if extra:
        project(tmp_path, files)
        record_dir = tmp_path / "run"
        record_dir.mkdir()
        code = run_collection(tmp_path, record_dir, extra).returncode
    return code, read_record(record_dir)


def test_conftest_plugin_module_recorded_with_file(tmp_path: Path) -> None:
    files = {"conftest.py": "X = 1\n", "test_a.py": "def test_a(): pass\n"}
    code, record = _record_for(tmp_path, files)
    assert code == 0
    entries = [m for m in record["plugin_modules"] if m["name"] == "conftest"]
    assert entries and Path(entries[0]["file"]).name == "conftest.py"


def test_pytest_plugins_and_dash_p_modules_recorded(tmp_path: Path) -> None:
    files = {
        "conftest.py": "pytest_plugins = ['helper_plug']\n",
        "helper_plug.py": "X = 1\n",
        "other_plug.py": "X = 2\n",
        "test_a.py": "def test_a(): pass\n",
    }
    code, record = _record_for(tmp_path, files, ("-p", "other_plug"))
    assert code == 0
    names = {m["name"] for m in record["plugin_modules"]}
    assert {"helper_plug", "other_plug"} <= names
    files_by_name = {m["name"]: m["file"] for m in record["plugin_modules"]}
    assert Path(files_by_name["helper_plug"]).name == "helper_plug.py"
    assert Path(files_by_name["other_plug"]).name == "other_plug.py"


def test_sys_path_at_load_and_inserted_path_only_at_finish(tmp_path: Path) -> None:
    conftest = (
        "import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'extra'))\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    (tmp_path / "extra").mkdir()
    code, record = _record_for(tmp_path, files)
    assert code == 0
    assert str(ROOT / "src") in record["sys_path"]
    assert not any(p.endswith("extra") for p in record["sys_path"])
    assert any(p.endswith("extra") for p in record["sys_path_finish"])


def test_plugin_object_without_name_keeps_record(tmp_path: Path) -> None:
    conftest = (
        "class Anon:\n    pass\n\n\n"
        "def pytest_configure(config):\n    config.pluginmanager.register(Anon(), 'anon')\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record = _record_for(tmp_path, files)
    assert code == 0
    assert record["complete"] is True
    assert "finish" in record["stages"]


def test_raising_file_attribute_voids_complete_not_exit(tmp_path: Path) -> None:
    conftest = (
        "class Boom:\n    __name__ = 'boomplug'\n\n"
        "    @property\n    def __file__(self):\n        raise RuntimeError('boom')\n\n\n"
        "def pytest_configure(config):\n    config.pluginmanager.register(Boom(), 'boom')\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    path = record_dir / "record.json"
    assert not path.exists() or read_record(record_dir)["complete"] is False
