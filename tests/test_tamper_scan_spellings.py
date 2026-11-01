"""tamper_scan: more spellings of the same change to pytest's classes, hooks and internals.

Each sample is valid Python (checked with ``compile``, never run) and is a spelling of a change
the rules already refuse in its plainest form: a keyword, bracketed or prefixed-string target of a
patch call, an attribute chain in brackets or through a subscript, a name bound to two things, an
item assignment under a class attribute, and hook implementations in a test helper file. Sample
text is assembled from parts (``P`` is the module name), as in test_tamper_scan_pytest_strength.py.
"""

import re
import time

import pytest

from cairnwork.tamper_scan import _canonical, scan_added_lines
from cairnwork.tamper_scan_pytest import PYTEST_HOOK_IMPL, PYTEST_PUBLIC_PATCH, canonical_pytest

P = "py" + "test"
LIBRARY = "src/calc.py"
TEST = "tests/test_calc.py"
HELPER = "tests/helper_plugin.py"
RULE = "pytest_public_patch"
MOCK = "from unittest.mock import patch\n"
ITEM = f"{P}.Item"


def _rules(source: str, path: str = LIBRARY) -> set[str]:
    lines = list(enumerate(source.split("\n"), start=1))
    return {finding.rule for finding in scan_added_lines(path, lines, source.encode())}


KEYWORD_AND_STRING_TARGETS = [
    MOCK + f"patch.object(target={ITEM}, attribute='runtest', new=f).start()",
    MOCK + f"patch.object(attribute='runtest', new=f, target={ITEM}).start()",
    MOCK + f"patch(target='{ITEM}.runtest', new=f).start()",
    MOCK + f"patch.object(({ITEM}), 'runtest', f).start()",
    MOCK + f"patch(r'{ITEM}.runtest', f).start()",
    MOCK + f"patch(R'''{ITEM}.runtest''', f).start()",
    MOCK + f"patch.multiple(target={ITEM}, runtest=f).start()",
    f"monkeypatch.setattr(r'{ITEM}.runtest', f)",
    f"monkeypatch.setattr(target={ITEM}, name='runtest', value=f)",
    f"monkeypatch.delattr(target={ITEM}, name='runtest')",
]


@pytest.mark.parametrize("path", [LIBRARY, TEST])
@pytest.mark.parametrize("source", KEYWORD_AND_STRING_TARGETS)
def test_a_keyword_bracketed_or_prefixed_patch_target_is_found(source, path):
    compile(source, "calc.py", "exec")
    assert RULE in _rules(source, path)


@pytest.mark.parametrize(
    "source",
    [
        MOCK + "patch('_" + P + ".python.Function.runtest', f).start()",
        MOCK + "patch(target=r'_" + P + ".reports.TestReport.passed', new=True).start()",
        MOCK + "patch.multiple('_" + P + ".runner', call_and_report=f).start()",
        "monkeypatch.setattr('_" + P + ".python.Function.runtest', f)",
    ],
)
def test_a_patch_target_naming_pytest_internals_is_found_in_a_library(source):
    """The patch imports the named module, as an ``import`` statement would."""
    compile(source, "calc.py", "exec")
    assert "pytest_internals" in _rules(source, LIBRARY)


BRACKETED_CHAINS = [
    f"({ITEM}).runtest = f",
    f"({P}).Item.runtest = f",
    f"((({P}).Item)).runtest = f",
    f"( {ITEM} ).runtest += f",
    f"del ({ITEM}).runtest",
    f"setattr(({P}).Item, 'runtest', f)",
    f"{P}.Function.__mro__[0].runtest = f",
    f"for ({ITEM}).runtest in [f]: pass",
    f"from {P} import Item as I\n(I).runtest = f",
    f"import {P} as p\n(p).Item.runtest = f",
]


@pytest.mark.parametrize("source", BRACKETED_CHAINS)
def test_a_chain_in_brackets_or_through_a_subscript_is_found(source):
    compile(source, "calc.py", "exec")
    assert RULE in _rules(source)


