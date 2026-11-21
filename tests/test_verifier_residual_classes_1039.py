"""#1039 residual classes: static scanning is a hurdle, not the verdict boundary.

Each candidate goes through the real macOS Seatbelt verifier and must not reach VERIFIED.
The static scan is only one of the layers that may refuse it; these tests pin the verdict,
not which layer refused. They name only the residual classes and do not expand the spelling catalog.
"""

import pytest
from verify_helpers import base_of, candidate, verify

pytestmark = pytest.mark.sandbox

_IMPORT_REPORT = (
    "import importlib\n"
    "_m = importlib.import_module('py' + 'test.reports')\n"
    "_r = getattr(_m, 'Test' + 'Report')\n"
    "_old = _r.from_item_and_call.__func__\n"
    "def _replacement(cls, item, call):\n"
    "    report = _old(cls, item, call)\n"
    "    report.outcome = 'passed'\n"
    "    report.longrepr = None\n"
    "    return report\n"
    "_r.from_item_and_call = classmethod(_replacement)\n"
)
_PYCACHE = "import sys\nsetattr(sys, 'pycache_' + 'prefix', __import__('os').environ['HOME'])\n"
_CLASS_WRITES = (
    "import importlib\n"
    "_r = importlib.import_module('py' + 'test.reports').TestReport\n"
    "_attrs = {'from_item_and_call': classmethod(lambda cls, item, call: None)}\n"
    "vars(_r).update(_attrs)\n"
)


def _verify_candidate(state, project, source):
    base = base_of(project)
    code = source + "\ndef add(a, b):\n    return a + b + 1\n"
    sha = candidate(project, base, {"src/calc.py": code})
    return verify(state, project, base, sha)[1]


@pytest.mark.parametrize(
    "source",
    [_IMPORT_REPORT, _PYCACHE, _CLASS_WRITES],
    ids=["A-report-module", "B-pycache-setting", "C-pytest-class-state"],
)
def test_a_residual_class_is_not_verified(state, project, source):
    out = _verify_candidate(state, project, source)
    assert out["status"] != "VERIFIED" and not out.get("verified"), out


def test_an_honest_small_fix_still_verifies(state, project):
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    code, out = verify(state, project, base, sha)
    assert (code, out["status"], out["reason"]) == (0, "VERIFIED", "CORE_VERIFIED")
