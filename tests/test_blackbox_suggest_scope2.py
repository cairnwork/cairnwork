"""tools/blackbox_suggest (#370): import-time gaps left after #369.

Each refused module below runs project code when pytest imports or collects it (each was checked
with real pytest on the same files for #370: ``core.SCALE`` changes, so ``test_a`` fails under
pytest although the blackbox case would pass). Each control must keep its draft. The mutation
cases pin checks that could be dropped without any other test failing.
"""

import ast
import textwrap

import pytest

from tools.blackbox_suggest import KINDS_NOW, analyze_file, default_roots
from tools.blackbox_suggest.obligations import is_autouse

CORE = """
SCALE = 1
def f(x):
    return x * SCALE
class Registered:
    def __init_subclass__(cls, **kw):
        global SCALE
        SCALE = 10
"""
HEAD = "import pytest\nfrom pkg import core\nfrom pkg.core import f, Registered\n"
PLAIN = "def test_a():\n    assert f(2) == 2\n"
BOX = """
class Box:
    def __class_getitem__(cls, item):
        core.SCALE = 10
        return cls
"""
SET = "def _s(*args):\n    core.SCALE = 10\n"
TC = "from typing import TYPE_CHECKING\n"
EXTRA = "if TYPE_CHECKING:\n    import pkg.extra\n"
ENUM_INIT = "    def __init__(self, *a):\n        core.SCALE = 10\n    RED = 1\n"
POISON = "def poison():\n    core.SCALE = 10\n"
GETATTR = "def __getattr__(name):\n    core.SCALE = 10\n    raise AttributeError(name)\n"


def run(tmp_path, body, conftest=None, target="test_a"):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": CORE,
        "src/pkg/extra.py": "import pkg.core\npkg.core.SCALE = 10\n",
        "tests/test_x.py": HEAD + body + PLAIN,
    }
    if conftest is not None:
        files["tests/conftest.py"] = HEAD + conftest
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    found = analyze_file(tmp_path / "tests/test_x.py", tmp_path, default_roots(tmp_path))
    return next(o for o in found if o.test_id.endswith("::" + target))


def case(id, *parts, conftest=None, target="test_a"):
    body = "".join(textwrap.dedent(p) if p.startswith("\n") else p for p in parts)
    return pytest.param(body, conftest, target, id=id)