ITEM_ASSIGNMENTS = [
    f"{P}.Function.runtest.__globals__['x'] = f",
    f"{P}.Config.__init__.__kwdefaults__['k'] = 1",
    f"{P}.Config.__init__.__kwdefaults__['k'] += 1",
    f"{P}.TestReport.__init__.__defaults__ = (1,)",
    f"{P}.Function.runtest.__globals__['x'], y = f, 0",
]


@pytest.mark.parametrize("source", ITEM_ASSIGNMENTS)
def test_an_item_assignment_under_a_class_attribute_is_found_like_its_del(source):
    compile(source, "calc.py", "exec")
    assert RULE in _rules(source)
    assert RULE in _rules(f"del {P}.Function.runtest.__globals__['x']")


@pytest.mark.parametrize("path", [LIBRARY, TEST])
@pytest.mark.parametrize(
    ("source", "rule"),
    [
        (f"from sys import modules as p\nimport {P} as p\np.Item.runtest = f", RULE),
        (f"if 0: from sys import modules as p\nimport {P} as p\np.Item.runtest = f", RULE),
        (f"from sys import modules as I\nfrom {P} import Item as I\nI.runtest = f", RULE),
        (f"from {P} import Item as m\nfrom sys import modules as m\nm['{P}'] = None", None),
    ],
)
def test_a_name_bound_to_two_things_is_read_as_each(source, rule, path):
    compile(source, "calc.py", "exec")
    assert (rule or "sys_modules_write") in _rules(source, path)


HOOKS = [
    "def " + P + "_pyfunc_call(pyfuncitem):\n    return True",
    "def " + P + "_make_collect_report(collector):\n    pass",
    "async def " + P + "_fixture_setup(fixturedef, request):\n    pass",
    P + "_runtest_makereport = replacement",
    "x = 1; " + P + "_runtest_call: object = replacement",
    "from helpers import replacement as " + P + "_runtest_makereport",
    "from helpers import " + P + "_runtest_makereport",
    f"import {P}\n@{P}.hookimpl(specname='{P}_runtest_makereport')\ndef g(item, call):\n  pass",
    f"from {P} import hookimpl as h",
    f"from {P} import (fixture, hookimpl)",
    "import pluggy\nmark = pluggy.HookimplMarker('" + P + "')",
    'from pluggy import HookimplMarker\nmark = HookimplMarker(project_name="' + P + '")',
]


@pytest.mark.parametrize("source", HOOKS)
def test_a_hook_bound_in_a_test_helper_file_is_found(source):
    compile(source, "helper_plugin.py", "exec")
    assert "pytest_hook_impl" in _rules(source, HELPER)
    assert "pytest_hook_impl" not in _rules(source, "tests/conftest.py")


OWN_PLUGIN_MARKERS = [  # a project's own pluggy plugins: its marker and hooks are not pytest's
    "import pluggy\nhookimpl = pluggy.HookimplMarker('myproj')",
    "from pluggy import HookimplMarker\nmyproj_hookimpl = HookimplMarker(project_name='myproj')",
    "@myproj_hookimpl\ndef myproj_startup(app):\n    pass",
    "import myproj\n@myproj.hookimpl\ndef register_commands(cli):\n    pass",
    "from myproj import hookimpl\n@hookimpl(tryfirst=True)\ndef startup(app):\n    pass",
    'def test_startup():\n    """The plugin marks startup with hookimpl."""',
    "x = 1  # registered through hookimpl",
]


@pytest.mark.parametrize("source", OWN_PLUGIN_MARKERS)
def test_a_projects_own_plugin_marker_in_a_test_file_passes(source):
    compile(source, "test_calc.py", "exec")
    assert _rules(source, TEST) == set()


# Main's reading of alias spellings (call brackets kept as written) and what it refuses there.
_MODULES = "from sys import modules as m\n"
_SYS = "import sys as s\n"
MAIN_READINGS = [
    (_MODULES + "m.pop (name)", "sys_modules_write"),
    (_MODULES + "m.update (fakes)", "sys_modules_write"),
    (_MODULES + "m.setdefault (name)", "sys_modules_write"),
    (_MODULES + "m.pop(name)", "sys_modules_write"),
    (_SYS + "s.modules.pop (name)", "sys_modules_write"),
    (_SYS + "s.modules.update (fakes)", "sys_modules_write"),
    ("import gc as g\ng.get_objects (x)", "gc_walk"),
]


