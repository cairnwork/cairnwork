"""Permanent macOS sandbox regression for #821: a pytest plugin module the candidate can change.

Real Seatbelt, real verify path. ``tests/conftest.py`` (protected) lists a library file in
``pytest_plugins``. (a) the two published forgeries, in their smallest form (a hook that flips a
failed report, a hook that swallows the call's exception), are refused before pytest runs;
(b) a real fix still verifies; (c) a project that only *uses* a benign plugin (hooks and fixtures
in an unchanged file, loaded through ``pytest_plugins``) is not blocked; (d) a candidate that
*defines* a hook in a library file is refused even when benign: a known trade-off, locked here.
Each case prints ``RESULT <name> <exit code> <status> <reason>`` (run with ``-s``).
"""

import pytest
from conftest import commit_change
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from verify_helpers import FIX, candidate, verify

CONFTEST = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
    "pytest_plugins = ('myplug',)\n"
)
FLIP = (  # R2: a wrapper turns a failed report into a passed one
    "import pytest\n"
    "@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    out = yield\n"
    "    rep = out.get_result()\n"
    "    if rep.failed:\n"
    "        rep.outcome = 'passed'\n"
    "        rep.longrepr = None\n"
)
SWALLOW = (  # R4: a wrapper swallows what the test raised
    "import pytest\n"
    "@pytest.hookimpl(wrapper=True)\n"
    "def pytest_runtest_call(item):\n"
    "    try:\n"
    "        return (yield)\n"
    "    except BaseException:\n"
    "        return None\n"
)
ALLOWED = ("src/calc.py", "src/myplug.py")  # the plugin is listed but a real fix leaves it alone


def _run(project, state, files):
    commit_change(project, {"tests/conftest.py": CONFTEST, "src/myplug.py": "X = 1\n"})
    base, test_commit = make_test_commit(project, {"tests/test_regression.py": REGRESSION})
    cand = candidate(project, base, files)
    return verify(
        state, project, base, cand, "--test-commit", test_commit,
        allowed=ALLOWED, tests=(" ".join(CHECK[0][1]),),
    )  # fmt: skip


def _say(name, code, out):
    print("RESULT", name, code, out.get("status"), out.get("reason"))


def _no_receipt(out):
    assert not out.get("verified")
    assert out.get("core_status") != "CORE_VERIFIED"
    assert not out.get("receipt") and not out.get("receipt_id")


@pytest.mark.sandbox
@pytest.mark.parametrize(
    "name,source",
    [
        ("R2_makereport_flip", FLIP),
        ("R4_swallow_call", SWALLOW),
    ],
)
def test_a_published_forgery_is_refused_with_no_receipt(project, state, name, source):
    code, out = _run(project, state, {"src/myplug.py": source})
    _say(name, code, out)
    assert out["status"] == "REFUSED" and code != 0
    assert "VERIFIER_TAMPER_API" in str(out["reason"])
    assert "pytest_hook_impl" in str(out["reason"])
    _no_receipt(out)


@pytest.mark.sandbox
def test_a_real_fix_verifies_though_the_plugin_is_listed_as_allowed(project, state):
    """The task may name the plugin file; only an actual change to it counts (workspace.py)."""
    code, out = _run(project, state, {"src/calc.py": FIX})
    _say("control_fix", code, out)
    assert (code, out["status"], out["reason"]) == (0, "VERIFIED", "CORE_VERIFIED")


@pytest.mark.sandbox
def test_a_fix_beside_a_hook_free_plugin_edit_still_verifies(project, state):
    code, out = _run(project, state, {"src/calc.py": FIX, "src/myplug.py": "X = 2\n"})
    _say("fix_beside_plugin_edit", code, out)
    assert (code, out["status"]) == (0, "VERIFIED")


