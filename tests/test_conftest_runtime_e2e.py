"""#986 step 6: the whole runtime path on real Seatbelt, end to end (AC1, AC2, AC5).

Every row runs ``deferred tests`` through ``conftest_run``: the owner's collect-only run on the
base with the recorder, then ``check_conftests`` with what it saw, then the verifiers. Three
groups: names only a run can see are refused (the runtime path), the #982 false refusals stay
fixed, and every fault of the path falls back to the static #916 rule and never to a looser one.
Run with CAIRNWORK_REQUIRE_SANDBOX=1; without a sandbox these rows are skipped, not passed.
"""

import pytest
from conftest_scenarios import BASE_CONFTEST, PLAIN, conftest_run
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)
from test_conftest_rebind_e2e import NEW_FIXTURE
from test_deferred_tests import FIX

from cairnwork import base_fixture_names, fixture_probe
from cairnwork_sandbox import select as _real_select

pytestmark = pytest.mark.sandbox

STATIC = "TEST_COMMIT_CONFTEST_STATIC"
LOGIN = "tests/sub/test_login.py"
EDIT = "tests/sub/test_edit.py"  # the file the test commit edits; it meets LOGIN by import
PROXY = "from test_login import *  # noqa: F403\n"
OVERRIDE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return None\n"
GUARD = (  # a base fixture that asks for the builtin monkeypatch
    "import pytest\n\n\n@pytest.fixture\ndef guard(monkeypatch):\n    return monkeypatch\n"
)
USES_GUARD = "def test_guard(guard):\n    assert guard is not None\n"
DEP_HELPER = "def verify_token(monkeypatch):\n    return True\n"
BOUND = "from helpers import verify_token\ntest_login = verify_token\n"
# a base conftest line gated on the recorder run alone: only that run sees the environment name
GATE = "import os\n\nGATED = bool(os.environ.get('CAIRNWORK_FIXTURE_RECORD_DIR'))\n"


def _verifiers(*edited):
    argv = ("python3", "-m", "pytest", "-q", "tests/test_regression.py", *edited)
    return (("pytest", argv),)


def _run(project, tmp_path, base, addition, edited, conftest="tests/sub/conftest.py", fix=FIX):
    """A test commit adding ``addition`` to ``conftest`` and editing the base files ``edited``."""
    base = {**base, EDIT: PROXY}
    extra = {p: base[p] + "\n# edited\n" for p in edited}
    return conftest_run(
        project, tmp_path, addition=addition, regression=PLAIN, fix=fix, path=conftest,
        base_files=base, extra_files=extra, verifiers=_verifiers(*edited),
    )  # fmt: skip


def _plugin_base(register):
    return {
        "tests/conftest.py": BASE_CONFTEST + register,
        "tests/helpers.py": GUARD,
        LOGIN: USES_GUARD,
    }


IMPORTED = "def pytest_configure(config):\n    config.pluginmanager.import_plugin('helpers')\n"
REGISTERED = (
    "def pytest_configure(config):\n    import helpers\n"
    "    config.pluginmanager.register(helpers)\n"
)
ALIASED = (
    "import helpers as h\n\n\ndef pytest_configure(config):\n    config.pluginmanager.register(h)\n"
)
SHAPES = {  # name: base files whose collected items request monkeypatch only at run time
    "#990 a plugin imported by name in pytest_configure": _plugin_base(IMPORTED),
    "#990 a plugin module registered in pytest_configure": _plugin_base(REGISTERED),
    "#990 a plugin module registered under an alias": _plugin_base(ALIASED),
    "#984 a helper bound under a test name by an aliased import": {
        "tests/conftest.py": BASE_CONFTEST,
        "tests/checks.py": "def guard(monkeypatch):\n    return monkeypatch\n",
        LOGIN: "from checks import guard as test_guard\n",
    },
    "#984 a helper bound as a module attribute": {
        "tests/conftest.py": BASE_CONFTEST,
        "tests/checks.py": "def guard(monkeypatch):\n    return monkeypatch\n",
        LOGIN: "import checks\ntest_guard = checks.guard\n",
    },
    "#984 a helper bound through globals()": {
        "tests/conftest.py": BASE_CONFTEST,
        LOGIN: "def guard(monkeypatch):\n    return monkeypatch\n\n\n"
        "test_guard = globals()['guard']\n",
    },
    "#984 a base hook collects check_* functions": {
        "tests/conftest.py": BASE_CONFTEST
        + "import pytest\n\n\ndef pytest_pycollect_makeitem(collector, name, obj):\n"
        "    if name.startswith('check_') and callable(obj):\n"
        "        return pytest.Function.from_parent(collector, name=name, callobj=obj)\n",
        LOGIN: "def check_it(monkeypatch):\n    assert monkeypatch is not None\n",
    },
    "#987 a helper outside the directory bound under a test name": {
        "tests/conftest.py": BASE_CONFTEST,
        "tests/helpers.py": DEP_HELPER,
        LOGIN: BOUND,
    },
}


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _addition(name):
    return OVERRIDE.format("monkeypatch")


