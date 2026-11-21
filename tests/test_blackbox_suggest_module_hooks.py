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
    "globals_update_in_a_function": {
        "src/pkg/core.py": CORE + "def _i():\n    globals().update(__getattr__=len)\n_i()\n"
    },
    "setattr_with_a_computed_name": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "setattr(sys.modules[__name__], '__get' + 'attr__', len)\n"
    },
    "exec_defines_getattr": {
        "src/pkg/core.py": CORE + "exec('def __getattr__(n):\\n    return 1')\n"
    },
    "exec_defines_dir": {
        "src/pkg/core.py": CORE + "exec(\"def __dir__():\\n    return ['f']\\n\")\n"
    },
    "module_class_swapped": {
        "src/pkg/core.py": "import sys, types\n"
        + CORE
        + "class _M(types.ModuleType):\n    def __getattr__(self, n):\n        return 1\n"
        + "sys.modules[__name__].__class__ = _M\n"
    },
    "module_replaced_in_sys_modules": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "class _O:\n    def __getattr__(self, n):\n        return 1\n"
        + "sys.modules[__name__] = _O()\n"
    },
    "vars_update_with_a_computed_name": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "vars(sys.modules[__name__]).update({'__di' + 'r__': len})\n"
    },
    "globals_subscript_with_a_computed_name": {
        "src/pkg/core.py": CORE + "globals()['__get' + 'attr__'] = len\n"
    },
    "attribute_store_in_a_function": {
        "src/pkg/core.py": CORE
        + "def _i():\n    import pkg.core\n    pkg.core.__getattr__ = len\n_i()\n"
    },
    "function_globals": {
        "src/pkg/core.py": CORE + "_g = f.__globals__\n_g['__get' + 'attr__'] = len\n"
    },
    "frame_globals": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "sys._getframe().f_globals['__get' + 'attr__'] = len\n"
    },
    "getattr_with_a_computed_name": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "_m = getattr(sys, 'modu' + 'les')\n_m[__name__].__getattr__ = len\n"
    },
    "builtins_exec": {
        "src/pkg/core.py": "import builtins as _b\n"
        + CORE
        + "_b.exec('def __dir__():\\n    return []')\n"
    },
    "builtins_looked_up_by_a_plain_name": {
        "src/pkg/core.py": "import builtins as _b\nimport pkg.core as _me\n"
        + CORE
        + "getattr(_b, 'setattr')(_me, '__get' + 'attr__', len)\n"
    },
    "methodcaller_with_a_computed_name": {
        "src/pkg/core.py": "import operator\nimport pkg.core as _me\n"
        + CORE
        + "operator.methodcaller('__set' + 'attr__', '__get' + 'attr__', len)(_me)\n"
    },
    "sys_modules_looked_up_by_a_plain_name": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "class _O:\n    def __getattr__(self, n):\n        return 1\n"
        + "getattr(sys, 'modules')[__name__] = _O()\n"
    },
    "sys_modules_looked_up_by_a_computed_name": {
        "src/pkg/core.py": "import sys\n"
        + CORE
        + "class _O:\n    def __getattr__(self, n):\n        return 1\n"
        + "getattr(sys, 'modu' + 'les')[__name__] = _O()\n"
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
                return name.upper()
            def __dir__(self):
                return []
        import re
        PATTERN = re.compile("x")
        def g(obj):
            __dir__ = 1
            return getattr(obj, "name", __dir__), obj.__class__.__name__
        """
    )
    item = run(tmp_path, {"src/pkg/core.py": core})
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


DYNAMIC = ["exec", "eval", "globals", "locals", "vars", "setattr", "delattr", "__import__"]
DYNAMIC += ["__builtins__", "import_module", "attrgetter", "methodcaller"]
REFLECTIVE = ["__dict__", "__globals__", "__self__", "__setattr__", "__getattribute__"]
REFLECTIVE += ["modules", "f_globals", "f_locals", "f_builtins"]


@pytest.mark.parametrize(
    "line", [f"_x = {name}\n" for name in DYNAMIC] + [f"_x = f.{name}\n" for name in REFLECTIVE]
)
def test_each_dynamic_or_reflective_name_alone_refuses(tmp_path, line):
    item = run(tmp_path, {"src/pkg/core.py": CORE + line})
    assert item.refusal is not None and not item.protected(KINDS_NOW)
