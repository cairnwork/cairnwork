"""#797: an OSError counts as an environment error only when it was raised outside the checkout's
own code. The innermost raising frame decides: a non-test file inside the checkout is the code
under test (no fail_kind, so teeth); stdlib, site-packages, the test file and conftest keep the
#775 reading (``env``). End to end through the real plugin."""

import subprocess
import sys

import pytest

from cairnwork import report_channel, report_guard

T = "tests/test_x.py::"
LIB = (
    "class MissingThing(FileNotFoundError):\n    pass\n\n"
    "def load():\n    raise MissingThing(2, 'gone')\n"
)
USE = "from lib import load\ndef test_a():\n    load()\n"


def _run(tmp_path, files, target="tests/test_x.py", ini=""):
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n" + ini)
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    channel = report_channel.open_channel(tmp_path)
    try:
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(guard),
            **channel.env(),
        }
        argv = [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
            "-p", report_guard.MODULE, target,
        ]  # fmt: skip
        subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, pass_fds=channel.pass_fds)
        return channel.read()
    finally:
        channel.close()


def test_an_oserror_subclass_raised_by_checkout_code_is_not_env(tmp_path):
    v = _run(tmp_path, {"lib.py": LIB, "tests/test_x.py": USE})
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {}


def test_an_oserror_from_the_standard_library_stays_env(tmp_path):
    body = "def test_a():\n    open('/nonexistent-dir-797/none.txt')\n"
    v = _run(tmp_path, {"tests/test_x.py": body})
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_an_oserror_raised_in_the_test_file_stays_env(tmp_path):
    v = _run(tmp_path, {"tests/test_x.py": "def test_a():\n    raise TimeoutError('slow')\n"})
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_an_oserror_raised_in_a_conftest_stays_env(tmp_path):
    conftest = "def boom():\n    raise ConnectionError('down')\n"
    body = "from conftest import boom\ndef test_a():\n    boom()\n"
    v = _run(tmp_path, {"tests/conftest.py": conftest, "tests/test_x.py": body})
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_checkout_code_calling_a_stdlib_python_function_that_raises_stays_env(tmp_path):
    """The innermost Python frame is pathlib's, outside the checkout."""
    lib = (
        "import pathlib\ndef load():\n"
        "    return pathlib.Path('/nonexistent-dir-797/n').read_text()\n"
    )
    v = _run(tmp_path, {"lib.py": lib, "tests/test_x.py": USE})
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_each_node_is_judged_on_its_own_innermost_frame(tmp_path):
    body = USE + "def test_b():\n    open('/nonexistent-dir-797/none.txt')\n"
    v = _run(tmp_path, {"lib.py": LIB, "tests/test_x.py": body})
    assert v.fail_kind == {f"{T}test_b": "env"}


def test_an_oserror_raised_by_a_helper_under_a_tests_directory_stays_env(tmp_path):
    body = "from helpers import load\ndef test_a():\n    load()\n"
    files = {
        "tests/helpers.py": LIB,
        "tests/test_x.py": "import sys\nsys.path.insert(0, 'tests')\n",
    }
    files["tests/test_x.py"] += body
    v = _run(tmp_path, files)
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_an_oserror_raised_in_site_packages_inside_the_checkout_stays_env(tmp_path):
    head = "import sys\nsys.path.insert(0, 'venv/lib/site-packages')\n"
    body = head + "from extlib import load\ndef test_a():\n    load()\n"
    files = {"venv/lib/site-packages/extlib.py": LIB, "tests/test_x.py": body}
    v = _run(tmp_path, files)
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_a_builtin_oserror_raised_by_checkout_code_stays_env(tmp_path):
    """A sandbox-denied open() in checkout code: a built-in class, so the environment reading
    stays (a candidate that avoids the denied read must not be VERIFIED off it)."""
    lib = "def load():\n    open('/nonexistent-dir-797/none.txt')\n"
    deny = "def load():\n    raise PermissionError(13, 'denied')\n"
    for body in (lib, deny):
        v = _run(tmp_path / str(len(body)), {"lib.py": body, "tests/test_x.py": USE})
        assert v.outcomes == {f"{T}test_a": "failed"}
        assert v.fail_kind == {f"{T}test_a": "env"}


@pytest.mark.parametrize("name", ["test_lib.py", "lib_test.py", "conftest.py"])
def test_an_oserror_raised_by_a_root_level_test_named_file_stays_env(tmp_path, name):
    """Outside any tests directory, so only the name rule keeps it out of the checkout code."""
    body = f"from {name[:-3]} import load\ndef test_a():\n    load()\n"
    v = _run(tmp_path, {name: LIB, "tests/test_x.py": body})
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_an_oserror_class_and_raise_in_the_run_test_file_itself_stays_env(tmp_path):
    """The test file is named and placed like checkout code; only the own-test-file check."""
    body = LIB + "def test_a():\n    load()\n"
    v = _run(tmp_path, {"check.py": body}, "check.py", "python_files = check.py\n")
    assert v.fail_kind == {"check.py::test_a": "env"}


def test_a_raising_frame_whose_file_does_not_exist_stays_env(tmp_path):
    """The innermost frame names a checkout path with no file (compiled source): not code."""
    ghost = "def load():\n    raise MissingThing(2, 'gone')\n"
    lib = LIB.split("def load")[0] + f"exec(compile({ghost!r}, __file__ + '.ghost.py', 'exec'))\n"
    v = _run(tmp_path, {"lib.py": lib, "tests/test_x.py": USE})
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_realpath_is_bound_when_the_plugin_is_imported(tmp_path):
    """A test that later replaces os.path.realpath does not move the checkout boundary."""
    head = (
        "import os.path\nreal = os.path.realpath\n"
        "os.path.realpath = lambda p, **k: '/elsewhere' if str(p).endswith('lib.py') else real(p)\n"
    )
    v = _run(tmp_path, {"lib.py": LIB, "tests/test_x.py": head + USE})
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {}
