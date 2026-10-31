"""tamper_scan: alias spellings, the most direct forgeries in library files, pycache_prefix.
Also changes to pytest's public classes (pytest_public_patch), in any file.

Out of scope: dynamic forms such as importlib.import_module('_' + 'pytest.runner'),
getattr(os, '_' + 'exit') or getattr(pytest, 'Test' + 'Report'), a rebinding by assignment
(m = sys.modules) and an alias bound only in an unchanged line of the file. A scan of added lines
cannot see these; other layers cover them.
"""

import pytest

from cairnwork.tamper_scan import scan_added_lines

TEST = "tests/test_x.py"
LIBRARY = "pkg/module.py"
BOTH = pytest.mark.parametrize("path", [TEST, LIBRARY])


def _rules(path, snippet):
    lines = [(i + 1, line) for i, line in enumerate(snippet.strip().splitlines())]
    return [f.rule for f in scan_added_lines(path, lines)]


@BOTH
@pytest.mark.parametrize(
    ("line", "expected_rule"),
    [
        ("sys.modules['pytest'] = fake", "sys_modules_write"),
        ("import _pytest.runner", "pytest_internals"),
        ("from _pytest.reports import TestReport", "pytest_internals"),
        ("import json, _pytest.runner", "pytest_internals"),
        ("import sys as s; s.modules['pytest'] = fake", "sys_modules_write"),
        ("import json, sys as s; s.modules['pytest'] = fake", "sys_modules_write"),
        ("from sys import modules; modules['pytest'] = fake", "sys_modules_write"),
        ("from sys import modules as m; m['pytest'] = fake", "sys_modules_write"),
        ("from sys import path, modules as m; del m['pytest']", "sys_modules_write"),
        ("import sys as s; s.modules.pop('pytest')", "sys_modules_write"),
        ("import sys as s; s.modules.setdefault('pytest', fake)", "sys_modules_write"),
        ("import sys as s; s.modules.update({'pytest': fake})", "sys_modules_write"),
        ("import os as o; o._exit(0)", "os_exit"),
        ("from os import _exit; _exit(0)", "os_exit"),
        ("from os import _exit as my_exit; my_exit(0)", "os_exit"),
        ("try: import sys as s\ns.modules['pytest'] = fake", "sys_modules_write"),
        ("from sys import (modules as m)\nm['pytest'] = fake", "sys_modules_write"),
    ],
)
def test_sensitive_targets_and_aliases_caught(path, line, expected_rule):
    assert _rules(path, line) == [expected_rule]


@pytest.mark.parametrize(
    "line",
    [
        "del sys.modules['pytest']",
        "del sys.modules['dep'], sys.modules['pluggy']",
        "sys.modules.pop('_pytest.runner')",
        "sys.modules.setdefault('pytest', fake)",
        "sys.modules.update({'pytest': fake})",
        'sys.modules["cairnwork.verifiers"] = fake',
        "sys.modules['os.path'] = fake",
    ],
)
def test_more_sensitive_key_writes_caught_in_library_code(line):
    assert _rules(LIBRARY, line) == ["sys_modules_write"]


def test_a_key_naming_pytest_internals_is_one_finding_per_rule_in_test_code():
    # main's _pytest rule reads the key too; in library code only the write is refused
    assert _rules(TEST, "sys.modules['_pytest.runner'] = fake") == [
        "sys_modules_write",
        "pytest_internals",
    ]
    assert _rules(LIBRARY, "sys.modules['_pytest.runner'] = fake") == ["sys_modules_write"]


@BOTH
@pytest.mark.parametrize(
    ("snippet", "expected_rule"),
    [
        ("import sys as s\ns.modules['pytest'] = fake", "sys_modules_write"),
        ("from sys import modules\nmodules['pytest'] = fake", "sys_modules_write"),
        ("import os as o\no._exit(0)", "os_exit"),
        ("from os import _exit\n_exit(0)", "os_exit"),
    ],
)
def test_multiline_alias_forms_caught(path, snippet, expected_rule):
    assert set(_rules(path, snippet)) == {expected_rule}


@BOTH
def test_an_alias_bound_earlier_is_followed_to_a_later_added_line(path):
    lines = [
        (3, "import sys as _s  # vendored shim"),
        (4, "x = 1"),
        (5, "# _s.modules['pytest'] = fake would be caught: comment lines are skipped"),
        (40, "    _s.modules['pytest'] = fake"),
    ]
    found = scan_added_lines(path, lines)
    assert [(f.line, f.rule) for f in found] == [(40, "sys_modules_write")]