@pytest.mark.parametrize("name", SHAPES)
def test_a_name_only_the_run_sees_is_refused_on_real_seatbelt(project, tmp_path, name):
    result = _run(project, tmp_path, SHAPES[name], _addition(name), [EDIT])
    assert result.status == "FAILED", result.reason
    assert STATIC in result.reason


def test_the_987_shape_is_accepted_without_the_run(project, tmp_path, monkeypatch):
    """The before column: only the run refuses it (main's static rule reads no test module)."""
    monkeypatch.setattr(base_fixture_names, "collect", lambda *a, **k: None)
    base = SHAPES["#987 a helper outside the directory bound under a test name"]
    result = _run(project, tmp_path, base, _addition("#987"), [EDIT])
    assert result.status == "COMPLETED", result.reason


# --- #982: the false refusals stay fixed with the run in the path -----------------------------

HELPER_PARAM = {  # a plain helper whose parameter is named like the new fixture
    "tests/conftest.py": BASE_CONFTEST,
    "tests/helpers.py": "def load(data):\n    return data\n",
    LOGIN: "from helpers import load\n\n\ndef test_login():\n    assert load(1) == 1\n",
}
DATA_FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef data():\n    return 1\n"


@pytest.mark.parametrize(
    ("base", "addition", "fix"),
    [
        (HELPER_PARAM, DATA_FIXTURE, FIX),
        (HELPER_PARAM, "import pytest\n" + NEW_FIXTURE, FIX),
        (SHAPES["#987 a helper outside the directory bound under a test name"],
         DATA_FIXTURE, FIX),
    ],
    ids=["#982 helper parameter", "#982 unrelated new fixture", "#987 shape, other name"],
)  # fmt: skip
def test_982_benign_fixtures_are_accepted(project, tmp_path, base, addition, fix):
    result = _run(project, tmp_path, base, addition, [EDIT], fix=fix)
    assert result.status == "COMPLETED", result.reason


# --- fault injection: every fault gives None and the static rule's verdict --------------------

RUNTIME_ONLY = SHAPES["#987 a helper outside the directory bound under a test name"]
STATIC_REFUSED = SHAPES["#984 a helper bound through globals()"]


@pytest.fixture
def spy(monkeypatch):
    """The names the base run recorded (a one-item list; None when none were obtained)."""
    seen: list = []
    real = base_fixture_names._probe

    def probe(*args, **kwargs):
        result = real(*args, **kwargs)
        run = result[0]
        seen.append(None if run is None else run.fixture_names)
        return result

    monkeypatch.setattr(base_fixture_names, "_probe", probe)
    return seen


def _boom(*args, **kwargs):
    raise RuntimeError("injected")


def _read_with(**override):
    real = fixture_probe.Probe.read_record

    def read(self, returncode, timed_out):
        return real(
            self, override.get("returncode", returncode), override.get("timed_out", timed_out)
        )

    return read


