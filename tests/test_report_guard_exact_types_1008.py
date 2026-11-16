"""Candidate reachable exception/report values must not run hooks in report guard helpers."""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# pyright: reportIncompatibleMethodOverride=false, reportAssignmentType=false
# (hostile test doubles: they break the declared types on purpose)

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


def test_denied_inside_repeated_member_is_not_a_denial():
    """Codex r1: 65 repeats of one plain exception are one distinct exception, as on main."""
    plugin = _plugin(DENIED_SOURCE)
    one = ValueError(1)
    assert not plugin["_denied_inside"](BaseExceptionGroup("rep", [one] * 65))


def test_report_cause_reads_no_str_subclass_when_and_node_paths_need_exact_paths():
    """Codex r1: a replaced ``report.when`` or ``node.path`` runs none of its own code."""
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class When(str):
        def __eq__(self, other):
            calls.append("eq")
            return True

        def __ne__(self, other):
            calls.append("ne")
            return False

        __hash__ = str.__hash__

    call = CallInfo.from_call(lambda: 1, "collect")
    report = TestReport("n", ("f", 0, "n"), {}, "passed", None, "collect", [])
    report.when = When("collect")
    assert plugin["_report_cause"](call, report) is None and calls == []

    class Node:
        path = "not-a-path"
        config = type("C", (), {"rootpath": "/"})()

    assert plugin["_node_paths"](Node()) is None

    class Hooked:
        path = __import__("pathlib").PosixPath("/x")

        @property
        def config(self):
            calls.append("config")
            return type("C", (), {"rootpath": property(lambda s: calls.append("root"))})()

    node = Hooked()
    assert plugin["_node_paths"](node) is None
    assert "root" not in calls  # the replaced config's rootpath hook never ran


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


def test_exception_attribute_hook_is_line_based():
    """The scan reads one added line at a time: dunder definitions and one-line property
    assignments match; an ordinary method or function named path/name/args does not."""
    import re

    from cairnwork.tamper_scan_exception import EXCEPTION_ATTRIBUTE_HOOK as rule

    for bad in ("    def __cause__(self):", "    def __getattribute__(self, n):",
                "    errno = property(lambda self: 1)", "__class__ = property(f)"):  # fmt: skip
        assert re.search(rule, bad), bad
    for ok in ("def path(root):", "    def name(self):", "def args(parser):",
               "async def path(self):", "def errno_text(x):", "    @property"):  # fmt: skip
        assert not re.search(rule, ok), ok


class _Meta(type):
    """A candidate metaclass: any comparison or subclass probe on its classes is recorded."""

    calls: list = []

    def __eq__(cls, other):
        _Meta.calls.append("eq")
        return True

    def __hash__(cls):
        return id(cls)

    def __subclasscheck__(cls, sub):
        _Meta.calls.append("subclasscheck")
        return True

    def __instancecheck__(cls, obj):
        _Meta.calls.append("instancecheck")
        return True


def test_report_cause_and_innermost_run_no_metaclass_code():
    """Codex r1 / Claude review: ``in (A, B)`` compares with ==, so a report class or an error
    class with a candidate metaclass ran its __eq__; identity checks run nothing."""
    plugin = _plugin(PARTS_SOURCE)
    _Meta.calls.clear()
    hostile_report = _Meta("R", (TestReport,), {})
    report = hostile_report("n", ("f", 0, "n"), {}, "passed", None, "collect", [])
    call = CallInfo.from_call(lambda: 1, "collect")
    assert plugin["_report_cause"](call, report) is None
    hostile_error = _Meta("C", (ImportError,), {})
    assert plugin["_innermost"](hostile_error("x")) == ""
    assert _Meta.calls == []


def test_env_exc_reads_the_real_class_not_a_spoofed_one():
    from cairnwork.env_failure import ENV_SOURCE

    plugin = _plugin(ENV_SOURCE)
    calls = []

    class Spoof(Exception):
        @property
        def __class__(self):
            calls.append("class")
            return OSError

    info = type("Info", (), {"value": Spoof("x")})()
    plugin["_env_exc"](info, "/")
    assert calls == []


def test_a_wrapped_oserror_that_replaces_errno_is_doubt_and_runs_no_code():
    """The walk reads errno through the built-in descriptor, so a property on a test's OSError
    subclass never runs; main failed closed when that read raised, so a class that replaces
    errno is still doubt (True) here."""
    plugin = _plugin(DENIED_SOURCE)
    calls = []

    class Odd(OSError):
        errno = property(lambda self: calls.append("errno"))

    assert plugin["_denied_inside"](ExceptionGroup("g", [Odd()])) is True
    assert calls == []
    assert plugin["_denied_inside"](ExceptionGroup("g", [FileNotFoundError(2, "x")])) is False
