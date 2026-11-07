"""#960: a default-skipped directory is exempt from the P2P targets only while the base
cannot change what pytest collects. Real pipeline runs (Seatbelt). Setup: tests/.cache/test_far.py
is the only base test of neg(); the wrong candidate breaks neg(). On main every override row
was VERIFIED."""

import pytest
from conftest import commit_change
from test_p2p import CONFTEST, FIX_ADD, FIX_BREAKS_NEG, SIBLING, run
from test_p2p_base_complete import ADD, project  # noqa: F401 - the fixture

pytestmark = pytest.mark.sandbox

FAR = {"tests/test_sibling.py": ADD, "tests/.cache/test_far.py": SIBLING}
IGNORE = (
    "import pytest\n\n\n@pytest.hookimpl(tryfirst=True)\n"
    "def pytest_ignore_collect(collection_path, config):\n"
    "    if collection_path.name == '.cache':\n        return False\n"
)
DIRECTORY = (
    "import pytest\n\n\ndef pytest_collect_directory(path, parent):\n"
    "    if path.name == 'tests':\n\n        class Tests(pytest.Dir):\n"
    "            def collect(self):\n                yield from super().collect()\n"
    "                yield pytest.Dir.from_parent(self, path=self.path / '.cache')\n\n"
    "        return Tests.from_parent(parent, path=path)\n"
)
UNRELATED = "def pytest_report_header(config):\n    return 'x'\n\n\ndef helper():\n    return 1\n"
REFUSED = {
    "1_addopts": {"pytest.ini": "[pytest]\naddopts = -o norecursedirs=\n"},
    "2a_ignore_collect": {"tests/conftest.py": CONFTEST + IGNORE},
    "2b_collect_directory": {"conftest.py": DIRECTORY},
    "plugins": {"conftest.py": "pytest_plugins = []\n"},
    "unparsable": {"conftest.py": "def (:\n"},
    "nested_plugins": {"conftest.py": "import sys\nif sys:\n    pytest_plugins = ['x']\n"},
}


@pytest.mark.parametrize("name", REFUSED)
def test_a_base_that_can_collect_a_skipped_directory_is_not_exempt(project, tmp_path, name):  # noqa: F811
    result = run(project, tmp_path, {"src/calc.py": FIX_BREAKS_NEG}, mode="whole-suite",
                 extra={**FAR, **REFUSED[name]})  # fmt: skip
    assert result.status == "FAILED" and not result.verified, result.reason
    assert result.reason.startswith("UNCOMPARABLE: P2P_BASE_UNTARGETABLE: "), result.reason


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"tests/conftest.py": CONFTEST + UNRELATED},
        {"pytest.toml": "[pytest]\nnorecursedirs = [{foo = 'bar'}]\n"},  # pytest 9.1.1 skips .cache
    ],
    ids=["no_override", "unrelated_hook", "toml_non_scalar"],
)
def test_a_base_that_cannot_collect_it_keeps_the_exemption(project, tmp_path, extra):  # noqa: F811
    commit_change(project, {**FAR, **extra})
    result = run(project, tmp_path, {"src/calc.py": FIX_ADD}, mode="whole-suite")
    assert result.status == "COMPLETED", result.reason
