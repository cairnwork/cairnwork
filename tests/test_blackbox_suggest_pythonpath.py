"""tools/blackbox_suggest (#370): ``pythonpath`` entries read the way pytest reads them.

pytest takes each item of a TOML list as one path and splits a string value like a shell, so an
entry may hold spaces. The reader lists every such directory for standard-library names, and
still lists the parts a plain split on whitespace gives.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nimport fractions\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
SIDE = "from pkg import core\ncore.SCALE = 10\n"

CONFIGS = {
    "toml_list_item": ("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["src", "{}"]\n'),
    "toml_string": ("pyproject.toml", "[tool.pytest.ini_options]\npythonpath = 'src \"{}\"'\n"),
    "ini_double_quoted": ("pytest.ini", '[pytest]\npythonpath = src "{}"\n'),
    "ini_single_quoted": ("pytest.ini", "[pytest]\npythonpath = src '{}'\n"),
}


def run(tmp_path, outside, config, module):
    (outside / "shared libs").mkdir()
    (outside / "shared libs" / module).write_text(SIDE)
    name, text = CONFIGS[config]
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
        name: text.format(outside / "shared libs"),
    }
    for rel, body in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(textwrap.dedent(body))
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


@pytest.mark.parametrize("config", CONFIGS)
def test_a_stdlib_name_on_an_entry_with_a_space_refuses(tmp_path, tmp_path_factory, config):
    item = run(tmp_path, tmp_path_factory.mktemp("lib"), config, "fractions.py")
    assert item.refusal is not None and not item.protected(KINDS_NOW)


@pytest.mark.parametrize("config", CONFIGS)
def test_other_names_on_an_entry_with_a_space_keep_the_draft(tmp_path, tmp_path_factory, config):
    item = run(tmp_path, tmp_path_factory.mktemp("lib"), config, "helper.py")
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


def test_a_part_of_an_entry_split_on_whitespace_still_refuses(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("lib")
    (outside / "helpers").mkdir()
    (outside / "helpers" / "fractions.py").write_text(SIDE)
    entry = outside / "helpers other"
    for rel, body in {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
        "pyproject.toml": f'[tool.pytest.ini_options]\npythonpath = ["{entry}"]\n',
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    item = next(o for o in found if o.test_id.endswith("::test_a"))
    assert item.refusal is not None and not item.protected(KINDS_NOW)
