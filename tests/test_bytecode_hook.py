"""The bytecode hook at the top of the report plugin (bytecode_hook.py, Issue #276).

Each case runs in its own child Python that installs the hook as the plugin does, tries one thing
and then checks that the hook still refuses both reading a ``.pyc`` and unmarshalling code. Every
interpreter ``uv python find`` locates from 3.9 to 3.14 runs them, as well as the one running these
tests; a version that is not installed is left out, and one whose marshal raises no audit event
(Apple's Python 3.9.6) must instead fail the hook's probe. The plugin end to end: a hook that
fails to install or does not answer its own probe leaves no verdict record (test_report_guard.py
drives the same plugin when it works).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork import report_guard
from cairnwork.bytecode_hook import HOOK_SOURCE, NAME
from cairnwork.verifiers import _prepare_run


def _interpreters():
    found = {sys.executable: f"{sys.version_info[0]}.{sys.version_info[1]}"}
    uv = shutil.which("uv")
    for minor in range(9, 15):
        if uv is None:
            break
        done = subprocess.run(
            [uv, "python", "find", "--no-project", f"3.{minor}"], capture_output=True, text=True
        )
        path = done.stdout.strip()
        if done.returncode == 0 and path and path not in found:
            found[path] = f"3.{minor}"
    return found


# Does this interpreter raise the marshal.loads audit event? Without it the hook cannot refuse
# unmarshalling, so its probe must fail (some builds of 3.9 lack the event).
AUDITS_MARSHAL = (
    "import marshal, sys\n"
    "seen = []\n"
    "sys.addaudithook(lambda event, args: seen.append(event))\n"
    "marshal.loads(marshal.dumps(1))\n"
    "print('marshal.loads' in seen)\n"
)


def _audits_marshal(python):
    done = subprocess.run([python, "-I", "-c", AUDITS_MARSHAL], capture_output=True, text=True)
    return done.stdout.strip() == "True"


FOUND = _interpreters()
INTERPRETERS = {path: v for path, v in FOUND.items() if _audits_marshal(path)}
SILENT = sorted(set(FOUND) - set(INTERPRETERS))

PRELUDE = f"""
import builtins, gc, io, marshal, os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import hooked
PROBE = os.path.join(sys.argv[2], "absent", "m.pyc")
NAME = {NAME!r}

def live():
    try:
        open(PROBE, "rb")
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        marshal.loads(marshal.dumps(compile("0", "probe", "eval")))
        return False
    except RuntimeError:
        return True

def hook_frame():
    try:
        open(PROBE, "rb")
    except PermissionError as error:
        tb = error.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        return tb.tb_frame
"""
EPILOGUE = """
try:
    step()
    outcome = "done"
except (OSError, RuntimeError) as error:  # the hook's own refusals carry its prefix
    text = error.strerror if isinstance(error, OSError) else str(error)
    by_hook = (text or "").startswith("cairnwork:")
    outcome = ("refused:" if by_hook else "error:") + type(error).__name__
