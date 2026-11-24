"""Source of the probe test module and of the deliberately leaky guard variants (#1671, review of
#1698). Not a test file: ``test_collect_indistinguishable_1671`` commits ``PROBE`` into a
checkout and runs it as a quiet collect run and as a real run.

The probe records, at import, in a dynamic fixture scope called with the config and in a lazy
parametrize iterable, a normalised observation of every surface the code under test can reach
(plugin manager, closures and defaults of the guard's functions, gc, module names, environment,
descriptors, arguments, paths, threads), and returns them as parametrize ids, so they come back in
``run.collected``. Equal id sets mean the two runs cannot be told apart through these surfaces."""

PROBE = r"""
import gc
import hashlib
import inspect
import os
import re
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

GUARD = "_cairnwork_report_guard"
OBS = {}
STATE = {}


def _norm(text):
    text = re.sub(r"\.[A-Za-z0-9_]{8}(?=/|$)", ".R", str(text))  # mkdtemp suffix
    text = re.sub(r"[0-9a-f]{8,}", "H", text)
    return re.sub(r"\d+", "N", text)


def _put(phase, key, value):
    text = _norm(value)
    digest = hashlib.sha1(text.encode()).hexdigest()[:10]
    OBS[f"{phase}:{key}"] = text[:60] + "#" + digest


def _short(value):
    scalar = type(value) in (bool, int, str, float, type(None))
    return repr(value) if scalar else type(value).__name__


def _function(label, func, phase):
    try:
        cells = []
        for cell in func.__closure__ or ():
            try:
                cells.append(_short(cell.cell_contents))
            except ValueError:
                cells.append("empty")
        closure = inspect.getclosurevars(func)
        _put(phase, f"fn:{label}", repr([
            func.__code__.co_freevars, cells, [_short(v) for v in func.__defaults__ or ()],
            sorted((func.__kwdefaults__ or {}).items(), key=str), sorted(closure.nonlocals),
            sorted(closure.globals), repr(getattr(func, "pytest_impl", None)),
            str(inspect.signature(func)),
        ]))
    except Exception as exc:  # noqa: BLE001
        _put(phase, f"fn:{label}", "ERR " + type(exc).__name__)


def _functions(label, owner, phase):
    for name in sorted(dir(owner)):
        try:
            member = getattr(owner, name)
        except Exception:  # noqa: BLE001
            continue
        member = getattr(member, "__func__", member)
        if inspect.isfunction(member):
            _function(f"{label}.{name}", member, phase)


def _ours(plugin):
    module = getattr(type(plugin), "__module__", "") or ""
    if inspect.ismodule(plugin):
        module = plugin.__name__
    return not module.split(".")[0] in ("_pytest", "pytest", "pluggy", "builtins", "xdist")


def _stub(config):
    return SimpleNamespace(testsfailed=0, items=[], config=config, Interrupted=RuntimeError,
                           shouldfail=False, shouldstop=False)


def _config():
    frame = sys._getframe()
    while frame is not None:
        config = getattr(frame.f_locals.get("self"), "config", None)
        if config is not None and hasattr(config, "pluginmanager"):
            return config
        frame = frame.f_back
    return None


def _private(phase):
    # what shows beside HOME: the private directory's names, whether the Core run's mark exists
    # (a plain stat, which the sandbox allows), and the names of the run root above it
    try:
        private = Path(os.environ["HOME"]).parents[1]
    except Exception as exc:  # noqa: BLE001
        _put(phase, "private", "ERR " + type(exc).__name__)
        return
    _put(phase, "private-evidence-exists", repr((private / ".evidence").exists()))
    for label, directory in (("private-names", private), ("run-root-names", private.parent)):
        try:
            names = sorted({_norm(name) for name in os.listdir(directory)})
        except Exception as exc:  # noqa: BLE001
            names = ["ERR " + type(exc).__name__]
        _put(phase, label, repr(names))


def observe(phase, config):
    if config is not None:
        manager = config.pluginmanager
        names = sorted((name, type(plugin).__qualname__) for name, plugin in
                       manager.list_name_plugin())
        _put(phase, "plugin-names", repr(names))
        for name, plugin in manager.list_name_plugin():
            if not _ours(plugin):
                continue
            attrs = {} if inspect.ismodule(plugin) else vars(plugin)
            _put(phase, f"plugin:{name}", repr([
                type(plugin).__module__, type(plugin).__qualname__,
                sorted((k, _short(v)) for k, v in attrs.items()),
                sorted(k for k in vars(type(plugin)) if not k.startswith("__")),
            ]))
            _functions(f"plugin:{name}", plugin, phase)
            for hook in ("pytest_runtestloop", "pytest_runtest_protocol"):
                call = getattr(plugin, hook, None)
                if callable(call):
                    try:
                        args = (_stub(config),) if hook == "pytest_runtestloop" else (None, None)
                        got = call(*args)
                    except BaseException as exc:  # noqa: BLE001
                        got = "EXC " + type(exc).__name__
                    _put(phase, f"call:{name}.{hook}", repr(got))
        _put(phase, "invocation", repr(list(config.invocation_params.args)))
        options = sorted((k, _short(v)) for k, v in vars(config.option).items())
        _put(phase, "option", repr(options))
        hooks = getattr(manager.hook, "pytest_runtestloop").get_hookimpls()
        _put(phase, "runtestloop-impls",
             repr([(i.plugin_name, i.tryfirst, i.trylast) for i in hooks]))
    module = sys.modules.get(GUARD)
    if module is not None:
        _put(phase, "guard-vars", repr(sorted((k, _short(v)) for k, v in vars(module).items()
                                             if not k.startswith("__"))))
        _functions("guard", module, phase)
    seen = []
    try:  # the verifier's audit hook refuses the object-graph walks; the refusal is the same
        for obj in gc.get_objects():
            if getattr(type(obj), "__module__", "") == GUARD:
                attrs = vars(obj) if hasattr(obj, "__dict__") else {}
                seen.append((type(obj).__qualname__,
                             sorted((k, _short(v)) for k, v in attrs.items())))
                if not inspect.isfunction(obj):
                    _functions(f"gc:{type(obj).__qualname__}", obj, phase)
    except Exception as exc:  # noqa: BLE001
        seen.append(("refused", type(exc).__name__))
    _put(phase, "gc-objects", repr(sorted(seen, key=repr)))
    _put(phase, "env-keys", repr(sorted(os.environ)))
    for key in sorted(os.environ):
        _put(phase, f"env:{key}", os.environ[key])
    fds = []
    for fd in range(3, 64):
        try:
            fds.append((stat.S_IFMT(os.fstat(fd).st_mode), os.fstat(fd).st_size))
        except OSError:
            continue
    _put(phase, "fds", repr(fds))
    _put(phase, "argv", repr(sys.argv))
    _put(phase, "paths", repr([os.getcwd(), os.path.realpath(os.getcwd()), __file__,
                               os.environ.get("HOME"), os.environ.get("TMPDIR"), sys.path]))
    _put(phase, "threads", repr(sorted(t.name for t in threading.enumerate())))
    _private(phase)  # the run's private directory sits two levels above HOME (#1698)
    _put(phase, "modules", repr(sorted(n for n in sys.modules if "cairnwork" in n.lower())))


observe("import", _config())


def _scope(*, fixture_name, config):
    STATE["config"] = config
    observe("scope", config)
    return "function"


@pytest.fixture(scope=_scope)
def probe_fixture():
    pass


class Lazy:
    def __iter__(self):
        observe("lazy", STATE.get("config"))
        return iter(sorted(f"{k}={v}" for k, v in OBS.items()))


def _id(value):
    return re.sub(r"[^A-Za-z0-9_.:=/#-]", "_", value)


@pytest.mark.parametrize("obs", Lazy(), ids=_id)
def test_obs(obs):
    assert obs
"""