REFUSED = [
    # G1: a value that is not a literal in a decorator argument, a default or an annotation
    case("default_attribute", "def helper(x=core.LAZY):\n    return x\n"),
    case("kwonly_default_attribute", "def helper(*, x=core.LAZY):\n    return x\n"),
    case("param_annotation_attribute", "def helper(x: core.LAZY):\n    return x\n"),
    case("return_annotation_attribute", "def helper() -> core.LAZY:\n    return 1\n"),
    case("mark_argument_attribute", """
        @pytest.mark.skipif(core.LAZY, reason="r")
        def helper():
            pass
    """),
    case("parametrize_values_attribute", """
        @pytest.mark.parametrize("v", [core.LAZY])
        def test_b(v):
            pass
    """),
    case("fixture_params_attribute", """
        @pytest.fixture(params=[core.LAZY])
        def unused(request):
            return request.param
    """),
    case("default_compare", "def helper(x=core.N == 1):\n    return x\n"),
    case("default_subscript_class_getitem", BOX, "def helper(x=Box[0]):\n    return x\n"),
    case("annotation_subscript_class_getitem", BOX, "def helper(x: Box[int]):\n    return x\n"),
    case("method_default_attribute", """
        class Helper:
            def m(self, x=core.LAZY):
                return x
    """),
    case("annotation_project_name_in_union", "def helper(x: Registered | None):\n    pass\n"),
    case("annotation_rebound_generic", """
        from pkg.core import Registered as list
        def helper(x: list[int]):
            return x
    """),
    case("annotation_rebound_typing", """
        import enum as typing
        def helper(x: typing.Optional[int]):
            return x
    """),
    case("decorator_kwargs_unpacked", """
        @pytest.mark.skipif(**{"condition": False, "reason": "r"})
        def helper():
            pass
    """),
    # G2: a walrus in a decorator, an annotation or a default
    case("walrus_in_decorator_rebinds_testcase", """
        from unittest import TestCase
        @pytest.mark.skipif((TestCase := Registered) is None, reason="r")
        def helper():
            pass
        class TestU(TestCase):
            def test_u(self):
                pass
    """),
    case("walrus_rebinds_same_file_base", """
        class Base:
            pass
        @pytest.mark.skipif((Base := Registered) is None, reason="r")
        def helper():
            pass
        class Mine(Base):
            pass
    """),
    case("walrus_in_return_annotation_enables_type_checking",
         TC, "def helper() -> (TYPE_CHECKING := True):\n    pass\n", EXTRA),
    case("walrus_in_default_enables_type_checking",
         TC, "def helper(x=(TYPE_CHECKING := True)):\n    pass\n", EXTRA),
    case("walrus_in_decorator_enables_type_checking", TC, """
        @pytest.mark.skipif(not (TYPE_CHECKING := True), reason="r")
        def helper():
            pass
    """, EXTRA),
    # G3: a same-file base whose __init_subclass__ runs when it is subclassed
    case("same_file_base_init_subclass", """
        class Base:
            def __init_subclass__(cls, **kw):
                core.SCALE = 10
        class Mine(Base):
            pass
    """),
    case("same_file_base_init_subclass_imports_project", """
        class Base:
            def __init_subclass__(cls, **kw):
                import pkg.extra
        class TestMine(Base):
            def test_m(self):
                pass
    """),
    case("init_subclass_two_level_chain", """
        class A:
            def __init_subclass__(cls, **kw):
                core.SCALE = 10
        class B(A):
            pass
        class C(B):
            pass
    """),
    case("init_subclass_bound_by_assignment", SET, """
        class Base:
            __init_subclass__ = _s
        class Mine(Base):
            pass
    """),
    # G4: a hook bound by assignment or import instead of def
    case("setup_module_bound_by_assignment", SET, "setup_module = _s\n"),
    case("setUpModule_bound_by_assignment", SET, "setUpModule = _s\n"),
    case("setup_function_bound_by_assignment", SET, "setup_function = _s\n"),
    case("pytest_hook_bound_by_assignment_in_test_module", SET, "pytest_generate_tests = _s\n"),
    case("conftest_hook_bound_by_assignment", conftest=SET + "pytest_runtest_setup = _s\n"),
    case("conftest_hook_in_chained_assignment", conftest=SET + "a = pytest_runtest_setup = _s\n"),
    case("class_setup_method_bound_by_assignment", SET, """
        class TestK:
            setup_method = _s
            def test_k(self):
                assert f(2) == 2
    """, target="TestK::test_k"),
    case("setup_module_bound_by_project_import", "from pkg.core import f as setup_module\n"),
    case("class_level_generate_tests", """
        class TestK:
            def pytest_generate_tests(self, metafunc):
                core.SCALE = 10
            def test_k(self):
                assert f(2) == 2
    """, target="TestK::test_k"),
    # G5: module-level __getattr__ / __dir__, which pytest's collection calls
    case("module_getattr_in_test_file", GETATTR),
    case("module_getattr_in_conftest", conftest=GETATTR),
    case("module_dir_in_conftest", conftest="def __dir__():\n    core.SCALE = 10\n    return []\n"),
    case("module_getattr_by_assignment", SET, "__getattr__ = _s\n"),
    # G6: an import or assignment that reuses TestCase or a same-file class name
    case("import_aliased_as_testcase",
         "from enum import Enum as TestCase\nclass Color(TestCase):\n", ENUM_INIT),
    case("import_shadowing_same_file_class", """
        class Base:
            pass
        from enum import Enum as Base
        class Color(Base):
    """, ENUM_INIT),
    case("assignment_shadowing_same_file_class", """
        from enum import Enum
        class Base:
            pass
        Base = Enum
        class Color(Base):
    """, ENUM_INIT),
    case("import_rebinding_object",
         "from enum import Enum as object\nclass Color(object):\n", ENUM_INIT),
    case("import_rebinding_unittest",
         "import enum as unittest\nclass Color(unittest.TestCase):\n", ENUM_INIT),
    case("nested_class_name_is_not_a_module_base", """
        from enum import Enum as Inner
        class Helper:
            class Inner:
                pass
        class Color(Inner):
    """, ENUM_INIT),
    # N1: autouse given by anything but a literal True or False
    case("autouse_by_name", "AUTO = True\n@pytest.fixture(autouse=AUTO)\n", POISON),
    case("autouse_truthy_constant", "@pytest.fixture(autouse=1)\n", POISON),
    case("fixture_kwargs_unpack_of_a_name",
         'OPTS = {"autouse": False}\n@pytest.fixture(**OPTS)\n', POISON),
    case("fixture_kwargs_unpack", '@pytest.fixture(**{"autouse": True})\n', POISON),
    case("conftest_autouse_by_name",
         conftest="AUTO = True\n@pytest.fixture(autouse=AUTO)\n" + POISON),
    # Mutation pins: each fails if the named check is dropped from scope.py
    case("dict_unpack_of_class", """
        class M:
            def keys():
                core.SCALE = 10
                return []
        X = {**M}
    """),
    case("class_shadowing_type_checking", TC, "class TYPE_CHECKING:\n    pass\n", EXTRA),
    case("class_shadowing_typing_module", """
        import typing
        class typing:
            TYPE_CHECKING = True
        if typing.TYPE_CHECKING:
            import pkg.extra
    """),
]  # fmt: skip


