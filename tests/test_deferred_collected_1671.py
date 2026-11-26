"""#1671: a test that behaves differently only when pytest says it is collect-only is still seen.
The collected-set run of #974 does not announce itself: same argv and same hand-over as a real run;
the parent ends it when it reads the guard's collection record. Each wrong shape sits beside a
visible new test (the false-green case); the controls are the same moves written plainly. That
the child cannot tell the two runs apart is pinned by test_collect_indistinguishable_1671. Real
runs under the sandbox, worker path."""

import sys

import pytest
from conftest import commit_change
from test_deferred_collected_974 import PATH, _run

from cairnwork.collect_reader import read_collected
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext

sandbox = pytest.mark.sandbox
OK_TWO = "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
BAD_IMPORT = "import no_such_module_1671\n\ndef test_never():\n    pass\n"
MARK = "marker_9f3c2e71"  # a body string that must not come back in any result field
BODY = f"    def test_h(self):\n        assert '{MARK}'\n"
WRONG = {  # categories: arguments read at import, a skip that fires only then
    "class-attribute-from-argv": "import sys\nimport unittest\nTC = unittest.TestCase\n"
    "class Hid(TC):\n    __test__ = '--collect-only' not in sys.argv\n" + BODY,
    "subscript-base-from-argv": "import sys\nimport unittest\nbs = [unittest.TestCase]\n"
    "class Hid(bs[0]):\n    __test__ = '--collect-only' not in sys.argv\n" + BODY,
    "module-skip-from-argv": "import sys\nimport pytest\nimport unittest\nTC = unittest.TestCase\n"
    "if '--collect-only' in sys.argv:\n    pytest.skip('x', allow_module_level=True)\n"
    "class Hid(TC):\n" + BODY,
}
SCOPE = (  # review of #1698: a dynamic fixture scope is called with the config at registration
    "import pytest\nSEEN = []\n"
    "def _scope(*, fixture_name, config):\n"
    "    g = getattr(config, 'plugin' + 'manager').get_plugin('cairnwork_report_guard')\n"
    "    flags = [k for k, v in vars(g).items() if v is True]\n"
    "    SEEN.append(bool(getattr(g, '_quiet', False) or flags))\n"
    "    return 'function'\n"
    "@pytest.fixture(scope=_scope)\ndef f():\n    pass\n"
    "class Lazy:\n    def __iter__(self):\n"
    "        return iter([1] if SEEN and SEEN[0] else [1, 2])\n"
    "@pytest.mark.parametrize('v', Lazy())\ndef test_a(v):\n    assert '" + MARK + "'\n"
)
RIGHT = {
    "plugin-state-read-by-dynamic-scope": SCOPE,
    "plain-class": "class TestHid:\n" + BODY,
    "argv-read-not-used": "import sys\nclass TestHid:\n    n = len(sys.argv)\n" + BODY,
}


@sandbox
@pytest.mark.parametrize("shape", WRONG)
def test_a_test_that_hides_only_under_collect_only_is_refused(project, tmp_path, shape):
    result = _run(project, tmp_path, WRONG[shape])
    assert result.status == "FAILED", result.reason
    assert result.reason.startswith("DEFERRED_TESTS_UNREADABLE"), result.reason
    assert MARK not in repr(result) and "Hid" not in result.reason


@sandbox
@pytest.mark.parametrize("shape", RIGHT)
def test_the_same_moves_written_plainly_complete(project, tmp_path, shape):
    result = _run(project, tmp_path, RIGHT[shape])
    assert result.status == "COMPLETED", result.reason


def test_a_quiet_collect_spec_has_the_real_run_argv():
    quiet = VerifierSpec("q", (PATH,), collect_only=True, announce=False)
    loud = VerifierSpec("l", (PATH,), collect_only=True)
    assert quiet.flags() == ()
    assert "--collect-only" in loud.flags()


@sandbox
def test_a_quiet_collect_run_reads_the_same_as_an_announced_one(repo, tmp_path):
    files = {"tests/test_ok.py": OK_TWO, "tests/test_bad.py": BAD_IMPORT}
    head = commit_change(repo, files)
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    paths = tuple(sorted(files))
    got = {}
    for announce in (False, True):
        spec = VerifierSpec("c", paths, collect_only=True, announce=announce)
        (run,) = run_verifiers(ctx, head, (spec,), f"r{announce}")
        got[announce] = (run, read_collected(run))
    quiet_run, quiet = got[False]
    assert not quiet_run.outcomes and quiet_run.observation.passed is not True
    assert "tests/test_bad.py" in quiet.error_files and not quiet.unusable
    assert {"tests/test_ok.py::test_one", "tests/test_ok.py::test_two"} <= set(quiet.ids)
    loud = got[True][1]
    assert (quiet.ids, quiet.error_files) == (loud.ids, loud.error_files)
