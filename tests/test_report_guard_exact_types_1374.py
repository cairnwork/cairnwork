"""#1374: report guard: remaining exact-type reads of test-controlled values (#1368 family).
Every candidate-controlled value is read only when it is the exact builtin type; otherwise
fails closed and none of its methods or properties run inside the guard."""
# (hostile test doubles: they break the declared types on purpose)

import types

import pytest
from _pytest.fixtures import FixtureLookupErrorRepr
from _pytest.reports import CollectReport, TestReport
from test_fail_kind_guard import _run

from cairnwork.report_guard_parts import PARTS_SOURCE


def _plugin(source):
    namespace = {"errno": __import__("errno"), "socket": __import__("socket")}
    namespace.update({"os": __import__("os"), "sys": __import__("sys"), "pytest": pytest})
    exec(source, namespace)
    return namespace


def _skipped(nodeid="t.py::a"):
    return TestReport(nodeid, ("t.py", 0, "a"), {}, "skipped", ("t.py", 1, "Skipped: why"), "call")


# 1. _record_skip: report.longrepr (tuple subclass indexing) and report.nodeid (dict lookup)


def test_record_skip_takes_no_methods_of_a_tuple_subclass_longrepr():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class LongreprTuple(tuple):
        def __getitem__(self, idx):
            calls.append("getitem")
            return super().__getitem__(idx)

    report = _skipped()
    report.longrepr = LongreprTuple(("t.py", 1, "Skipped: why"))
    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    plugin["_record_skip"](reporter, report)
    assert calls == []
    assert reporter._skips == {"t.py::a": {"kind": "runtime", "reason": ""}}


def test_record_skip_calls_no_str_of_an_object_longrepr():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class LongreprObj:
        def __str__(self):
            calls.append("str")
            return "Skipped: why"

    report = _skipped()
    report.longrepr = LongreprObj()  # pyright: ignore[reportAttributeAccessIssue]
    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    plugin["_record_skip"](reporter, report)
    assert calls == []
    assert reporter._skips == {"t.py::a": {"kind": "runtime", "reason": ""}}


def test_record_skip_looks_up_no_non_str_nodeid_in_item_marks():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class NodeId(str):
        def __hash__(self):
            calls.append("hash")
            return super().__hash__()

        def __eq__(self, other):
            calls.append("eq")
            return super().__eq__(other)

    report = _skipped()
    report.nodeid = NodeId("t.py::a")
    reporter = types.SimpleNamespace(_skips={}, _item_marks={"t.py::a": True})
    plugin["_record_skip"](reporter, report)
    assert calls == []
    assert reporter._skips == {}


def test_record_skip_keeps_exact_tuple_and_str_behaviour():
    plugin = _plugin(PARTS_SOURCE)
    reporter = types.SimpleNamespace(_skips={}, _item_marks={"t.py::m": True})
    rep1 = _skipped("t.py::m")
    rep2 = _skipped("t.py::s")
    rep2.longrepr = "Skipped: simple"
    plugin["_record_skip"](reporter, rep1)
    plugin["_record_skip"](reporter, rep2)
    assert reporter._skips == {
        "t.py::m": {"kind": "marker", "reason": "why"},
        "t.py::s": {"kind": "runtime", "reason": "simple"},
    }


# 2. pytest_sessionfinish record: session.testsfailed copied without an exact int check


def test_sessionfinish_records_no_testsfailed_when_not_exact_int(tmp_path):
    verdict = _run(
        tmp_path,
        "import pytest\n"
        "class Count(int):\n"
        "    pass\n"
        "@pytest.fixture(scope='session', autouse=True)\n"
        "def corrupt(request):\n"
        "    request.session.testsfailed = Count(0)\n"
        "def test_a():\n"
        "    pass\n",
    )
    assert not verdict.ran and verdict.testsfailed is None