# Each leak is code appended to the guard module. It reads LEAKBIT, which the test's patched
# _prepare_run sets to "q" for the quiet run and "r" for the real one.
_BIT = "_BIT = os.environ.pop('LEAKBIT', None) == 'q'\n"
_REGISTER = (
    "_orig_configure = pytest_configure\n"
    "def pytest_configure(config):\n"
    "    _orig_configure(config)\n"
    "    config.pluginmanager.register({plugin}, 'cairnwork_leak')\n"
)
LEAKS = {
    "plugin-attribute": _BIT
    + "class _Leak:\n    def __init__(self):\n        self.flag = _BIT\n"
    + _REGISTER.format(plugin="_Leak()"),
    "closure-bit": _BIT
    + "def _mk(bit):\n    class _L:\n        def pytest_runtestloop(self, session):\n"
    "            return True if bit else None\n    return _L()\n"
    + _REGISTER.format(plugin="_mk(_BIT)"),
    "default-argument-bit": _BIT
    + "class _L:\n    def pytest_collection_finish(self, session, _q=_BIT):\n        return None\n"
    + _REGISTER.format(plugin="_L()"),
    "hook-return-differs": _BIT
    + "class _L:\n    def pytest_runtestloop(self, session):\n        return None if not _BIT"
    " else True\n" + _REGISTER.format(plugin="_L()"),
    "module-variable": _BIT + "_MODE = 'collect' if _BIT else 'run'\n",
    "environment-variable": "",
}
# the bit also travels as an environment variable that the guard never scrubs
ENV_LEAK = "environment-variable"
# a quiet run's directory name says what it is (the pre-#1698 name carried the run id)
PATH_LEAK = "directory-name"
