"""tamper_scan: alias spellings, the most direct forgeries in library files, pycache_prefix.

Out of scope: dynamic forms such as importlib.import_module('_' + 'pytest.runner') or
getattr(os, '_' + 'exit'), a rebinding by assignment (m = sys.modules) and an alias bound only
in an unchanged line of the file. A scan of added lines cannot see these; other layers cover
them.
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