def test_sessionfinish_keeps_exact_int_testsfailed(tmp_path):
    verdict = _run(tmp_path, "def test_a():\n    pass\n")
    assert verdict.ran and verdict.testsfailed == 0


# 3. pytest_runtest_makereport wrapper: report object not checked to be exact TestReport


def test_makereport_reads_no_attributes_of_a_testreport_subclass():
    from cairnwork.report_guard import _PLUGIN_SOURCE

    plugin = _plugin(_PLUGIN_SOURCE)
    reporter = plugin["_Reporter"]("/path", None, "nonce")
    calls = []

    class SubReport(TestReport):
        def __init__(self):
            pass

        @property
        def nodeid(self):
            calls.append("nodeid")
            return "t.py::a"

        @property
        def when(self):  # pyright: ignore[reportIncompatibleVariableOverride]
            calls.append("when")
            return "call"

    item = types.SimpleNamespace(nodeid="t.py::a")
    reporter._sources[id(item)] = (item, "t.py::a", "t.py")
    call = types.SimpleNamespace(excinfo=None)

    gen = reporter.pytest_runtest_makereport(item, call)
    next(gen)
    sub = SubReport()
    try:
        gen.send(sub)
    except StopIteration:
        pass

    assert calls == []
    assert reporter._outcomes == {}


def test_makereport_keeps_exact_testreport():
    from cairnwork.report_guard import _PLUGIN_SOURCE

    plugin = _plugin(_PLUGIN_SOURCE)
    reporter = plugin["_Reporter"]("/path", None, "nonce")
    item = types.SimpleNamespace(nodeid="t.py::a")
    reporter._sources[id(item)] = (item, "t.py::a", "t.py")
    call = types.SimpleNamespace(excinfo=None)

    gen = reporter.pytest_runtest_makereport(item, call)
    next(gen)
    rep = TestReport("t.py::a", ("t.py", 0, "a"), {}, "passed", None, "call")
    try:
        gen.send(rep)
    except StopIteration:
        pass

    assert reporter._outcomes == {"t.py::a": "passed"}


# 4. report_guard_parts.py fixture-name / missing-fixture paths (argname, _missing)


def test_fixture_plugin_reads_no_argname_from_a_non_fixture_lookup_error_repr():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class FakeLongrepr:
        @property
        def argname(self):
            calls.append("argname")
            return "anyio_backend"

    table = {"anyio_backend": "anyio.pytest_plugin"}
    assert plugin["_fixture_plugin"](table, FakeLongrepr()) is None
    assert calls == []


def test_fixture_plugin_keeps_exact_fixture_lookup_error_repr():
    plugin = _plugin(PARTS_SOURCE)
    table = {"anyio_backend": "anyio.pytest_plugin"}
    repr_obj = FixtureLookupErrorRepr("arg", "cache", "filename", 1, "anyio_backend")  # pyright: ignore[reportArgumentType]
    assert plugin["_fixture_plugin"](table, repr_obj) == "anyio.pytest_plugin"


def test_missing_calls_no_methods_of_a_tuple_subclass_longrepr():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class LongreprTuple(tuple):
        def __getitem__(self, idx):
            calls.append("getitem")
            return super().__getitem__(idx)

    longrepr = LongreprTuple(("t.py", 1, "could not import 'foo'"))
    rep = CollectReport("t.py", "skipped", longrepr, None)
    assert plugin["_missing"](rep) == ""
    assert calls == []


def test_missing_returns_empty_when_report_is_not_collect_report():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class FakeReport:
        @property
        def longrepr(self):
            calls.append("longrepr")
            return ("t.py", 1, "could not import 'foo'")

    assert plugin["_missing"](FakeReport()) == ""
    assert calls == []


def test_missing_keeps_exact_collect_report():
    plugin = _plugin(PARTS_SOURCE)
    rep = CollectReport("t.py", "skipped", ("t.py", 1, "could not import 'foo'"), None)
    assert plugin["_missing"](rep) == "foo"