@BOTH
@pytest.mark.parametrize(
    "line",
    [
        "sys.modules['yaml'] = stub",
        "from _pytest.monkeypatch import MonkeyPatch",
        "# os._exit(0)",
        "import os.path as osp; osp._exit = 1",  # os.path is not os
        "from pkg import sys as s; s.modules['pytest'] = fake",  # pkg.sys is not sys
        "import sys as s; obj.s.modules['pytest'] = fake",  # an attribute, not the alias
    ],
)
def test_allowed_forms_pass_in_both_paths(path, line):
    assert _rules(path, line) == []


@BOTH
def test_a_method_named_like_an_exit_alias_is_not_a_call(path):
    lines = [(1, "from os import _exit as e"), (2, "class C:"), (3, "    def e(self): pass")]
    assert [(f.line, f.rule) for f in scan_added_lines(path, lines)] == [(1, "os_exit")]


def test_library_code_keeps_variable_keys_and_stubs_through_an_alias():
    assert _rules(LIBRARY, "sys.modules[spec.name] = mod") == []
    assert _rules(LIBRARY, "import sys as s\ns.modules[spec.name] = mod") == []
    assert _rules(LIBRARY, "from sys import modules\nmodules['dep'] = stub") == []


@pytest.mark.parametrize(
    ("snippet", "caught"),
    [
        ("import sys as s\ns.modules['yaml'] = stub", False),  # like the plain-spelling stub
        ("from sys import modules as m\nm.pop('dep', None)", False),
        ("import sys as s\ns.modules[name] = m", True),  # main's breadth in test code
        ("import sys as s\ns.modules.clear()", True),
        ("from sys import modules\nmodules.popitem()", True),
        ("import sys as s\ns.modules['dep'] = s.modules['pytest'] = f", True),
    ],
)
def test_aliases_get_mains_full_sys_modules_rule_in_test_code(snippet, caught):
    assert ("sys_modules_write" in _rules(TEST, snippet)) is caught


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("importlib.import_module('_pytest.runner')", "pytest_internals"),
        ("x = _pytest.runner", "pytest_internals"),
        ("config.pluginmanager.unregister(p)", "pytest_internals"),
        ("sys.modules['os'] = x", "sys_modules_write"),
        ("sys.modules[name] = m", "sys_modules_write"),
        ("sys.modules.clear()", "sys_modules_write"),
    ],
)
def test_mains_test_code_detections_still_fire(line, rule):
    assert _rules(TEST, line) == [rule]


@BOTH
@pytest.mark.parametrize(
    "line",
    [
        "sys.pycache_prefix = os.path.expanduser('~/alt')",
        "import sys as s; s.pycache_prefix = tmp",
        "setattr(sys, 'pycache_prefix', tmp)",
        "where = sys.pycache_prefix",  # a read: an accepted false positive
    ],
)
def test_pycache_prefix_is_caught_in_any_file(path, line):
    assert _rules(path, line) == ["pycache_prefix"]


def test_pycache_prefix_in_a_conftest_and_not_in_a_comment():
    assert _rules("tests/conftest.py", "sys.pycache_prefix = tmp") == ["pycache_prefix"]
    assert _rules(LIBRARY, "    # sys.pycache_prefix = tmp is how #276 plants bytecode") == []


CALC = "src/calc.py"  # library code: the verifier's pytest process imports it too (#276)


@pytest.mark.parametrize("path", [TEST, LIBRARY, CALC])
@pytest.mark.parametrize(
    "snippet",
    [
        "pytest.TestReport.from_item_and_call = classmethod(lambda cls, item, call: make(item))",
        "pytest.TestReport.passed = property(lambda self: True)",
        "pytest.CallInfo.excinfo = None",
        "pytest.Item.runtest += hook",
        "pytest.TestReport.__init__.__defaults__ = ('passed',)",
        "setattr(pytest.TestReport, 'passed', True)",
        "object.__setattr__(pytest.Item, 'runtest', run)",
        "delattr(pytest.Function, 'runtest')",
        "del pytest.Session.shouldfail",
        "monkeypatch.setattr('pytest.TestReport.passed', True)",
        "make = pytest.TestReport.from_item_and_call",  # any mention of the report factory
        "import pytest as p\np.TestReport.passed = True",
        "import json, pytest as p; setattr(p.Config, 'option', opts)",
        "from pytest import TestReport as R; R.from_item_and_call = make",
        "from pytest import TestReport as R\nR.passed = property(lambda self: True)",
        "from pytest import (Item as I, Session)\nI.runtest = run",
        "from pytest import PytestPluginManager\nPytestPluginManager.hook = hooks",
    ],
)
def test_changes_to_pytest_public_classes_are_refused_in_any_file(path, snippet):
    assert set(_rules(path, snippet)) == {"pytest_public_patch"}


