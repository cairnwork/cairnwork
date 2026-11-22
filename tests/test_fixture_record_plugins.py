"""Plugin-module and sys.path fields of the fixture recorder, and its fault isolation."""

import os
import subprocess
import sys
from pathlib import Path

from test_fixture_record import ROOT, _exit, project, read_record, run_collection


def _record_for(
    tmp_path: Path, files: dict[str, str], extra: tuple[str, ...] = ()
) -> tuple[int, dict]:
    if not extra:
        code, record_dir = _exit(tmp_path, files, True)
        return code, read_record(record_dir)
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


def test_plugin_file_raising_systemexit_keeps_exit_code(tmp_path: Path) -> None:
    conftest = (
        "import pytest\n\n\nclass Exiting:\n    __name__ = 'exitplug'\n    armed = False\n\n"
        "    @property\n    def __file__(self):\n        if Exiting.armed:\n"
        "            raise SystemExit(5)\n        return '/x/exit.py'\n\n\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(Exiting(), 'exiting')\n\n\n"
        "@pytest.hookimpl(tryfirst=True)\ndef pytest_collection_finish(session):\n"
        "    Exiting.armed = True\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    assert read_record(record_dir)["complete"] is not True


def test_non_string_plugin_metadata_is_skipped(tmp_path: Path) -> None:
    conftest = (
        "class Num:\n    __name__ = 'numplug'\n    __file__ = 123\n\n\n"
        "class NumName:\n    __name__ = 7\n    __file__ = 'name.py'\n\n\n"
        "def pytest_configure(config):\n    config.pluginmanager.register(Num(), 'num')\n"
        "    config.pluginmanager.register(NumName(), 'numname')\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    record = read_record(record_dir)
    assert record["complete"] is not True
    modules = record["plugin_modules"]
    assert all(isinstance(m["name"], str) for m in modules)
    assert all(m["file"] is None or isinstance(m["file"], str) for m in modules)
    assert not any(m["name"] in ("numplug", 7) or m["file"] == 123 for m in modules)


def test_conftest_plugin_entry_has_exact_name_and_file(tmp_path: Path) -> None:
    files = {"conftest.py": "X = 1\n", "test_a.py": "def test_a(): pass\n"}
    code, record = _record_for(tmp_path, files)
    assert code == 0
    entries = [m for m in record["plugin_modules"] if m["name"] == "conftest"]
    assert len(entries) == 1
    assert set(entries[0]) == {"name", "file"}
    assert Path(entries[0]["file"]).resolve() == (tmp_path / "conftest.py").resolve()


def test_item_fixturenames_raising_systemexit_keeps_exit_code(tmp_path: Path) -> None:
    conftest = (
        "import pytest\n\n\nclass Bad(pytest.Item):\n    @property\n    def fixturenames(self):\n"
        "        raise SystemExit(5)\n\n    def runtest(self):\n        pass\n\n\n"
        "def pytest_collect_file(file_path, parent):\n"
        "    if file_path.name == 'odd.txt':\n        return Bad.from_parent(parent, name='bad')\n"
    )
    files = {"conftest.py": conftest, "odd.txt": "x", "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    assert (
        not (record_dir / "record.json").exists() or read_record(record_dir)["complete"] is not True
    )


def test_non_string_sys_path_entry_voids_complete(tmp_path: Path) -> None:
    conftest = "import sys\n\n\ndef pytest_configure(config):\n    sys.path.append(123)\n"
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    plain = tmp_path / "plain"
    plain.mkdir()
    project(plain, files)
    # a non-str sys.path entry breaks pytest itself; compare with a run that has no recorder
    assert code == run_collection(plain, None).returncode
    assert (record_dir / "record.json").exists()
    record = read_record(record_dir)
    assert record["complete"] is not True
    assert all(isinstance(p, str) for p in record["sys_path_finish"])


def test_falsey_non_string_plugin_name_voids_complete(tmp_path: Path) -> None:
    conftest = (
        "class Zero:\n    __name__ = 0\n    __file__ = '/x/zero.py'\n\n\n"
        "def pytest_configure(config):\n    config.pluginmanager.register(Zero(), 'zero')\n"
    )
    files = {"conftest.py": conftest, "test_a.py": "def test_a(): pass\n"}
    code, record_dir = _exit(tmp_path, files, True)
    assert code == 0
    assert (record_dir / "record.json").exists()
    record = read_record(record_dir)
    assert record["complete"] is not True
    assert not any(m["file"] == "/x/zero.py" for m in record["plugin_modules"])


RUNNER = """\
import sys
import functools, json, os, tempfile, pathlib, typing
import pytest
import cairnwork


class Guarded(list):
    armed = True

    def __iter__(self):
        if Guarded.armed:
            raise SystemExit(5)
        return super().__iter__()


sys.path = Guarded(sys.path)
import cairnwork.fixture_record as fr

Guarded.armed = False
assert fr._failed[0] is True
assert fr._META["sys_path"] == []
raise SystemExit(pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider",
                              "-p", "cairnwork.fixture_record", "."]))
"""


def test_load_time_sys_path_read_failure_keeps_exit_status(tmp_path: Path) -> None:
    (tmp_path / "runner.py").write_text(RUNNER, encoding="utf-8")
    (tmp_path / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    record_dir = tmp_path / "run"
    record_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    env["CAIRNWORK_FIXTURE_RECORD_DIR"] = str(record_dir)
    env["CAIRNWORK_SETTINGS_DIGEST"] = "settings"
    env["CAIRNWORK_BASE_SHA"] = "base"
    result = subprocess.run(
        [sys.executable, str(tmp_path / "runner.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    path = record_dir / "record.json"
    assert not path.exists() or read_record(record_dir)["complete"] is not True
