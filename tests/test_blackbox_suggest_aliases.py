"""tools/blackbox_suggest (#370): pytest names under another name, pytest imports inside
functions, and a package init's setup hooks.

The readers know pytest's names by their spelling (``fixture``, ``mark.parametrize``,
``pytest.importorskip``), so a module that imports one of them under another name is refused
as a whole rather than resolved: an alias read as something else could hide code that runs
around the test under real pytest. The plain spellings are read as before.
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
    return next(o for o in found if o.test_id.split("[")[0].endswith("::test_a"))


def refused(item):
    return item.refusal is not None and not item.protected(KINDS_NOW)


ALIASED = {
    "fixture_as_mark": "from pytest import fixture as mark\n@mark(autouse=True)\n"
    + "def env():\n    yield\n",
    "fixture_as_fx": "from pytest import fixture as fx\n@fx(autouse=True)\ndef env():\n    yield\n",
    "module_as_pt": "import pytest as pt\n@pt.fixture(autouse=True)\ndef env():\n    yield\n",
    "importorskip_as_mark": "from pytest import importorskip as mark\nmod = mark('json')\n",
    "unused_alias": "from pytest import skip as sk\n",
    "mark_as_m": "from pytest import mark as m\n",
    "private_module": "import _pytest.mark as pm\n",
    "private_from": "from _pytest.fixtures import fixture as fx\n",
    "alongside_another_import": "import json, pytest as pt\n",
    "match_capture": "from pytest import fixture as fx\n"
    + "def helper(v):\n    match v:\n        case {'x': fx}:\n            return fx\n",
    "global_declaration": "import pytest as pt\ndef helper():\n    global pt\n    pt.skip('no')\n",
    "real_name_bound_too": "from pytest import fixture as fx\nfixture = None\n",
}


@pytest.mark.parametrize("case", sorted(ALIASED))
def test_a_module_importing_a_pytest_name_under_another_name_is_refused(tmp_path, case):
    item = run(tmp_path, HEAD + ALIASED[case] + PLAIN)
    reason = item.refusal or ""
    assert refused(item) and ("another name" in reason or "binds a name" in reason), reason


def test_an_alias_in_a_conftest_refuses_the_tests_it_covers(tmp_path):
    conftest = "from pytest import fixture as mark\n@mark(autouse=True)\ndef env():\n    yield\n"
    assert refused(run(tmp_path, HEAD + PLAIN, {"tests/conftest.py": conftest}))


PLAIN_SPELLINGS = {
    "import_pytest": "import pytest\n@pytest.mark.parametrize('x', [1, 2])\n",
    "from_pytest_import_mark": "from pytest import mark\n@mark.parametrize('x', [1, 2])\n",
    "same_name_alias": "from pytest import mark as mark\n@mark.parametrize('x', [1, 2])\n",
    "fixture_not_used": "from pytest import fixture\n@fixture\ndef val():\n    return 1\n"
    + "import pytest\n@pytest.mark.parametrize('x', [1, 2])\n",
    "other_module_alias": "import json as js\nimport pytest\n@pytest.mark.parametrize('x', [1])\n",
}


@pytest.mark.parametrize("case", sorted(PLAIN_SPELLINGS))
def test_plain_pytest_spellings_keep_the_draft(tmp_path, case):
    item = run(tmp_path, HEAD + PLAIN_SPELLINGS[case] + "def test_a(x):\n    assert f(x) == x\n")
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


@pytest.mark.parametrize(
    "prelude",
    [
        "import pytest\n@pytest.fixture(autouse=True)\ndef env():\n    yield\n",
        "import pytest\nmod = pytest.importorskip('json')\n",
    ],
)
def test_plain_spellings_that_run_code_are_still_refused(tmp_path, prelude):
    assert refused(run(tmp_path, HEAD + prelude + PLAIN))


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


def test_a_mark_named_fixture_is_not_read_as_an_autouse_fixture(tmp_path):
    helper = "import pytest\n@pytest.mark.fixture(autouse=True)\ndef env():\n    pass\n"
    item = run(tmp_path, HEAD + helper + PLAIN)
    assert item.protected(KINDS_NOW), item.reason(KINDS_NOW)


def test_the_plain_fixture_spellings_are_still_read_as_fixtures(tmp_path):
    for i, deco in enumerate(["pytest.fixture(autouse=True)", "fixture(autouse=True)"]):
        head = HEAD + "import pytest\nfrom pytest import fixture\n"
        item = run(tmp_path / str(i), head + f"@{deco}\ndef env():\n    yield\n" + PLAIN)
        assert item.refusal and "autouse fixture env" in item.refusal
