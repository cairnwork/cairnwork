"""#1368: the report guard reads values a test controls (an xfail reason, a fixture name, a
pytest.exit status, a code object's file and function name, a class namespace key, a marker) only
after an exact-type check, or through a builtin copy; anything else is recorded on the fail-closed
side. No method of the test's own objects may run inside the guard."""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# pyright: reportIncompatibleMethodOverride=false, reportAssignmentType=false
# (hostile test doubles: they break the declared types on purpose)

import os
import types

import pytest
from _pytest.reports import TestReport
from test_fail_kind_guard import T, _run

from cairnwork.report_denied import DENIED_SOURCE
from cairnwork.report_guard_parts import PARTS_SOURCE


def _plugin(source):
    namespace = {"errno": __import__("errno"), "socket": __import__("socket")}
    namespace.update({"os": __import__("os"), "sys": __import__("sys"), "pytest": pytest})
    exec(source, namespace)
    return namespace


def _skipped(nodeid="t.py::a"):
    return TestReport(nodeid, ("t.py", 0, "a"), {}, "skipped", ("t.py", 1, "Skipped: why"), "call")


@pytest.mark.parametrize("reason_kind", ["object", "str_subclass"])
def test_record_skip_takes_no_truth_value_of_a_non_str_xfail_reason(reason_kind):
    plugin = _plugin(PARTS_SOURCE)
    calls = []

    class Reason:
        def __bool__(self):
            calls.append("bool")
            return True

    class ReasonStr(str):
        def __len__(self):
            calls.append("len")
            return 1

    report = _skipped()
    report.wasxfail = Reason() if reason_kind == "object" else ReasonStr("")
    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    plugin["_record_skip"](reporter, report)
    assert calls == []
    assert reporter._skips == {"t.py::a": {"kind": "runtime", "reason": "why"}}  # fail closed


def test_record_skip_keeps_the_str_xfail_reason_behaviour():
    plugin = _plugin(PARTS_SOURCE)
    reporter = types.SimpleNamespace(_skips={}, _item_marks={})
    xfailed = _skipped("t.py::x")
    xfailed.wasxfail = "flaky"
    plugin["_record_skip"](reporter, xfailed)
    plugin["_record_skip"](reporter, _skipped("t.py::s"))
    assert reporter._skips == {"t.py::s": {"kind": "runtime", "reason": "why"}}


def _hostile_frame_tb(tmp_path, calls):
    class Name(str):
        def startswith(self, *args):
            calls.append("startswith")
            return True

        def __eq__(self, other):
            calls.append("eq")
            return str.__eq__(self, other)

        __hash__ = str.__hash__

    def raiser():
        raise PermissionError(1, "denied", "/x")

    top = os.path.realpath(tmp_path)
    code = raiser.__code__.replace(
        co_filename=Name(os.path.join(top, "test_a.py")), co_name=Name("raiser")
    )
    try:
        types.FunctionType(code, {})()
    except PermissionError as denied:
        return top, denied.__traceback__
    raise AssertionError("not raised")


def test_checkout_frame_reads_a_str_subclass_file_and_name_as_plain_text(tmp_path):
    plugin = _plugin(PARTS_SOURCE)
    calls = []
    top, tb = _hostile_frame_tb(tmp_path, calls)
    origin, line = plugin["_checkout_frame"](tb, top)
    assert calls == []
    assert origin == "test_a.py" and type(origin) is str and line > 0
    assert plugin["_checkout_rel"](tb.tb_next.tb_frame.f_code, top) == "test_a.py"
    assert calls == []


def test_denied_inside_doubts_a_class_namespace_key_that_is_not_a_str():
    plugin = _plugin(DENIED_SOURCE)
    calls = []

    class Key(str):
        def __eq__(self, other):
            calls.append("eq")
            return str.__eq__(self, other)

        def __hash__(self):
            return hash("errno")

    odd = type("Odd", (FileNotFoundError,), {Key("errno"): 1})
    assert plugin["_denied_inside"](odd(2, "missing"))  # doubt: fail closed
    assert calls == []
    plain = type("Plain", (FileNotFoundError,), {"note": 1})
    assert not plugin["_denied_inside"](plain(2, "missing"))


def test_a_non_str_missing_fixture_name_is_not_looked_up(tmp_path):
    verdict = _run(
        tmp_path,
        "import pytest\n"
        "class Name:\n"
        "    def __hash__(self):\n"
        "        return hash('anyio_backend')\n"
        "    def __eq__(self, other):\n"
        "        if other == 'anyio_backend':  # only the guard's fixture table holds this name\n"
        "            open('eq-ran', 'w').close()\n"
        "        return False\n"
        "@pytest.fixture\n"
        "def broken(request):\n"
        "    return request.getfixturevalue(Name())\n"
        "def test_a(broken):\n"
        "    pass\n",
    )
    assert verdict.fail_kind == {T + "test_a": "setup"}
    assert not (tmp_path / "eq-ran").exists()


def test_a_non_int_exit_status_is_not_converted_and_fails_closed(tmp_path):
    verdict = _run(
        tmp_path,
        "import pytest\n"
        "class Status:\n"
        "    def __int__(self):\n"
        "        return 0\n"
        "@pytest.fixture(scope='session', autouse=True)\n"
        "def leave():\n"
        "    yield\n"
        "    pytest.exit('bye', returncode=Status())\n"
        "def test_a():\n"
        "    pass\n",
    )
    assert not verdict.ran and verdict.exitstatus is None


def test_an_exact_int_exit_status_is_still_recorded(tmp_path):
    verdict = _run(tmp_path, "def test_a():\n    pass\n")
    assert verdict.ran and verdict.exitstatus == 0


def test_a_mark_subclass_is_not_marker_skip_evidence(tmp_path):
    verdict = _run(
        tmp_path,
        "import pytest\n"
        "from _pytest.mark.structures import Mark\n"
        "class Odd(Mark):\n"
        "    pass\n"
        "pytestmark = [Odd('skip', (), {'reason': 'odd'}, _ispytest=True)]\n"
        "def test_a():\n"
        "    pass\n",
    )
    assert verdict.skips == {T + "test_a": ("runtime", "odd")}


def test_a_skip_marker_is_still_marker_skip_evidence(tmp_path):
    verdict = _run(
        tmp_path,
        "import pytest\n@pytest.mark.skip(reason='plain')\ndef test_a():\n    pass\n",
    )
    assert verdict.skips == {T + "test_a": ("marker", "plain")}
