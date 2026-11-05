"""tamper_scan: names built from string pieces and handed to a dynamic lookup (STRING-BUILT-v2).

Hits and non-hits of the observer in tamper_scan_strings.py, through scan_added_lines. Only the
smallest shape of each form is written here. The "residual" tests lock what is NOT caught today.
"""

import itertools

import pytest

from cairnwork.tamper_scan import scan_added_lines
from cairnwork.tamper_scan_fold import CAP
from cairnwork.tamper_scan_strings import MAX_CONSTS, MAX_DEPTH, StringScan
from cairnwork.tamper_scan_tokens import file_views

PATH = "pkg/module.py"
HOOK, INTERNALS, FORGE = "pytest_hook_impl", "pytest_internals", "code_object_forge"


def _scan(source: str, added: list[int] | None = None) -> list[tuple[int, str]]:
    """What the observer alone reports for ``source`` (``added``: the lines the diff adds)."""
    scan = StringScan()
    if file_views(source.encode(), scan) is None:
        return []
    return scan.findings(set(added if added is not None else range(1, source.count("\n") + 2)))


def _rules(source: str, **kw) -> set[str]:
    return {rule for _, rule in _scan(source, **kw)}


def test_the_findings_reach_scan_added_lines_once_and_only_for_added_lines():
    source = 'x = getattr(m, "pytest_" + "configure")\ny = 1\n'
    got = scan_added_lines(PATH, [(1, source.split("\n")[0])], source.encode())
    assert [(f.line, f.rule) for f in got] == [(1, HOOK)]
    assert scan_added_lines(PATH, [(2, "y = 1")], source.encode()) == []


ACCESSORS = [
    "getattr(m, {})", "setattr(m, {}, f)", "delattr(m, {})", "hasattr(m, {})",
    "globals()[{}] = f", "locals()[{}]", "vars(m)[{}] = f", "m.__dict__[{}] = f",
    "sys.modules[{}] = f", "sys.modules.get({})", "m.__dict__.get({})",
]  # fmt: skip
FORMS = [
    '"pytest_" + "configure"',
    '"pytest_" "configure"',
    '"" + "pytest_" + "" + "configure"',
    '"".join(["pytest_", "configure"])',
    '"_".join(("pytest", "configure"))',
    '"pytest_%s" % "configure"',
    '"pytest_%s%s" % ("con", "figure")',
    '"pytest_{}".format("configure")',
    '"{1}_{0}".format("configure", "pytest")',
    'b"pytest_configure".decode()',
    '"pytest_configure"',
]


@pytest.mark.parametrize(("accessor", "form"), list(itertools.product(ACCESSORS, FORMS)))
def test_each_accessor_catches_each_supported_way_to_build_a_hook_name(accessor, form):
    assert _scan(accessor.format(form)) == [(1, HOOK)]


@pytest.mark.parametrize(
    ("call", "rule"),
    [
        ('getattr(c, "plugin" + "manager")', INTERNALS),
        ('getattr(c, "_inner_" "hookexec")', INTERNALS),
        ('importlib.import_module("_py" + "test.config")', INTERNALS),
        ('__import__("plug" + "gy")', INTERNALS),
        ('sys.modules.get("_py" + "test")', INTERNALS),
        ('getattr(fn, "__co" + "de__")', FORGE),
        ('getattr(types, "Function" + "Type")', FORGE),
        ('getattr(builtins, "com" + "pile")', FORGE),
    ],
)
def test_internals_and_code_forgery_names_are_caught_by_their_own_rule(call, rule):
    assert _scan(call) == [(1, rule)]


def test_the_name_in_the_second_argument_of_getattr_is_found_in_a_wrapped_call():
    source = 'x = getattr(\n    module,\n    "pytest_"\n    + "configure",\n    None,\n)\n'
    assert _scan(source) == [(1, HOOK)]


def test_a_name_kept_in_a_variable_is_followed_through_the_constant_table():
    source = 'n = "plugin"\nm = "manager"\nx = getattr(c, n + m)\n'
    assert _scan(source, [3]) == [(3, INTERNALS)]
    assert _rules(source.replace('m = "manager"', 'm = "other"'), added=[3]) == set()
    assert _rules(source.replace('n = "plugin"', 'n = "plugin"; n = "x"'), added=[3]) == set()


def test_an_ordinary_constant_does_not_enter_the_table():
    scan = StringScan()
    file_views(b'a = "hello"\nb = 3\nn = "plugin"\n', scan)
    assert scan.consts == {"n": "plugin"}


def test_a_full_table_keeps_its_entries_marks_itself_and_refuses_nothing():
    names = [f"n{i}" for i in range(MAX_CONSTS + 1)]
    source = (
        "".join(f'{name} = "plugin"\n' for name in names)
        + f'x = getattr(c, {names[0]} + "manager")\n'
    )
    scan = StringScan()
    assert file_views(source.encode(), scan) is not None
    assert len(scan.consts) == MAX_CONSTS
    assert scan.consts_full
    last = len(names) + 1
    assert _scan(source, [last]) == [(last, INTERNALS)]  # an entry kept before the table filled
    assert _rules(source.replace(f"{names[0]} +", f"{names[-1]} +"), added=[last]) == set()


