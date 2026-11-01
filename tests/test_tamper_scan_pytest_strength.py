"""pytest_public_patch (tamper_scan_pytest.py): every listed class, target form and spelling.

The sample lines are assembled from parts (``P`` is the module name, ``obj`` a listed class
reached through it), so each case reads as the shape it checks. The class list is spelled out
here, not read from the module, so a class dropped from ``PYTEST_CLASSES`` fails a test.
"""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cairnwork.tamper_scan import scan_added_lines
from cairnwork.tamper_scan_pytest import canonical_pytest

LIBRARY = "src/calc.py"
TEST = "tests/test_calc.py"
RULE = "pytest_public_patch"
P = "py" + "test"
REPORT, ITEM = f"{P}.TestReport", f"{P}.Item"

LISTED = (
    "TestReport",
    "CollectReport",
    "SubtestReport",
    "CallInfo",
    "ExceptionInfo",
    "RaisesExc",
    "RaisesGroup",
    "Session",
    "Item",
    "Function",
    "Metafunc",
    "Config",
    "PytestPluginManager",
)


def _rules(snippet: str, path: str = LIBRARY) -> set[str]:
    lines = [(number, line) for number, line in enumerate(snippet.split("\n"), start=1)]
    return {finding.rule for finding in scan_added_lines(path, lines)}


@pytest.mark.parametrize("cls", LISTED)
def test_every_listed_class_is_refused_on_a_plain_assignment(cls):
    assert _rules(f"{P}.{cls}.attr = value") == {RULE}


@pytest.mark.parametrize("cls", LISTED)
def test_every_listed_class_is_refused_through_an_import_alias(cls):
    assert _rules(f"from {P} import {cls} as K\nK.attr = value") == {RULE}


AUGMENTED = ("//=", "**=", "<<=", ">>=", "%=", "|=", "^=", "&=", "/=", "*=", "-=", "@=")
REFUSED = [
    f"{REPORT}.__init__.__defaults__: tuple = ('passed',)",  # two levels down, annotated
    f"x = {ITEM}.runtest.__code__ = code",  # chained, not first
    f"a = {ITEM}.runtest = run",
    *[f"{ITEM}.runtest {op} f" for op in AUGMENTED],
    f"{ITEM}.runtest, *rest = runs",
    f"with cm() as ({ITEM}.runtest): pass",
    f"with a() as {ITEM}.runtest, b(): pass",
    f"with (a() as {ITEM}.runtest): pass",
    f"from {P} import TestReport as T, Item as Ti\nTi.runtest = run",  # one alias a prefix
]
PASSED = [
    f"my{ITEM}.name = 1",
    f"(handler)({ITEM}.name, flag=True)",
    f"[handler][0]({ITEM}.name, flag=True)",
    f"{ITEM}.name: a == b",
    f"{ITEM}.name: str; y = 1",
    f"{ITEM}.name: str  # y = 1",
    f"run(**{{{ITEM}.name: 1}}, flag=True)",
    f"msg = 'look for {ITEM}.name'",
    f"msg = 'therefor {ITEM}.name in x'",
    f"msg = 'known as {ITEM}.name today'",
    f"del cache[{ITEM}]",
    "my_from_item_and_call_helper()",
    f"patcher.patched({REPORT}, 'outcome')",
]


@pytest.mark.parametrize("snippet", REFUSED)
def test_more_target_forms_are_refused(snippet):
    assert RULE in _rules(snippet)


@pytest.mark.parametrize("snippet", PASSED)
def test_honest_lines_near_the_target_forms_pass(snippet):
    assert RULE not in _rules(snippet)


SPELLINGS = [  # Python needs no space around these keywords and brackets
    f"for({REPORT}.outcome)in[prop]:pass",
    f"_ = [0 for({ITEM}.runtest)in[run]]",
    f"_ = [0for {ITEM}.runtest in[run]]",
    f"with contextlib.nullcontext(prop)as({REPORT}.outcome):pass",
    f"with cm() as[{ITEM}.runtest]:pass",
    f"{REPORT}.outcome: 1 == 1 = prop",  # an annotation holding "=="
    f"({REPORT}.outcome): object = prop",  # a parenthesised annotated target
    f"unittest.mock.patch.object({REPORT}, 'outcome', prop, create=True).start()",
    f"unittest.mock.patch('{REPORT}.outcome', prop, create=True).start()",
    f"unittest.mock.patch.multiple({REPORT}, outcome=prop, create=True).start()",
    f"mocker.patch.object({ITEM}, 'runtest', run)",
    f"from {P} import(TestReport as R)\nR.outcome = prop",
]