# ---- a legitimate plugin that defines hooks and fixtures, loaded through a second plugin -------
PLUG_CONFTEST = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
    "pytest_plugins = ('pkg.plug',)\n"
)
PLUG_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/product.py": "def add(a, b):\n    return a + b + 1\n\ndef div(a, b):\n    return a / b\n",  # noqa: E501
    "src/pkg/plug.py": (
        "import pytest\n"
        "pytest_plugins = ('pkg.plug2',)\n"
        "def pytest_configure(config):\n"
        "    config.addinivalue_line('markers', 'plugged: made by the plugin')\n"
        "@pytest.hookimpl(wrapper=True)\n"
        "def pytest_runtest_call(item):\n"
        "    return (yield)\n"
        "@pytest.fixture\n"
        "def answer():\n    return 5\n"
    ),
    "src/pkg/plug2.py": "import pytest\n@pytest.fixture\ndef second():\n    return 1\n",
}
PLUG_TESTS = (
    "import pytest\nfrom pkg.product import add, div\n"
    "@pytest.mark.plugged\n"
    "def test_uses_the_plugin(answer, second):\n"
    "    assert add(2, 3) == answer\n    assert second == 1\n"
    "def test_raises():\n    with pytest.raises(ZeroDivisionError):\n        div(1, 0)\n"
)
PLUG_FIX = "def add(a, b):\n    return a + b\n\ndef div(a, b):\n    return a / b\n"
PLUG_ALLOWED = ("src/pkg/product.py", "src/pkg/plug.py")


def _run_plugin_project(project, state, files):
    commit_change(project, {"tests/conftest.py": PLUG_CONFTEST, **PLUG_FILES})
    base, test_commit = make_test_commit(project, {"tests/test_plug.py": PLUG_TESTS})
    cand = candidate(project, base, files)
    return verify(
        state, project, base, cand, "--test-commit", test_commit,
        allowed=PLUG_ALLOWED, tests=("python3 -m pytest -q tests/test_plug.py",),
    )  # fmt: skip


@pytest.mark.sandbox
def test_an_untouched_plugin_with_hooks_and_fixtures_passes_with_a_real_fix(project, state):
    """The plugin and product are in one package; only product.py changes; pytest.raises works."""
    code, out = _run_plugin_project(project, state, {"src/pkg/product.py": PLUG_FIX})
    _say("legit_plugin_fix", code, out)
    assert (code, out["status"], out["reason"]) == (0, "VERIFIED", "CORE_VERIFIED")


@pytest.mark.sandbox
def test_without_the_fix_the_same_test_still_fails_so_it_was_not_switched_off(project, state):
    comment = "# no fix\n" + PLUG_FILES["src/pkg/product.py"]
    code, out = _run_plugin_project(project, state, {"src/pkg/product.py": comment})
    _say("revert_fix", code, out)
    assert out["status"] != "VERIFIED" and code != 0
    assert "VERIFIER_TAMPER_API" not in str(out.get("reason"))
    _no_receipt(out)


@pytest.mark.sandbox
def test_a_benign_hook_defined_in_a_library_file_is_refused_a_known_trade_off(project, state):
    """KNOWN TRADE-OFF, locked as it is today: REFUSED (on main it verified). A repo that ships
    its own pytest plugin under ``src/`` and loads it through ``pytest_plugins`` has a candidate
    refused when it changes that file to define a (harmless) hook, even with the real fix beside
    it. The scan cannot tell a harmless hook from a forging one by shape. See
    docs/PRESERVED_BEHAVIORS.md, "已知取捨"; the counterpart that passes: the untouched-plugin test."""
    harmless = PLUG_FILES["src/pkg/plug.py"] + "def pytest_report_header(config):\n    return 'x'\n"
    files = {"src/pkg/product.py": PLUG_FIX, "src/pkg/plug.py": harmless}
    code, out = _run_plugin_project(project, state, files)
    _say("benign_hook_defined", code, out)
    assert out["status"] == "REFUSED" and code != 0
    assert "VERIFIER_TAMPER_API" in str(out["reason"])
    _no_receipt(out)
