"""tools/blackbox_suggest (#370): project modules that answer missing names themselves.

A module-level ``__getattr__`` runs when an import or pytest's collection looks up a name the
module does not define (``from pkg import core`` asks the package first; collection probes every
object in the test module's namespace, modules included). ``__dir__`` changes what a listing of
the module sees. The reader keeps a test only when no project module that the test file, its
conftests or its drafted targets import binds either name at module level.
"""

import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots

CORE = "SCALE = 1\ndef f(x):\n    return x * SCALE\n"
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
HOOK = """
def __getattr__(name):
    import pkg.core
    pkg.core.SCALE = 10
    raise AttributeError(name)
"""


def run(tmp_path, extra, head=HEAD):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "tests/test_x.py": head + PLAIN,
        **{rel: textwrap.dedent(text) for rel, text in extra.items()},
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::test_a"))


REFUSED = {
    "package_getattr_asked_by_from_import": {"src/pkg/__init__.py": HOOK},
    "target_module_getattr": {"src/pkg/core.py": CORE + HOOK},
    "target_module_dir": {"src/pkg/core.py": CORE + "def __dir__():\n    return ['f']\n"},
    "getattr_bound_by_assignment": {
        "src/pkg/core.py": CORE + "def _lazy(name):\n    return 1\n__getattr__ = _lazy\n"
    },
    "getattr_bound_by_import": {
        "src/pkg/core.py": CORE + "from pkg._lazy import __getattr__\n",
        "src/pkg/_lazy.py": HOOK,
    },
    "getattr_bound_by_import_as": {
        "src/pkg/core.py": CORE + "from pkg._lazy import hook as __getattr__\n",
        "src/pkg/_lazy.py": "def hook(name):\n    return 1\n",
    },
    "getattr_under_if": {"src/pkg/core.py": CORE + "if True:\n" + textwrap.indent(HOOK, "    ")},
    "getattr_set_through_globals": {
        "src/pkg/core.py": CORE
        + "def _lazy(name):\n    return 1\nglobals()['__getattr__'] = _lazy\n"
    },
    "getattr_set_through_globals_in_a_function": {
        "src/pkg/core.py": CORE
        + "def _install():\n    globals()['__getattr__'] = len\n_install()\n"
    },
    "getattr_set_through_global_statement": {
        "src/pkg/core.py": CORE
        + "def _install():\n    global __getattr__\n    __getattr__ = len\n_install()\n"
    },
    "getattr_in_a_module_the_target_imports": {
        "src/pkg/core.py": "from pkg import helper\n" + CORE,
        "src/pkg/helper.py": HOOK,
    },
}


@pytest.mark.parametrize("extra", REFUSED.values(), ids=REFUSED.keys())
def test_project_modules_with_a_module_getattr_or_dir_refuse(tmp_path, extra):
    item = run(tmp_path, extra)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


def test_getattr_in_a_class_body_keeps_the_draft(tmp_path):
    core = CORE + textwrap.dedent(
        """
        class Proxy:
            def __getattr__(self, name):
                return getattr(object(), name)
            def __dir__(self):
                return []
        def g(obj, name):
            __dir__ = 1
            return getattr(obj, name, __dir__)
        """
    )
    item = run(tmp_path, {"src/pkg/core.py": core})
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)
