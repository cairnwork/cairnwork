"""tools/blackbox_suggest (#370): project modules the import closure cannot read.

The reader checks every project module that the drafted targets import for code that changes
what a name lookup sees. A module it cannot follow or read (an import under a statement, a
module with no single readable source, a package that moves where its submodules come from)
refuses instead of being left out of that check.
"""

import importlib.machinery
import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots, resolve

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
HELPER = """
import sys
def hook(name):
    raise AttributeError(name)
sys.modules["pkg.core"].__getattr__ = hook
"""


def run(tmp_path, extra):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
        **{rel: textwrap.dedent(text) for rel, text in extra.items()},
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "import_under_if": {
        "src/pkg/core.py": CORE + "if True:\n    import pkg.helper\n",
        "src/pkg/helper.py": HELPER,
    },
    "import_under_try": {
        "src/pkg/core.py": CORE + "try:\n    import pkg.helper\nexcept ImportError:\n    pass\n",
        "src/pkg/helper.py": HELPER,
    },
    "from_import_under_if": {
        "src/pkg/core.py": CORE + "if True:\n    from . import helper\n",
        "src/pkg/helper.py": HELPER,
    },
    "sourceless_compiled_module": {
        "src/pkg/core.py": CORE + "import pkg.helper\n",
        "src/pkg/helper.pyc": "not read",
    },
    "package_beside_module": {
        "src/pkg/core/__init__.py": "SCALE = 10\ndef f(x):\n    return x * SCALE\n",
    },
    "path_rebound_in_package": {
        "src/pkg/__init__.py": (
            "import os\n__path__ = [os.path.join(os.path.dirname(__file__), 'alt')]\n"
        ),
        "src/pkg/alt/core.py": "SCALE = 10\ndef f(x):\n    return x * SCALE\n",
    },
    "path_extended_in_package": {
        "src/pkg/__init__.py": "__path__.insert(0, 'alt')\n",
    },
    "path_set_on_the_package": {
        "src/pkg/core.py": CORE + "import pkg\npkg.__path__ = []\n",
    },
    "path_reached_by_name": {
        "src/pkg/core.py": CORE + "import pkg\ngetattr(pkg, '__path__').append('alt')\n",
    },
    "search_locations_changed": {
        "src/pkg/core.py": CORE + "import pkg\npkg.__spec__.submodule_search_locations.clear()\n",
    },
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_a_closure_module_the_reader_cannot_read_refuses(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


KEPT = {
    "stdlib_import_inside_a_function": {
        "src/pkg/core.py": CORE + "def g():\n    import json\n    return json\n",
    },
    "plain_subpackage": {
        "src/pkg/core.py": CORE + "from pkg.sub import mod\n",
        "src/pkg/sub/__init__.py": "",
        "src/pkg/sub/mod.py": "VALUE = 1\n",
    },
}


@pytest.mark.parametrize("extra", KEPT.values(), ids=KEPT.keys())
def test_a_closure_the_reader_reads_keeps_the_draft(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


@pytest.mark.parametrize("suffix", [".pyc", importlib.machinery.EXTENSION_SUFFIXES[0]])
def test_a_module_in_an_earlier_root_the_reader_cannot_read_refuses(tmp_path, suffix):
    files = {
        "src/pkg/__init__.py": "",
        f"src/pkg/core{suffix}": "not read",
        "pkg/__init__.py": "",
        "pkg/core.py": CORE,
        "tests/test_x.py": HEAD + PLAIN,
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    item = next(o for o in found if o.test_id.endswith("::test_a"))
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_a_closure_past_the_limit_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve, "CLOSURE_LIMIT", 2)
    item = run(tmp_path, {"src/pkg/core.py": CORE + "import pkg.helper\n", "src/pkg/helper.py": ""})
    assert item.refusal is not None and not item.protected(KINDS_NOW)
