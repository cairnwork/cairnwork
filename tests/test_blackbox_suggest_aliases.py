"""tools/blackbox_suggest (#370): pytest names under an alias, and a package init's setup hooks.

Each refused case would run code around the test under real pytest that a blackbox case does not
repeat; each control (the same file without the alias) is refused the same way.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
HEAD = "from pkg.core import f\n"


def run(tmp_path, body, extra=None):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": textwrap.dedent(body),
        **(extra or {}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def refused(item):
    return item.refusal is not None and not item.protected(KINDS_NOW)


ALIASED = [
    "from pytest import fixture as mark\n@mark(autouse=True)\ndef env():\n    yield\n",
    "from pytest import fixture as fx\n@fx(autouse=True)\ndef env():\n    yield\n",
    "import pytest as pt\n@pt.fixture(autouse=True)\ndef env():\n    yield\n",
    "from pytest import importorskip as mark\nmod = mark('json')\n",
    "from pytest import importorskip as skip_unless\nmod = skip_unless('json')\n",
]
PLAIN_FORMS = [
    "import pytest\n@pytest.fixture(autouse=True)\ndef env():\n    yield\n",
    "import pytest\nmod = pytest.importorskip('json')\n",
]


@pytest.mark.parametrize("prelude", PLAIN_FORMS)
def test_unaliased_pytest_forms_are_refused(tmp_path, prelude):
    assert refused(run(tmp_path, HEAD + prelude + PLAIN))


@pytest.mark.parametrize("prelude", ALIASED)
def test_aliased_pytest_names_are_resolved_before_classification(tmp_path, prelude):
    assert refused(run(tmp_path, HEAD + prelude + PLAIN))


def test_aliased_pytest_names_in_a_conftest_are_resolved(tmp_path):
    conftest = "from pytest import fixture as mark\n@mark(autouse=True)\ndef env():\n    yield\n"
    assert refused(run(tmp_path, HEAD + PLAIN, {"tests/conftest.py": conftest}))


def test_a_harmless_alias_keeps_the_draft_like_the_plain_spelling(tmp_path):
    test = "def test_a(x):\n    assert f(x) == x\n"
    plain = "import pytest\n@pytest.mark.parametrize('x', [1, 2])\n" + test
    aliased = "from pytest import mark as m\n@m.parametrize('x', [1, 2])\n" + test
    a = analyze_file(
        _write(tmp_path / "p", HEAD + plain), tmp_path / "p", default_roots(tmp_path / "p")
    )
    b = analyze_file(
        _write(tmp_path / "q", HEAD + aliased), tmp_path / "q", default_roots(tmp_path / "q")
    )
    assert [(o.refusal, o.exact, len(o.cases)) for o in a] == [
        (o.refusal, o.exact, len(o.cases)) for o in b
    ]


def _write(root, body):
    for rel, text in {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": textwrap.dedent(body),
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root / "tests/test_x.py"


@pytest.mark.parametrize("hook", ["setup_module", "teardown_module", "setUpModule"])
def test_package_init_setup_hooks_are_obligations(tmp_path, hook):
    init = f"def {hook}(module=None):\n    pass\n"
    item = run(tmp_path, HEAD + PLAIN, {"tests/__init__.py": init})
    assert item.refusal and "package init" in item.refusal and refused(item)


def test_module_setup_hook_in_the_test_file_is_the_control(tmp_path):
    item = run(tmp_path, HEAD + "def setup_module():\n    pass\n" + PLAIN)
    assert refused(item)


def test_a_package_init_without_hooks_keeps_the_draft(tmp_path):
    item = run(tmp_path, HEAD + PLAIN, {"tests/__init__.py": "VALUE = 1\n"})
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


UNRESOLVED = {
    "conftest_fixture_with_local_rebinding": (
        HEAD + PLAIN,
        {
            "tests/conftest.py": "from pytest import fixture as mark\n"
            "@mark(autouse=True)\ndef env():\n    mark = None\n    yield\n"
        },
    ),
    "parameter_named_like_the_alias": (
        HEAD
        + "from pytest import fixture as mark\n@mark(autouse=True)\ndef env():\n    yield\n"
        + "def helper(mark=None):\n    return mark\n"
        + PLAIN,
        {},
    ),
    "function_local_assignment": (
        HEAD
        + "from pytest import importorskip as mark\nmod = mark('json')\n"
        + "def helper():\n    mark = 1\n    return mark\n"
        + PLAIN,
        {},
    ),
    "comprehension_variable": (
        HEAD
        + "from pytest import fixture as mark\n@mark(autouse=True)\ndef env():\n    yield\n"
        + "VALUES = [mark for mark in ()]\n"
        + PLAIN,
        {},
    ),
    "match_capture": (
        HEAD
        + "from pytest import mark as m\n@m.parametrize('x', [1])\n"
        + "def test_a(x):\n    assert f(x) == x\n"
        + "def helper(v):\n    match v:\n        case {'x': m}:\n            return m\n",
        {},
    ),
    "match_star": (
        HEAD
        + "from pytest import mark as m\n@m.parametrize('x', [1])\n"
        + "def test_a(x):\n    assert f(x) == x\n"
        + "def helper(v):\n    match v:\n        case [*m]:\n            return m\n",
        {},
    ),
    "match_mapping_rest": (
        HEAD
        + "from pytest import mark as m\n@m.parametrize('x', [1])\n"
        + "def test_a(x):\n    assert f(x) == x\n"
        + "def helper(v):\n    match v:\n        case {**m}:\n            return m\n",
        {},
    ),
}


@pytest.mark.parametrize("case", sorted(UNRESOLVED))
def test_an_alias_that_cannot_be_resolved_is_refused(tmp_path, case):
    body, extra = UNRESOLVED[case]
    assert refused(run(tmp_path, body, extra))


def test_a_single_clean_alias_still_resolves_to_the_plain_result(tmp_path):
    body = HEAD + "from pytest import mark as m\n@m.parametrize('x', [1])\n"
    body += "def test_a(x):\n    assert f(x) == x\n"
    item = run(tmp_path, body)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


FUNCTION_LOCAL = {
    "aliased_name": "def helper():\n    from pytest import skip as sk\n    sk('no')\n",
    "aliased_module": "def helper():\n    import pytest as pt\n    pt.skip('no')\n",
    "plain_name": "def helper():\n    from pytest import skip\n    skip('no')\n",
    "in_a_method": "class Helper:\n    def go(self):\n        import pytest\n"
    + "        pytest.skip('no')\n",
}


@pytest.mark.parametrize("case", sorted(FUNCTION_LOCAL))
def test_a_pytest_import_inside_a_function_is_refused(tmp_path, case):
    assert refused(run(tmp_path, HEAD + FUNCTION_LOCAL[case] + PLAIN))


def test_a_function_local_import_of_another_module_keeps_the_draft(tmp_path):
    item = run(tmp_path, HEAD + "def helper():\n    import json\n    return json\n" + PLAIN)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)