@pytest.mark.filterwarnings("ignore::SyntaxWarning")
@pytest.mark.parametrize("snippet", SPELLINGS)
def test_compact_keyword_spellings_mock_patches_and_annotations_are_refused(snippet):
    compile(snippet.replace("cm()", "x"), "calc.py", "exec")  # valid Python
    assert _rules(snippet) == {RULE}


def test_a_modules_alias_imported_with_no_space_is_followed():
    snippet = "from sys import(modules as m)\nm['" + P + "'] = None"
    compile(snippet, "test_calc.py", "exec")
    assert "sys_modules_write" in _rules(snippet, TEST)


# --- properties --------------------------------------------------------------------------

_cls = st.sampled_from(LISTED)
_attr = st.sampled_from(["outcome", "passed", "runtest", "excinfo", "__init__", "when", "hook"])
_space = st.sampled_from(["", " ", "  ", "\t"])
_op = st.sampled_from(["=", "+=", *AUGMENTED])
WRITE_FORMS = [
    "{c} {op} v",
    "{c}: object = v",
    "{c}, other = v, 0",
    "other, {c} = 0, v",
    "({c}) = v",
    "[{c}] = [v]",
    "*rest, {c} = vs",
    "for {c} in [v]: pass",
    "with cm() as {c}: pass",
    "del {c}",
    "setattr({o}, '{a}', v)",
    "delattr({o}, '{a}')",
    "x = 1; {c} = v",
    "if ready: {c} = v",
]
READ_FORMS = [
    "x = {c}",
    "if {c} == v: pass",
    "ok = {c} <= 3",
    "run({c}, flag=True)",
    "registry[{c}] = item",
    "for name in [{c}]: pass",
    "def f(r: {o} = None): pass",
    "isinstance(x, {o})",
]
_SETTINGS = settings(
    max_examples=300, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow]
)


@_SETTINGS
@given(cls=_cls, attr=_attr, op=_op, form=st.sampled_from(WRITE_FORMS), sp=_space)
def test_property_every_write_form_on_every_class_is_refused(cls, attr, op, form, sp):
    obj = f"{P}{sp}.{sp}{cls}"
    line = form.format(c=f"{obj}.{attr}", o=obj, a=attr, op=op)
    compile(line.replace("cm()", "x"), "calc.py", "exec")
    assert _rules(line) == {RULE}, line


@_SETTINGS
@given(cls=_cls, attr=_attr, form=st.sampled_from(READ_FORMS))
def test_property_reads_and_checks_pass(cls, attr, form):
    obj = f"{P}.{cls}"
    assert _rules(form.format(c=f"{obj}.{attr}", o=obj)) == set()


@_SETTINGS
@given(
    cls=_cls,
    attr=_attr,
    form=st.sampled_from(WRITE_FORMS + READ_FORMS),
    alias=st.sampled_from(["p", "pt", "_p", "P2"]),
    how=st.sampled_from(["module", "class"]),
)
def test_property_an_alias_reads_like_the_plain_spelling(cls, attr, form, alias, how):
    plain = form.format(c=f"{P}.{cls}.{attr}", o=f"{P}.{cls}", a=attr, op="=")
    if how == "module":
        bind, obj = f"import {P} as {alias}", f"{alias}.{cls}"
    else:
        bind, obj = f"from {P} import {cls} as {alias}", alias
    aliased = form.format(c=f"{obj}.{attr}", o=obj, a=attr, op="=")
    assert _rules(f"{bind}\n{aliased}") == _rules(plain), (bind, aliased)


@_SETTINGS
@given(
    cls=_cls,
    alias=st.sampled_from(["p", "R", "q1"]),
    text=st.text(alphabet="pRq1._ =()[],:", max_size=30),
)
def test_property_canonical_pytest_is_idempotent(cls, alias, text):
    aliases = {P: {alias}, cls: {alias}}
    once = canonical_pytest(text, aliases)
    assert canonical_pytest(once, aliases) == once


@pytest.mark.parametrize("path", [LIBRARY, TEST])
@pytest.mark.parametrize(
    ("module", "alias", "call", "rule"),
    [("os", "o", "o._exit(0)", "os_exit"), ("gc", "g", "g.get_objects()", "gc_walk")],
)
def test_a_no_space_import_after_a_string_rebind_keeps_the_earlier_alias(
    path, module, alias, call, rule
):
    rebind = "from sys import modules as " + module
    no_space = "from sys import(modules as " + module + ")"
    snippet = "\n".join([f"import {module} as {alias}", '"""', rebind, '"""', no_space, call])
    compile(snippet, "calc.py", "exec")
    assert rule in _rules(snippet, path)