FORGED = (
    "import json, pathlib\n"
    "def pytest_sessionfinish(session):\n"
    "    if GATED:\n"
    "        d = pathlib.Path(os.environ['CAIRNWORK_FIXTURE_RECORD_DIR'])\n"
    "        (d / 'record.json').write_text(json.dumps({'names': [], 'complete': True}))\n"
)
LINKED = (
    "import pathlib\n"
    "def pytest_sessionfinish(session):\n"
    "    if GATED:\n"
    "        d = pathlib.Path(os.environ['CAIRNWORK_FIXTURE_RECORD_DIR']) / 'record.json'\n"
    "        d.unlink(missing_ok=True)\n"
    "        d.symlink_to('/dev/null')\n"
)
EXIT_ONE = "def pytest_sessionfinish(session):\n    if GATED:\n        session.exitstatus = 1\n"
# module level: a hook would make the static rule count every parameter
SLEEPY = "import time\n\nif GATED:\n    time.sleep(60)\n"
BROKEN = (
    "import os\n\nif os.environ.get('CAIRNWORK_FIXTURE_RECORD_DIR'):\n    raise ImportError('x')\n"
)


def _with_hook(hook, scenario):
    base = dict(scenario)
    base["tests/conftest.py"] = base["tests/conftest.py"] + GATE + hook
    return base


FAULTS = {  # name: (how to break the run, expects collect None)
    "the run raises": ("raise", None, True),
    "the process reports a timeout": ("read", {"timed_out": True}, True),
    "the exit code is True": ("read", {"returncode": True}, True),
    "the exit code is 3": ("read", {"returncode": 3}, True),
    "no sandbox backend": ("nosandbox", None, True),
    "a real timeout in the run": ("timeout", SLEEPY, True),
    "a real collection error in the base": ("module", BROKEN, True),  # residual: no reason code
    "a real exit code 1 from the recorder run": ("hook", EXIT_ONE, True),
    "a record replaced by a symlink": ("hook", LINKED, True),
    "a record forged empty after the last write": ("hook", FORGED, None),
}


def _break(monkeypatch, how, arg):
    if how == "raise":
        monkeypatch.setattr(base_fixture_names, "run_verifiers", _boom)
    elif how == "read":
        monkeypatch.setattr(fixture_probe.Probe, "read_record", _read_with(**arg))
    elif how == "nosandbox":
        monkeypatch.setattr("cairnwork_sandbox.select", lambda *a, **k: None)
    elif how == "timeout":
        real = base_fixture_names.run_verifiers
        monkeypatch.setattr(
            base_fixture_names, "run_verifiers", lambda *a, **k: real(*a, **k, timeout=4)
        )


@pytest.mark.parametrize("name", FAULTS)
@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [(RUNTIME_ONLY, "COMPLETED", ""), (STATIC_REFUSED, "FAILED", STATIC)],
    ids=["only the run refuses it", "the static rule refuses it"],
)
def test_a_fault_gives_the_static_verdict_never_a_looser_one(
    project, tmp_path, monkeypatch, spy, name, scenario, status, code
):
    how, arg, none = FAULTS[name]
    if how in ("hook", "timeout"):
        scenario = _with_hook(arg, scenario)
    elif how == "module":  # a base test module that cannot be collected in the recorder run
        scenario = {**scenario, LOGIN: arg + scenario[LOGIN]}
    if how == "nosandbox":
        monkeypatch.setattr("cairnwork_sandbox.select", lambda *a, **k: None)
    else:
        _break(monkeypatch, how, arg)
    result = _run(
        project, tmp_path, scenario, _addition("#987" if scenario is RUNTIME_ONLY else "x"), [EDIT]
    )
    if how == "nosandbox":
        assert result.status == "FAILED"  # nothing runs: not a pass, and no names
        assert not spy or spy[-1] is None
        return
    assert result.status == status, result.reason
    assert code in result.reason
    if none is not None:  # a forged record may be taken or not: it can only add refusals
        assert spy[-1] is None


def test_the_sandbox_is_real_here():
    """Without Seatbelt this file proves nothing: it must be named, not silently green."""
    assert _real_select() is not None
