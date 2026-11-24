"""tools/blackbox_suggest (#370): strict parametrize ids.

With ``strict_parametrization_ids`` on, or ``strict`` which turns it on, pytest stops collecting
when two cases of one parametrize call or one fixture's ``params`` get the same id, and no test
runs. Under such a configuration a test file refuses unless every id in it is known to differ.
"""

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "def f(x):\n    return x * 2\n"
TEST = "import pytest\nfrom pkg.core import f\ndef test_a():\n    assert f(2) == 4\n"
INI = "[pytest]\nstrict = true\n"


def run(tmp_path, body, config=INI, conftest=""):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": TEST + body,
        "tests/conftest.py": conftest,
    }
    files["pyproject.toml" if config.startswith("[tool") else "pytest.ini"] = config
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


def case(values, extra=""):
    return f"@pytest.mark.parametrize('x', {values}{extra})\ndef test_b(x):\n    assert x == x\n"


TWO = "@pytest.mark.parametrize('x,y', [(1, 2), (1, 2)])\ndef test_b(x, y):\n    assert x < y\n"
REFUSED = {
    "same_values": (TWO, INI, ""),
    "same_values_key": (TWO, "[pytest]\nstrict_parametrization_ids = true\n", ""),
    "same_values_addopts": (TWO, "[pytest]\naddopts = --strict\n", ""),
    "same_values_native": (TWO, "[tool.pytest]\nstrict = true\n", ""),
    "number_and_string": (case("[1, '1']"), INI, ""),
    "bool_and_string": (case("[True, 'True']"), INI, ""),
    "negative": (case("[-1, -1]"), INI, ""),
    "joined": (
        "@pytest.mark.parametrize('x,y', [('a-b', 'c'), ('a', 'b-c')])\n"
        "def test_b(x, y):\n    assert x\n",
        INI,
        "",
    ),
    "same_ids": (case("[1, 2]", ", ids=['a', 'a']"), INI, ""),
    "id_and_value": (case("[1, 2]", ", ids=['2', None]"), INI, ""),
    "same_param_ids": (case("[pytest.param(1, id='a'), pytest.param(2, id='a')]"), INI, ""),
    "named_values": (case("[len, len]"), INI, ""),
    "aliased": (
        "p = pytest.mark.parametrize\n@p('x', [1, 1])\ndef test_b(x):\n    assert x\n",
        INI,
        "",
    ),
    "fixture_params": (
        "@pytest.fixture(params=[1, 1])\ndef thing(request):\n    return request.param\n"
        "def test_b(thing):\n    assert thing\n",
        INI,
        "",
    ),
    "conftest_fixture_params": (
        "def test_b(thing):\n    assert thing\n",
        INI,
        "import pytest\n@pytest.fixture(params=['a', 'a'])\n"
        "def thing(request):\n    return request.param\n",
    ),
    "one_name_list": (
        "@pytest.mark.parametrize(['x'], [1, 2])\ndef test_b(x):\n    assert x\n",
        INI,
        "",
    ),
    "id_hook": (
        case("[1, 2]"),
        INI,
        "def pytest_make_parametrize_id(config, val, argname):\n    return 'same'\n",
    ),
}


@pytest.mark.parametrize("body,config,conftest", REFUSED.values(), ids=REFUSED.keys())
def test_a_file_whose_ids_may_repeat_under_strict_ids_refuses(tmp_path, body, config, conftest):
    item = run(tmp_path, body, config, conftest)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "no_parametrize": ("", INI),
    "distinct_values": (case("[1, 2, '3', None, True, 1.5, -2, 'a\\u00e9']"), INI),
    "distinct_ids": (case("[1, 1]", ", ids=['a', 'b']"), INI),
    "distinct_param_ids": (case("[pytest.param(1, id='a'), pytest.param(1, id='b')]"), INI),
    "key_off": (TWO, "[pytest]\nstrict = true\nstrict_parametrization_ids = false\n"),
    "not_strict": (TWO, "[pytest]\nxfail_strict = true\n"),
    "tuple_names": (
        "@pytest.mark.parametrize(('x', 'y'), [(1, 2), (2, 1)])\ndef test_b(x, y):\n    assert x\n",
        INI,
    ),
    "stacked": (
        "@pytest.mark.parametrize('x', [1, 2])\n@pytest.mark.parametrize('y', [1, 2])\n"
        "def test_b(x, y):\n    assert x\n",
        INI,
    ),
}


@pytest.mark.parametrize("body,config", KEPT.values(), ids=KEPT.keys())
def test_a_file_whose_ids_differ_keeps_the_draft(tmp_path, body, config):
    item = run(tmp_path, body, config)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
