"""Candidate reachable exception/report values must not run hooks in report guard helpers."""

from _pytest.reports import TestReport
from _pytest.runner import CallInfo

from cairnwork.report_denied import DENIED_SOURCE
from cairnwork.report_guard_parts import PARTS_SOURCE


def _plugin(source):
    namespace = {"errno": __import__("errno"), "socket": __import__("socket")}
    namespace.update({"os": __import__("os"), "sys": __import__("sys")})
    exec(source, namespace)
    return namespace


def test_denied_inside_skips_hostile_exception_hooks_and_keeps_oserror_result():
    plugin = _plugin(DENIED_SOURCE)
    calls = []

    class Hostile(PermissionError):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    try:
        raise Hostile(1, "denied", "/tmp/x")
    except Hostile as denied:
        try:
            raise RuntimeError("wrapped") from denied
        except RuntimeError as wrapped:
            assert plugin["_denied_inside"](wrapped)
    assert calls == []
    try:
        raise RuntimeError("wrapped") from PermissionError(1, "denied", "/tmp/x")
    except RuntimeError as ordinary:
        assert plugin["_denied_inside"](ordinary)


def test_denied_inside_fails_closed_on_a_group_past_the_bound():
    """A group with more than 64 members is past the bound: yes, never a silent truncation."""
    plugin = _plugin(DENIED_SOURCE)
    wide = BaseExceptionGroup("wide", [ValueError(i) for i in range(65)])
    assert plugin["_denied_inside"](wide)
    narrow = BaseExceptionGroup("narrow", [ValueError(i) for i in range(3)])
    assert not plugin["_denied_inside"](narrow)
    denied = BaseExceptionGroup("denied", [ValueError(0), PermissionError(1, "denied")])
    assert plugin["_denied_inside"](denied)


def test_innermost_rejects_hostile_exception_before_traceback_read():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class Hostile(ImportError):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    try:
        raise Hostile("x")
    except Hostile as error:
        assert plugin["_innermost"](error) == ""
    assert calls == []

    try:
        raise ImportError("ordinary")
    except ImportError as error:
        expected = error.__traceback__.tb_frame.f_code.co_filename
        assert plugin["_innermost"](error) == expected
    try:
        raise ModuleNotFoundError("ordinary")
    except ModuleNotFoundError as error:
        expected = error.__traceback__.tb_frame.f_code.co_filename
        assert plugin["_innermost"](error) == expected


def test_import_error_does_not_read_hostile_report_or_exception():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class HostileReport(TestReport):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    class HostileImport(ImportError):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    report = object.__new__(HostileReport)
    call = CallInfo.from_call(lambda: None, when="collect")
    assert plugin["_import_error"](object(), call, report) is None
    try:
        raise ValueError("outer") from HostileImport("cannot import name 'x'")
    except ValueError as outer:
        call = CallInfo.from_call(lambda outer=outer: (_ for _ in ()).throw(outer), when="collect")
    assert plugin["_import_error"](object(), call, _ordinary_report()) is None
    assert calls == []


def test_submodule_error_does_not_read_hostile_report_or_exception():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class HostileReport(TestReport):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    class HostileMissing(ModuleNotFoundError):
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    report = object.__new__(HostileReport)
    call = CallInfo.from_call(lambda: None, when="collect")
    assert plugin["_submodule_error"](object(), call, report) is None
    try:
        raise ValueError("outer") from HostileMissing("No module named 'pkg.new'")
    except ValueError as outer:
        call = CallInfo.from_call(lambda outer=outer: (_ for _ in ()).throw(outer), when="collect")
    assert plugin["_submodule_error"](object(), call, _ordinary_report()) is None
    assert calls == []


def _ordinary_report():
    report = object.__new__(TestReport)
    vars(report).update(when="collect")
    return report
