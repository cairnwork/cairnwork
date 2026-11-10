"""Issue #1147: ordinary dynamic attribute access is not import machinery."""

import pytest
from conftest import commit_change, git

from cairnwork import candidate_sys_path
from cairnwork.workspace import CandidateRejected


def _changed(repo, base, candidate):
    return tuple(git(repo, "diff", "--name-only", base, candidate).split())


SAFE_ADDITIONS = {
    "getattr": '    def timeout(self):\n        return getattr(self.opts, "timeout", 10)\n',
    "setattr": '    def activate(self):\n        setattr(self, "active", True)\n',
    "vars": "    def values(self):\n        return vars(self)\n",
}


@pytest.mark.parametrize("method", SAFE_ADDITIONS.values(), ids=SAFE_ADDITIONS)
def test_safe_dynamic_access_in_added_method_is_allowed(repo, method):
    base = commit_change(repo, {"pkg/calc.py": "class Worker:\n    pass\n"})
    candidate = commit_change(repo, {"pkg/calc.py": "class Worker:\n" + method})
    candidate_sys_path.refuse_new_sys_path_mutation(
        repo, base, candidate, _changed(repo, base, candidate)
    )


UNSAFE_ADDITIONS = {
    "sys target": (
        'import sys\nclass Worker:\n    def run(self):\n        return getattr(sys, "modules")\n'
    ),
    "importlib target": (
        "import importlib\nclass Worker:\n"
        "    def run(self, name, x):\n        setattr(importlib, name, x)\n"
    ),
    "sys alias": (
        'import sys as s\nclass Worker:\n    def run(self):\n        return getattr(s, "path")\n'
    ),
    "nonliteral module name": (
        "import sys\nobj = sys\nclass Worker:\n"
        "    def run(self, name):\n        return getattr(obj, name)\n"
    ),
    "from importlib alias": (
        "from importlib import util as module\nclass Worker:\n"
        '    def run(self):\n        return getattr(module, "find_spec")\n'
    ),
}


@pytest.mark.parametrize("source", UNSAFE_ADDITIONS.values(), ids=UNSAFE_ADDITIONS)
def test_dynamic_access_to_module_or_dynamic_name_is_refused(repo, source):
    base = commit_change(repo, {"pkg/calc.py": "VALUE = 1\n"})
    candidate = commit_change(repo, {"pkg/calc.py": source})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, candidate, _changed(repo, base, candidate)
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