@pytest.mark.parametrize(("body", "conftest", "target"), REFUSED)
def test_project_code_at_import_or_collection_refuses(tmp_path, body, conftest, target):
    item = run(tmp_path, body, conftest, target)
    assert item.refusal is not None and not item.protected(KINDS_NOW)


CONTROLS = [
    case("plain_signature_and_literal_marks", """
        def helper(x: int = 0, *, y: "core.T | None" = None, z: list[int] | None = None) -> bool:
            return bool(x)
        @pytest.mark.skipif(False, reason="r")
        def test_b():
            pass
        @pytest.mark.parametrize("v", [1, -2, "s"])
        def test_c(v):
            pass
    """),
    case("typing_and_pytest_generics_in_annotations", """
        import typing
        from collections.abc import Mapping
        from typing import Optional
        def helper(x: Optional[int] = None, y: dict[str, tuple[int, ...]] | None = None):
            return x
        def other(m: Mapping[str, int], c: pytest.CaptureFixture[str]) -> typing.Any:
            return m
    """),
    case("type_checking_guard_with_plain_mark", TC, """
        @pytest.mark.skipif(False, reason="r")
        def helper() -> None:
            pass
    """, EXTRA),
    case("plain_same_file_base", """
        class Base:
            def helper(self):
                return 1
        class TestMine(Base):
            def test_m(self):
                assert self.helper() == 1
    """),
    case("non_hook_alias_assignment", "def _helper(x):\n    return x\nhelper = _helper\n"),
    case("instance_getattr", """
        class Proxy:
            def __getattr__(self, name):
                return 1
    """),
    case("real_unittest_testcase_and_local_base", """
        from unittest import TestCase
        import os
        class Base:
            pass
        class TestU(TestCase):
            def test_u(self):
                pass
        class Mine(Base):
            pass
    """),
    case("unittest_submodule_import", """
        import unittest.mock
        class TestU(unittest.TestCase):
            def test_u(self):
                pass
    """),
    case("literal_dict_and_plain_class", """
        D = {"a": 1, "b": [2, None]}
        class Helper:
            pass
    """),
    case("plain_and_autouse_false_fixtures", """
        @pytest.fixture
        def unused():
            return 1
        @pytest.fixture(autouse=False)
        def unused2():
            return 2
    """),
]  # fmt: skip


@pytest.mark.parametrize(("body", "conftest", "target"), CONTROLS)
def test_inert_module_keeps_the_draft(tmp_path, body, conftest, target):
    item = run(tmp_path, body, conftest, target)
    assert item.protected(KINDS_NOW) and item.exact, item.reason(KINDS_NOW)


@pytest.mark.parametrize(
    ("decorator", "autouse"),
    [
        ("@pytest.fixture", False),
        ("@pytest.fixture(autouse=False)", False),
        ("@pytest.fixture(autouse=True)", True),
        ("@pytest.fixture(autouse=AUTO)", True),
        ("@pytest.fixture(**OPTS)", True),
        ('@pytest.fixture(scope="module", **{"autouse": False})', True),
    ],
)
def test_autouse_unless_absent_or_literal_false(decorator, autouse):
    func = ast.parse(f"{decorator}\ndef fx():\n    pass\n").body[0]
    assert is_autouse(func) is autouse  # type: ignore[arg-type]