@pytest.mark.parametrize(("source", "rule"), MAIN_READINGS)
def test_an_alias_spelling_read_with_its_call_brackets_is_still_found(source, rule):
    """Removing brackets only adds a reading; the one main makes is kept beside it."""
    compile(source, "test_calc.py", "exec")
    assert rule in _rules(source, TEST)


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("m.pop (name)", "m.pop (name)"),
        ("run (x).y = 1", "run (x).y = 1"),
        ("f(a) (b)", "f(a) (b)"),
        ("cache[k] (v)", "cache[k] (v)"),
        ("del (x).y", "del x.y"),
        ("for (x).y in z: pass", "for x.y in z: pass"),
        ("a = [(x).y, ( z )]", "a = [x.y, z]"),
    ],
)
def test_only_brackets_of_their_own_are_removed(text, read):
    """Brackets after a name or a closing bracket belong to a call or subscript and stay."""
    assert canonical_pytest(text, {}) == read


def test_the_plain_reading_is_kept_beside_the_unwrapped_one():
    aliases: dict[str, set[str]] = {k: set() for k in ("sys", "os", "gc", "modules", "_exit")}
    aliases["modules"] = {"m"}
    readings = _canonical("x = (m)", aliases)
    assert "x = (sys.modules)" in readings
    assert "x = sys.modules" in readings


HONEST = [
    MOCK + f"patch.object(target=obj, attribute='x', new={ITEM}.runtest).start()",
    MOCK + f"patch.object(obj, 'x', {ITEM}).start()",
    MOCK + "patch('os.getcwd', new=lambda: '/').start()",
    f"monkeypatch.setattr(obj, 'kind', {ITEM})",
    f"print(({ITEM}).name)",
    f"x = ({ITEM}).name",
    f"registry[({ITEM})] = 1",
    f"x = {P}.Function.__mro__[0].runtest",
    f"cache[{ITEM}.name] = 1",
    f"{P}.Function.from_parent(parent, name='x')",
    f"{P}.Function.__mro__[0].from_parent(parent, name='x')",
    f"{P}.Config.getoption(config, 'x', default=None)",
    f"({P}.Function).from_parent(parent, name='x')",
    f"ok = {P}.Config.__init__.__kwdefaults__['k'] == 1",
    "from sys import modules as m\nx = m.get('json')",
    "importlib.import_module('_" + P + ".python')",
]


@pytest.mark.parametrize("path", [LIBRARY, TEST])
@pytest.mark.parametrize("source", HONEST)
def test_nearby_honest_lines_still_pass(source, path):
    compile(source, "calc.py", "exec")
    assert _rules(source, path) - {"pytest_internals"} == set()
    if path == LIBRARY:
        assert _rules(source, path) == set()


@pytest.mark.parametrize(
    "source",
    [
        "def " + P + "_generate_tests(metafunc):\n    pass",
        P + "_plugins = ['helpers.fixtures']",
        "import " + P + "_asyncio",
        "from " + P + "_mock import MockerFixture",
        "def test_" + P + "_pyfunc_call_name():\n    pass",
    ],
)
def test_honest_names_in_a_test_file_are_not_hooks(source):
    compile(source, "test_calc.py", "exec")
    assert _rules(source, TEST) == set()


LONG_LINES = [  # about 100 KB each, straight through the pattern or function
    ("(" * 50000 + ITEM + ")" * 50000 + ".runtest = f", lambda line: canonical_pytest(line, {})),
    ((P + "_runtest_a: ") * 8000, re.compile(PYTEST_HOOK_IMPL).search),
    ("from x import a " * 8000, re.compile(PYTEST_HOOK_IMPL).search),
    ("patch(a, " * 12000, re.compile(PYTEST_PUBLIC_PATCH).search),
    (f"{ITEM}.a[0], " * 7000, re.compile(PYTEST_PUBLIC_PATCH).search),
]


@pytest.mark.parametrize(
    ("line", "scan"), LONG_LINES, ids=["brackets", "hook", "from", "call", "tuple"]
)
def test_new_forms_are_read_in_bounded_time_on_long_lines(line, scan):
    start = time.perf_counter()
    scan(line)
    assert time.perf_counter() - start < 2.0
