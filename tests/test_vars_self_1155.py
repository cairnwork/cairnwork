"""The vars(self) exception is limited to statically safe methods (#1155)."""

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected

SIGNAL = "import sys\nsys.path.insert(0, 'x')\n"

ALLOWED = [
    ("plain_method", "class C:\n    def m(self):\n        return vars(self)\n"),
    ("classmethod", "class C:\n    @classmethod\n    def m(cls):\n        return vars(cls)\n"),
    ("alongside_code", "x = 1\nclass C:\n    def m(self):\n        return vars(self)\ny = 2\n"),
    (
        "self_call_from_init",
        "class C:\n    def __init__(self):\n        self.m()\n"
        "    def m(self):\n        return vars(self)\n",
    ),
]

REFUSED = [
    ("module_function", "def f(self):\n    return vars(self)\n"),
    (
        "nested_function",
        "class C:\n    def m(self):\n        def inner():\n"
        "            return vars(self)\n        return inner()\n",
    ),
    ("lambda", "f = lambda self: vars(self)\n"),
    ("staticmethod", "class C:\n    @staticmethod\n    def m(self):\n        return vars(self)\n"),
    (
        "assignment",
        "class C:\n    def m(self):\n        self = object()\n        return vars(self)\n",
    ),
    (
        "walrus",
        "class C:\n    def m(self):\n        (self := object())\n        return vars(self)\n",
    ),
    (
        "for_target",
        "class C:\n    def m(self):\n        for self in ():\n            return vars(self)\n",
    ),
    (
        "with_target",
        "class C:\n    def m(self):\n        with open('x') as self:\n"
        "            return vars(self)\n",
    ),
    (
        "except_target",
        "class C:\n    def m(self):\n        try:\n            pass\n"
        "        except Exception as self:\n            return vars(self)\n",
    ),
    (
        "match_as",
        "class C:\n    def m(self, x):\n        match x:\n"
        "            case self:\n                return vars(self)\n",
    ),
    (
        "match_star",
        "class C:\n    def m(self, x):\n        match x:\n"
        "            case [*self]:\n                return vars(self)\n",
    ),
    (
        "match_mapping",
        "class C:\n    def m(self, x):\n        match x:\n"
        "            case {**self}:\n                return vars(self)\n",
    ),
    ("vars_assignment", "vars = print\nclass C:\n    def m(self):\n        return vars(self)\n"),
    (
        "vars_import",
        "from builtins import vars\nclass C:\n    def m(self):\n        return vars(self)\n",
    ),
    (
        "vars_def",
        "def vars(x):\n    return x\nclass C:\n    def m(self):\n        return vars(self)\n",
    ),
    ("two_args", "class C:\n    def m(self):\n        return vars(self, 1)\n"),
    ("other_name", "class C:\n    def m(self, obj):\n        return vars(obj)\n"),
    ("unbound", "class C:\n    def m(self):\n        return vars(self)\nC.m(module)\n"),
    (
        "attribute",
        "import builtins\nclass C:\n    def m(self):\n        return builtins.vars(self)\n",
    ),
    (
        "custom_decorator",
        "def dec(f):\n    return f(sys)\nclass C:\n    @dec\n"
        "    def m(self):\n        return vars(self)\n",
    ),
    ("property", "class C:\n    @property\n    def m(self):\n        return vars(self)\n"),
    (
        "wraps_call",
        "import functools\nx = 1\nclass C:\n    @functools.wraps(x)\n"
        "    def m(self):\n        return vars(self)\n",
    ),
    ("attribute_decorator", "class C:\n    @a.b\n    def m(self):\n        return vars(self)\n"),
    (
        "stacked_classmethod",
        "def dec(f):\n    return f\nclass C:\n    @classmethod\n"
        "    @dec\n    def m(cls):\n        return vars(cls)\n",
    ),
    (
        "lambda_decorator",
        "class C:\n    @(lambda f: f)\n    def m(self):\n        return vars(self)\n",
    ),
    ("receiver_default", "class C:\n    def m(self=sys):\n        return vars(self)\n"),
    (
        "receiver_default_unbound",
        "class C:\n    def m(self=sys):\n        return vars(self)\ntype(C()).m()\n",
    ),
    ("receiver_annotation", "class C:\n    def m(self: int):\n        return vars(self)\n"),
    (
        "type_obj_unbound",
        "class C:\n    def m(self):\n        return vars(self)\ntype(obj).m(sys)\n",
    ),
    ("class_alias", "class C:\n    def m(self):\n        return vars(self)\nD = C\nD.m(sys)\n"),
    (
        "parameter_method_reference",
        "class C:\n    def m(self):\n        return vars(self)\ndef g(k):\n    return k.m(sys)\n",
    ),
    (
        "cls_method_reference",
        "class C:\n    def m(self):\n        return vars(self)\n"
        "    @classmethod\n    def other(cls):\n        cls.m(sys)\n",
    ),
    (
        "type_self_method_reference",
        "class C:\n    def m(self):\n        return vars(self)\n"
        "    def other(self):\n        type(self).m(sys)\n",
    ),
    ("subclass", "class C:\n    def m(self):\n        return vars(self)\nclass D(C):\n    pass\n"),
    ("method_string", "class C:\n    def m(self):\n        return vars(self)\n'm'\n"),
    (
        "class_decorator",
        "def dec(cls):\n    return cls\n@dec\nclass C:\n"
        "    def m(self):\n        return vars(self)\n",
    ),
    (
        "class_string",
        "class C:\n    def m(self):\n        return vars(self)\n'C'\n",
    ),
    (
        "nested_class_attribute",
        "class Outer:\n    class Inner:\n        def m(self):\n            return vars(self)\n"
        "Outer.Inner.m(sys)\n",
    ),
    (
        "tuple_subscript_attribute",
        "class C:\n    def m(self):\n        return vars(self)\n(C,)[0].m(sys)\n",
    ),
    (
        "positional_only_receiver",
        "class C:\n    def m(self, /):\n        return vars(self)\n",
    ),
    (
        "aliased_vars_import",
        "from builtins import vars as v\nclass C:\n    def m(self):\n        return vars(self)\n",
    ),
    (
        "default_receiver_no_unbound_call",
        "class C:\n    def m(self=sys):\n        return vars(self)\n",
    ),
]


def _judge(repo, body):
    base = git(repo, "rev-parse", "HEAD")
    candidate = commit_change(repo, {"pkg/calc.py": SIGNAL + body})
    changed = tuple(git(repo, "diff", "--name-only", base, candidate).split())
    return candidate_sys_path.refuse_new_sys_path_mutation(repo, base, candidate, changed)


def _assert_refused(repo, body):
    with pytest.raises(CandidateRejected) as got:
        _judge(repo, body)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


@pytest.mark.parametrize(("case", "body"), ALLOWED, ids=[case for case, _ in ALLOWED])
def test_safe_vars_method_is_allowed(repo, case, body):
    _judge(repo, body)


@pytest.mark.parametrize(("case", "body"), REFUSED, ids=[case for case, _ in REFUSED])
def test_unsafe_vars_method_is_refused(repo, case, body):
    _assert_refused(repo, body)
