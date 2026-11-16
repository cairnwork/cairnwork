"""#1308: the guards read a call's exception only from an exact pytest ExceptionInfo."""

from types import SimpleNamespace

import pytest
from conftest import real_info

from cairnwork.env_failure import is_env_failure
from cairnwork.report_guard_parts import PARTS_SOURCE

DENIED = "/p/x"


def _plugin() -> dict:
    plugin: dict = {"os": __import__("os")}
    exec(PARTS_SOURCE, plugin)
    return plugin


def _fakes(exc):
    sub = type("Sub", (type(real_info(exc)),), {})
    real = real_info(exc)
    fake_sub = sub.__new__(sub)
    fake_sub.__dict__.update(real.__dict__)
    return [
        SimpleNamespace(value=exc),
        fake_sub,
        SimpleNamespace(value=exc, _excinfo=(type(exc), exc, None)),
        exc,
    ]


def test_denied_reads_only_a_real_info():
    exc = PermissionError(1, "Operation not permitted", DENIED)
    plugin = _plugin()
    assert plugin["_denied_path"](real_info(exc)) == DENIED
    for fake in _fakes(exc):
        assert plugin["_denied_path"](fake) is None
        assert plugin["_denied_origin"](fake, "/r") is None
    assert plugin["_denied_path"](None) is None


def test_denied_refuses_a_malformed_excinfo_field():
    exc = PermissionError(1, "Operation not permitted", DENIED)
    plugin = _plugin()
    info = real_info(exc)
    object.__setattr__(info, "_excinfo", [type(exc), exc, None])
    assert plugin["_denied_path"](info) is None
    object.__setattr__(info, "_excinfo", (type(exc), exc))
    assert plugin["_denied_path"](info) is None


def test_env_failure_reads_only_a_real_info():
    assert is_env_failure(real_info(OSError(1, "x")), None) is True
    assert is_env_failure(real_info(ValueError("a test bug")), None) is False
    assert is_env_failure(None, None) is False
    for fake in _fakes(ValueError("a test bug")):
        assert is_env_failure(fake, None) is True  # unreadable: fail closed


def test_env_failure_refuses_a_malformed_excinfo_field():
    info = real_info(ValueError("a test bug"))
    object.__setattr__(info, "_excinfo", [ValueError, ValueError("a test bug"), None])
    assert is_env_failure(info, None) is True
    object.__setattr__(info, "_excinfo", (ValueError,))
    assert is_env_failure(info, None) is True


@pytest.mark.parametrize("exc", [ValueError("x"), AssertionError("y")])
def test_a_value_property_is_never_called(exc):
    calls = []

    class Spy:
        @property
        def value(self):
            calls.append(1)
            return exc

    is_env_failure(Spy(), None)
    _plugin()["_denied_path"](Spy())
    assert calls == []