@pytest.mark.parametrize(
    "call",
    [
        'getattr(log, "info")("pytest_configure ran")',
        'getattr(m, "x" + "pytest_configure_ran")',
        'getattr(m, "pytest_", "configure")',
        'getattr(o, "get_" + name)',
        'getattr(o, "pytest_" + name)',
        'getattr(o, "pytest_%d" % 1)',
        'getattr(o, "%d_configure" % "pytest")',
        'getattr(o, "{:s}_configure".format("pytest"))',
        'getattr(o, "{}_{}".format("pytest"))',
        'getattr(o, "pytest_{0.x}".format("configure"))',
        'getattr(o, "{}{}".format("pytest_configure"))',
        'getattr(o, "%s_%s" % ("pytest_configure",))',
        'getattr(m, f"pytest_{x}")',
        'getattr(m, f"pytest_configure")',
        'getattr(m, b"pytest_configure")',
        'getattr(m, ("pytest_configure"))[0]',
        'foo("pytest_" + "configure")',
        'print("pytest_" + "configure")',
        'getattr(m, "pytest_" + "configure" + x)',
        'getattr(m, "".join(parts))',
        'getattr(m, sep.join(["pytest_", "configure"]))',
        'getattr(m, "pytest_configur" "e".upper())',
        'x = "pytest_" + "configure"',
        'modules["pytest_" "configure_two"]',
    ],
)
def test_a_name_that_is_not_exactly_a_refused_one_is_not_caught(call):
    assert _scan(call) == []


def test_only_lines_the_diff_added_are_reported():
    source = 'x = getattr(\n    m,\n    "pytest_"\n    + "configure",\n)\n'
    assert _scan(source, added=[5]) == [(5, HOOK)]  # the logical line holds an added row
    assert _scan(source, added=[]) == []  # an unchanged call is not this change's


def test_a_committed_call_is_read_whole_even_when_only_a_far_row_is_added():
    source = 'x = getattr(m, "pytest_" + "configure")\ny = 1\n'
    assert _scan(source, added=[2]) == []
    assert _scan(source, added=[1]) == [(1, HOOK)]


def test_a_file_without_a_token_stream_yields_nothing_and_raises_nothing():
    source = 'getattr(m, "pytest_" + "configure")\nx = "unclosed\n'
    scan = StringScan()
    assert file_views(source.encode(), scan) is None
    assert scan.steps == 0


@pytest.mark.parametrize("depth", [3, MAX_DEPTH - 3])
def test_nesting_up_to_the_depth_limit_still_collects(depth):
    nest = "(" * depth + ")" * depth
    assert _scan(f'getattr(m, f{nest}, "pytest_" + "configure")') == [(1, HOOK)]


def test_nesting_past_the_depth_limit_stops_collecting_and_refuses_nothing():
    nest = "(" * (MAX_DEPTH + 8) + ")" * (MAX_DEPTH + 8)
    assert _scan(f'getattr(m, f{nest}, "pytest_" + "configure")') == []


def test_a_fold_never_holds_more_than_the_cap():
    scan = StringScan()
    pieces = " + ".join(['"ab"'] * 5000)
    assert file_views(f"getattr(m, {pieces})\n".encode(), scan) is not None
    assert scan.peak <= CAP + 2


def test_a_name_made_of_too_many_pieces_is_unknown_not_a_hit():
    pieces = " + ".join(['"pytest_"'] * 2 + ['"configure"'] * 40)
    assert _scan(f"getattr(m, {pieces})") == []


@pytest.mark.parametrize(
    "call",
    [
        'getattr(m, "".join(chr(c) for c in codes))',
        "getattr(m, bytes([112, 121]).decode())",
        'getattr(m, "etagifnoc_tset"[::-1])',
        'getattr(m, name_from_table["a"])',
        'g = getattr; g(m, "pytest_" + "configure")',
        'object.__getattribute__(m, "pytest_" + "configure")',
        'operator.attrgetter("pytest_" + "configure")(m)',
        'exec("getattr(m, pytest_configure)")',
    ],
)
def test_residual_forms_are_not_caught_today(call):
    """Residual by design (DESIGN of STRING-BUILT-v2): locked as 'not caught', not as 'blocked'."""
    assert _scan(call) == []


def test_residual_name_built_across_lines_with_augmented_assignment():
    source = 'n = "pytest_"\nn += "configure"\nx = getattr(m, n)\n'
    assert _scan(source) == []


def test_an_alias_of_an_accessor_is_residual():
    assert _scan('f = getattr\nx = f(m, "pytest_" + "configure")\n') == []