print(outcome, "live" if live() else "NOT LIVE")
"""

# What each case tries, and whether the hook must refuse it (True) or let it happen (False).
CASES = {
    "gc_referrers_of_its_code": ("def step():\n    gc.get_referrers(hook_frame().f_code)\n", True),
    "gc_referents_of_its_frame": ("def step():\n    gc.get_referents(hook_frame())\n", True),
    "gc_all_objects": ("def step():\n    gc.get_objects()\n", True),
    "gc_referrers_of_a_default": (
        "def step():\n    gc.get_referrers(hook_frame().f_locals['_pyc'])\n",
        True,
    ),
    "frames_of_all_threads": ("def step():\n    sys._current_frames()\n", True),
    "function_code_set": (
        "def step():\n"
        "    f = eval(compile('lambda: 0', NAME, 'eval'))\n"
        "    f.__code__ = (lambda: 1).__code__\n",
        True,
    ),
    "function_defaults_set": (
        "def step():\n"
        "    f = eval(compile('lambda a=1: a', NAME, 'eval'))\n"
        "    f.__defaults__ = (2,)\n",
        True,
    ),
    "function_defaults_deleted": (
        "def step():\n"
        "    f = eval(compile('lambda a=1: a', NAME, 'eval'))\n"
        "    del f.__defaults__\n",
        True,
    ),
    "builtins_changed": (
        "def step():\n    builtins.type = lambda o: str\n    builtins.PermissionError = OSError\n",
        False,
    ),
    "its_globals_changed": (
        "def step():\n    hook_frame().f_globals.update(_perm=None, _t=None, _rmsg='x')\n",
        False,
    ),
    "finished_frame_locals_changed": (
        "def step():\n"
        "    frame = hook_frame()\n"
        "    frame.f_locals['_perm'] = None\n"
        "    frame.f_locals['_pyc'] = frozenset()\n",
        False,
    ),
    "str_subclass_path": (
        "calls = []\n"
        "class P(str):\n"
        "    __class__ = property(lambda self: calls.append('class') or str)\n"
        "    def __getitem__(self, key):\n"
        "        calls.append('item')\n"
        "        return 'x'\n"
        "    def rpartition(self, sep):\n"
        "        calls.append('rpartition')\n"
        "        return ('', '', 'x')\n"
        "    def __str__(self):\n"
        "        calls.append('str')\n"
        "        return 'x'\n"
        "def step():\n"
        "    try:\n"
        "        open(P(PROBE), 'rb')\n"
        "    finally:\n"
        "        assert not calls, calls\n",
        True,
    ),
    "bytes_subclass_path": (
        "class P(bytes):\n"
        "    def __getitem__(self, key):\n"
        "        raise AssertionError('called')\n"
        "def step():\n    os.open(P(os.fsencode(PROBE)), os.O_RDONLY)\n",
        True,
    ),
    "path_object": ("def step():\n    open(pathlib.Path(PROBE), 'rb')\n", True),
    "file_io_on_a_path_object": (
        "def step():\n    io.FileIO(pathlib.Path(sys.argv[2]) / 'absent.txt')\n",
        True,
    ),
    "relative_to_a_directory_descriptor": (
        "def step():\n"
        "    fd = os.open(sys.argv[2], os.O_RDONLY)\n"
        "    os.open('m.pyc', os.O_RDONLY, dir_fd=fd)\n",
        True,
    ),
    "upper_case_name": ("def step():\n    open(PROBE[:-3] + 'PyC', 'rb')\n", True),
    "read_write_descriptor": ("def step():\n    os.open(PROBE, os.O_RDWR)\n", True),
    "marshal_load_from_a_file": (
        "def step():\n    marshal.load(io.BytesIO(marshal.dumps(1)))\n",
        True,
    ),
    "marshal_loads_of_code_in_a_tuple": (
        "def step():\n    marshal.loads(marshal.dumps((compile('1', 'x', 'eval'),)))\n",
        False,  # a tuple is data; only a buffer that starts with code is refused
    ),
    "marshal_loads_of_data": ("def step():\n    marshal.loads(marshal.dumps({'a': [1]}))\n", False),
    "file_named_mem": ("def step():\n    open(os.path.join(sys.argv[2], 'mem'), 'w')\n", True),
    "symlink_to_mem": (
        "def step():\n    os.symlink('/proc/self/mem', os.path.join(sys.argv[2], 'm'))\n",
        True,
    ),
    "remote_debugger_script_event": (
        "def step():\n    sys.audit('cpython.remote_debugger_script', 'x')\n",
        True,
    ),
    "frozen_stdlib_modules_still_import": (
        "def step():\n    import ntpath, runpy, importlib.util, importlib.machinery\n",
        False,
    ),
    "writing_a_pyc": (
        "def step():\n    open(os.path.join(sys.argv[2], 'w.pyc'), 'wb').close()\n",
        False,
    ),
}


def run_case(python, tmp_path, body):
    (tmp_path / "hooked.py").write_text(HOOK_SOURCE)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    code = PRELUDE + body + EPILOGUE
    done = subprocess.run(
        [python, "-I", "-B", "-c", code, str(tmp_path), str(scratch)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


@pytest.mark.parametrize("python", sorted(INTERPRETERS), ids=INTERPRETERS.get)
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_hook_holds_after_each_step(tmp_path, python, case):
    body, refused = CASES[case]
    outcome, state = run_case(python, tmp_path, body)
    assert state == "live", (case, outcome)
    assert outcome.startswith("refused:") if refused else outcome == "done", (case, outcome)


def test_an_interpreter_without_the_marshal_event_fails_the_probe(tmp_path):
    """Where marshal raises no audit event the hook cannot refuse unmarshalling: its probe fails,
    and so does the plugin import (no verdict record). Nothing to check when no such interpreter
    is installed."""
    (tmp_path / "hooked.py").write_text(HOOK_SOURCE)
    load = "import sys\nsys.path.insert(0, sys.argv[1])\nimport hooked\n"
    for python in SILENT:
        done = subprocess.run(
            [python, "-I", "-B", "-c", load, str(tmp_path)], capture_output=True, text=True
        )
        assert done.returncode != 0 and "hook is not active" in done.stderr, (python, done.stderr)


STAND_INS = {
    "no-spec": "types.ModuleType('ntpath')",
    "other-origin": (
        "types.ModuleType('ntpath'); stand_in.__spec__ = importlib.machinery.ModuleSpec("
        "'ntpath', None, origin='/elsewhere/ntpath.py')"
    ),
}


@pytest.mark.parametrize("stand_in", sorted(STAND_INS))
@pytest.mark.parametrize("python", sorted(INTERPRETERS), ids=INTERPRETERS.get)
def test_a_frozen_module_name_held_by_another_module_fails_the_install(tmp_path, python, stand_in):
    """The frozen standard-library modules are imported before the hook from the interpreter's
    frozen table only (3.11+ lists them); a module of that name with no spec, or with a spec from
    anywhere else, fails closed."""
    (tmp_path / "hooked.py").write_text(HOOK_SOURCE)
    code = (
        "import _imp, importlib.machinery, sys, types\n"
        "if not hasattr(_imp, '_frozen_module_names'):\n"
        "    print('no list')\n"
        "    raise SystemExit\n"
        f"stand_in = {STAND_INS[stand_in]}\n"
        "sys.modules['ntpath'] = stand_in\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "try:\n"
        "    import hooked\n"
        "    print('installed')\n"
        "except RuntimeError as error:\n"
        "    print('refused' if 'ntpath' in str(error) else error)\n"
    )
    done = subprocess.run(
        [python, "-I", "-B", "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    listed = tuple(int(part) for part in INTERPRETERS[python].split(".")) >= (3, 11)
    assert done.stdout.strip() == ("refused" if listed else "no list"), done.stdout + done.stderr


@pytest.mark.parametrize("python", sorted(INTERPRETERS), ids=INTERPRETERS.get)
def test_the_hook_defaults_hold_only_immutable_values(tmp_path, python):
    body = (
        "def step():\n"
        "    frame = hook_frame()\n"
        "    names = frame.f_code.co_varnames[2:frame.f_code.co_argcount]\n"
        "    values = [frame.f_locals[n] for n in names]\n"
        "    plain = (str, bytes, int, slice)\n"
        "    def ok(v):\n"
        "        if type(v) is type:\n"
        "            return True\n"
        "        if type(v) is frozenset:\n"
        "            return all(type(x) in (str, bytes) for x in v)\n"
        "        return type(v) in plain\n"
        "    bad = [n for n, v in zip(names, values) if not ok(v)]\n"
        "    assert names and not bad, bad\n"
        "    assert frame.f_code.co_filename == NAME\n"
        "    assert frame.f_globals == {}\n"
    )
    outcome, state = run_case(python, tmp_path, body)
    assert (outcome, state) == ("done", "live")


# --- the plugin fails closed ----------------------------------------------------------------


def _plugin_run(tmp_path, change):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    plugin = guard / f"{report_guard.MODULE}.py"
    text = plugin.read_text()
    changed = change(text)
    assert changed != text
    plugin.chmod(0o600)
    plugin.write_text(changed)
    verdict = tmp_path / "verdict.json"
    env = {
        "PATH": "/usr/bin:/bin",
        report_guard.PATH_ENV: str(verdict),
        report_guard.NONCE_ENV: "n0nce",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini"]
    done = subprocess.run(
        [*argv, "-p", report_guard.MODULE, "tests/test_x.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return done, report_guard.read_verdict(verdict, "n0nce")


def test_the_unchanged_plugin_records_the_run(tmp_path):
    done, verdict = _plugin_run(tmp_path, lambda text: text + "\n")
    assert done.returncode == 0 and verdict.ran, done.stdout + done.stderr


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda t: t.replace("import sys as _cw_sys\n", "import sys as _cw_sys\n1 / 0\n", 1),
            id="hook_section_raises",
        ),
        pytest.param(
            lambda t: t.replace("_cw_sys.addaudithook(hook)", "pass", 1),
            id="hook_never_installed",
        ),
        pytest.param(
            lambda t: t.replace("version_info < (3, 9)", "version_info < (99, 0)", 1),
            id="interpreter_too_old",
        ),
    ],
)
def test_a_plugin_whose_hook_is_not_live_records_nothing(tmp_path, change):
    done, verdict = _plugin_run(tmp_path, change)
    assert done.returncode != 0, done.stdout
    assert not verdict.ran and not verdict.recorded
    assert not (tmp_path / "verdict.json").exists()


def test_the_run_environment_disables_remote_debugging(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    env = _prepare_run(private, Path(tmp_path / "pytest.ini"), b"")[3]
    assert env["PYTHON_DISABLE_REMOTE_DEBUG"] == "1"
