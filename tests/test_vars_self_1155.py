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