@pytest.mark.parametrize("path", [TEST, CALC])
@pytest.mark.parametrize(
    "snippet",
    [
        "pytest.TestReport.outcome: object = property(get_outcome)",  # annotated
        "pytest.TestReport.outcome, extra = property(get_outcome), 0",  # tuple
        "extra, pytest.TestReport.outcome = 0, property(get_outcome)",
        "a, (b, pytest.TestReport.outcome) = 1, (2, prop)",
        "(pytest.TestReport.outcome) = property(get_outcome)",  # parenthesised
        "[pytest.TestReport.outcome] = [property(get_outcome)]",  # list
        "*rest, pytest.Item.runtest = runs",
        "if ready: (pytest.Item.runtest) = run",
        "x = 1; [pytest.Item.runtest] = [run]",
        "(pytest.Item.runtest) += hook",
        "for pytest.Item.runtest in [run]: pass",
        "with patched() as pytest.Item.runtest: pass",
        "pytest . TestReport.passed = True",
        "pytest.ExceptionInfo.__bool__ = lambda self: False",
        "pytest.RaisesExc.matches = lambda self, exc: True",
        "pytest.RaisesGroup.matches = lambda self, exc: True",
        "pytest.SubtestReport.passed = True",
        "pytest.Metafunc.parametrize = keep_one",
        "del(pytest.Item.runtest)",
        "setattr(*(pytest.TestReport, 'passed', True))",
        "import pytest as p\n(p.TestReport.outcome) = prop",
        "import pytest as p\np . TestReport.passed = True",
        "from pytest import TestReport as R\nR.outcome: object = prop",
        "from pytest import TestReport as R\nR.outcome, extra = prop, 0",
        "from pytest import ExceptionInfo as E\nE.__bool__ = lambda self: False",
    ],
)
def test_more_target_forms_and_classes_are_refused(path, snippet):
    assert set(_rules(path, snippet)) == {"pytest_public_patch"}


def test_a_pytest_class_alias_bound_earlier_is_followed_in_library_code():
    lines = [(1, "from pytest import TestReport as R"), (2, "x = 1"), (30, "    R.passed = True")]
    assert [(f.line, f.rule) for f in scan_added_lines(CALC, lines)] == [
        (30, "pytest_public_patch")
    ]


@pytest.mark.parametrize("path", [TEST, CALC])
@pytest.mark.parametrize(
    "snippet",
    [
        "def f(r: pytest.TestReport) -> None:",
        "def g(r: pytest.TestReport = None):",
        "isinstance(x, pytest.Item)",
        "item: pytest.Item = items[0]",
        "if report.outcome == 'passed':",
        "report.outcome = 'passed'",  # a local report object, not the class
        "assert pytest.TestReport.when == 'call'",
        "ok = pytest.Item.a <= 3 or pytest.Item.b >= 4 or pytest.Item.c != 1",
        "child = pytest.Item.from_parent(parent, name='x')",
        "import pytest as p\nhint: p.TestReport = report",
        "from pytest import Item\nclass MyItem(Item):\n    marker = 'x'",
        "import pytest as p; obj.p.TestReport.x = 1",  # an attribute, not the alias
        "pytest.Items.x = 1",  # not one of the listed classes
        "setattr(pytest.Items, 'x', 1)",
        "from pytest import TestReport as R\nobj.R.passed = 1",  # an attribute, not the alias
        "run(pytest.Item.name, flag=True)",  # a keyword argument is not an assignment
        "obj.check(pytest.Item.name)[0] = 1",
        "registry[pytest.Item.name] = item",
        "pair, other = pytest.Item.name, 1",
        "names = [pytest.Item.name]; flags = (pytest.Item.name, 1)",
        "pytest.Item.name: str",  # an annotation with no value
        "for name in [pytest.Item.name]: pass",
        "with pytest.raises(ValueError) as excinfo: pass",
        "if excinfo.value == pytest.ExceptionInfo: pass",
    ],
)
def test_honest_uses_of_pytest_public_classes_pass(path, snippet):
    assert _rules(path, snippet) == []