# 5. round 2 (Codex adversarial): outcome / when / nodeid of an exact report, and collectreport


class _Hostile:
    def __init__(self, calls):
        self.calls = calls

    def __eq__(self, other):
        self.calls.append("eq")
        return True

    def __ne__(self, other):
        self.calls.append("ne")
        return False

    def __hash__(self):
        self.calls.append("hash")
        return 1


def _makereport(field, value):
    from cairnwork.report_guard import _PLUGIN_SOURCE

    plugin = _plugin(_PLUGIN_SOURCE)
    reporter = plugin["_Reporter"]("/path", None, "nonce")
    item = types.SimpleNamespace(nodeid="t.py::a")
    reporter._sources[id(item)] = (item, "t.py::a", "t.py")
    rep = TestReport("t.py::a", ("t.py", 0, "a"), {}, "failed", None, "call")
    setattr(rep, field, value)
    gen = reporter.pytest_runtest_makereport(item, types.SimpleNamespace(excinfo=None))
    next(gen)
    try:
        gen.send(rep)
    except StopIteration:
        pass
    return reporter


def test_makereport_runs_no_method_of_a_hostile_outcome_when_or_nodeid():
    for field in ("outcome", "when", "nodeid"):
        calls = []
        reporter = _makereport(field, _Hostile(calls))
        assert calls == [], field
        assert reporter._outcomes == {} and reporter._renamed == [""], field  # fails closed


def test_makereport_rejects_a_str_subclass_outcome():
    calls = []

    class Outcome(str):
        def __eq__(self, other):
            calls.append("eq")
            return True

        __hash__ = str.__hash__

    reporter = _makereport("outcome", Outcome("failed"))
    assert calls == [] and reporter._renamed == [""]


def _collect(report):
    from cairnwork.report_guard import _PLUGIN_SOURCE

    reporter = _plugin(_PLUGIN_SOURCE)["_Reporter"]("/path", None, "nonce")
    reporter.pytest_collectreport(report)
    return reporter


def test_collectreport_reads_no_property_of_a_collectreport_subclass():
    calls = []

    class Sub(CollectReport):  # pyright: ignore[reportGeneralTypeIssues]
        @property
        def failed(self):
            calls.append("failed")
            return False

        @property
        def skipped(self):
            calls.append("skipped")
            return False

    reporter = _collect(Sub("t.py", "passed", None, None))
    assert calls == [] and reporter._collect_errors == [""]  # fail closed: a collection error


def test_collectreport_runs_no_method_of_a_hostile_outcome_or_nodeid():
    for field in ("outcome", "nodeid"):
        calls = []
        rep = CollectReport("t.py", "failed", None, None)
        setattr(rep, field, _Hostile(calls))
        reporter = _collect(rep)
        assert calls == [] and reporter._collect_errors == [""], field


def test_collectreport_keeps_exact_failed_and_skipped_reports():
    failed = _collect(CollectReport("t.py", "failed", None, None))
    assert failed._collect_errors == ["t.py"]
    skipped = _collect(
        CollectReport("u.py", "skipped", ("u.py", 1, "could not import 'foo'"), None)
    )
    assert skipped._collect_skipped == ["u.py"] and skipped._skipped_deps == ["foo"]
    assert skipped._collect_errors == []


def test_record_skip_runs_no_method_of_a_hostile_outcome():
    plugin = _plugin(PARTS_SOURCE)
    calls = []
    report = _skipped()
    report.outcome = _Hostile(calls)  # pyright: ignore[reportAttributeAccessIssue]
    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    plugin["_record_skip"](reporter, report)
    assert calls == [] and reporter._skips == {}


def test_record_skip_reads_no_attribute_of_a_non_testreport_double():
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class Hostile:
        def __getattribute__(self, name):
            calls.append(name)
            return "skipped"

    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    plugin["_record_skip"](reporter, Hostile())
    assert calls == []
    assert reporter._skips == {}
