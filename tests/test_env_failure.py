"""#808: which failed calls are environment failures (env_failure.py), on exception objects."""

import importlib.util
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cairnwork.base_teeth import base_teeth
from cairnwork.env_failure import ENV_SOURCE, is_env_failure


def info(exc):
    return SimpleNamespace(value=exc)


def chained(outer, inner, *, how="cause"):
    """``outer`` raised from ``inner`` (cause), while handling it (context), or with the context
    suppressed (``from None``)."""
    if how == "cause":
        outer.__cause__ = inner
    else:
        outer.__context__ = inner
        outer.__suppress_context__ = how == "none"
    return outer


def module_in(root, rel, source, name):
    """A module imported from a file under root, so classes in it are defined by that file."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path):
    yield tmp_path
    for name in [n for n in sys.modules if n.startswith("_env808_")]:
        del sys.modules[name]


@pytest.mark.parametrize(
    "exc",
    [
        MemoryError("limit"),
        RecursionError("deep"),
        OSError(28, "no space"),
        FileNotFoundError("gone"),
        chained(RuntimeError("cannot load"), FileNotFoundError("gone")),
        chained(RuntimeError("cannot load"), FileNotFoundError("gone"), how="context"),
        chained(ValueError("a"), chained(KeyError("b"), MemoryError())),
        chained(RuntimeError("x"), RecursionError()),
        chained(RuntimeError("x"), OSError(1, "denied"), how="context"),
    ],
)
def test_builtin_environment_errors_are_found_anywhere_in_the_chain(exc, root):
    assert is_env_failure(info(exc), root) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("plain"),
        KeyError("k"),
        RuntimeError("plain"),
        AssertionError("assert 3 == 2"),
        chained(RuntimeError("x"), FileNotFoundError("gone"), how="none"),  # context suppressed
        chained(AssertionError("assert in except"), FileNotFoundError("gone"), how="context"),
        chained(AssertionError("assert in except"), MemoryError(), how="cause"),
        chained(ValueError("a"), chained(KeyError("b"), IndexError())),
    ],
)
def test_other_failures_are_not_environment(exc, root):
    assert is_env_failure(info(exc), root) is False


def test_an_oserror_that_is_also_an_assertion_error_stays_environment(root):
    # #808 review: main judged this env (isinstance OSError); the AssertionError branch must not win
    class Both(OSError, AssertionError):
        pass

    assert is_env_failure(info(Both("both")), root) is True


def test_an_assertion_wrapping_a_sandbox_denial_still_counts_as_one(root):
    # #854: the denial rule is unchanged and still applies to an AssertionError
    exc = chained(AssertionError("x"), PermissionError(1, "denied"), how="context")
    assert is_env_failure(info(exc), root) is True


@pytest.mark.parametrize(
    ("code", "stderr", "output", "expected"),
    [
        (127, None, None, True),
        (126, None, None, True),
        (-9, None, None, True),
        (-11, None, None, True),
        (1, b"sh: 1: nosuch: not found\n", None, True),
        (1, None, "bash: ./x: Permission denied", True),
        (2, b"fork: Cannot allocate memory", None, True),
        (1, b"Operation not permitted", None, True),
        (1, None, None, False),
        (2, b"Traceback ...\nAssertionError\n", b"ok", False),
        (0, None, None, False),
        (255, "boom", "", False),
    ],
)
def test_a_failed_child_is_environment_only_when_it_could_not_run(
    code, stderr, output, expected, root
):
    exc = subprocess.CalledProcessError(code, ["x"], output=output, stderr=stderr)
    assert is_env_failure(info(exc), root) is expected
    wrapped = chained(RuntimeError("setup"), exc)
    assert is_env_failure(info(wrapped), root) is expected


def test_a_checkout_defined_subclass_is_environment_as_on_main(root):
    # #808 N2: main's errisinstance(OSError) matched these too; loosening is #797's gate
    mod = module_in(
        root,
        "src/mine.py",
        "class MyOSError(OSError):\n    pass\n"
        "class MyMemory(MemoryError):\n    pass\n"
        "class MyRuntime(RuntimeError):\n    pass\n",
        "_env808_mine",
    )
    assert is_env_failure(info(mod.MyOSError("x")), root) is True
    assert is_env_failure(info(mod.MyMemory("x")), root) is True
    assert is_env_failure(info(chained(RuntimeError("x"), mod.MyOSError())), root) is True
    # a checkout error class that is none of those, wrapping nothing, stays a test failure
    assert is_env_failure(info(mod.MyRuntime("wraps nothing")), root) is False


def test_a_hostile_exception_class_runs_no_code_and_never_raises(root):
    mod = module_in(
        root,
        "src/odd.py",
        "class Odd(RuntimeError):\n"
        "    returncode = property(lambda self: 1 / 0)\n"
        "    def __eq__(self, other):\n        raise ZeroDivisionError\n"
        "    def __bool__(self):\n        raise ZeroDivisionError\n",
        "_env808_odd",
    )
    assert is_env_failure(info(mod.Odd("x")), root) is False
    assert is_env_failure(info(chained(mod.Odd("x"), OSError())), root) is True


def test_a_chain_past_the_bound_fails_closed(root):
    exc = ValueError("0")
    for i in range(70):
        exc = chained(ValueError(str(i)), exc)
    assert is_env_failure(info(exc), root) is True


@pytest.mark.parametrize("value", [None, "text", 3])
def test_no_exception_is_not_environment(value, root):
    assert is_env_failure(info(value), root) is False
    assert is_env_failure(None, root) is False


def test_the_plugin_text_defines_the_classifier_after_the_denial_helper():
    assert ENV_SOURCE.index("def _denied_inside") < ENV_SOURCE.index("def _env_exc")


def run_of(outcomes, kinds):
    return SimpleNamespace(outcomes=outcomes, fail_kind=kinds)


def test_base_teeth_names_every_kind_of_environment_failure():
    runs = [run_of({"t.py::a": "failed"}, {"t.py::a": "env"})]
    kind, code, detail = base_teeth(runs, {"t.py::a"})
    assert (kind, code) == ("UNCOMPARABLE", "BASE_ENV_ERROR")
    for word in ("OSError", "MemoryError", "RecursionError", "subprocess that could not run"):
        assert word in detail
    assert "t.py" in detail


def test_one_target_with_real_teeth_outweighs_an_environment_failure():
    runs = [run_of({"t.py::a": "failed", "t.py::b": "failed"}, {"t.py::a": "env"})]
    assert base_teeth(runs, {"t.py::a", "t.py::b"}) == ("TEETH", "", "")


def test_a_cycle_in_the_chain_ends_the_walk(root):
    a, b = ValueError("a"), ValueError("b")
    a.__context__, b.__context__ = b, a
    assert is_env_failure(info(a), root) is False
    b.__cause__ = OSError()
    assert is_env_failure(info(a), root) is True
