"""#775 Step 1: the guard records why a node failed (``fail_kind``), end to end through the real
plugin: a fixture that raised before the test body is ``setup``, a call that raised an OSError is
``env``, anything else (an assertion, a missing import, xfail, skip) is absent. ``outcomes`` keep
their strings. The malformed-record rows pin the parser (fail closed)."""

import json
import subprocess
import sys

import pytest

from cairnwork import report_channel, report_guard

NONCE = "n0nce"
T = "tests/test_x.py::"


def _run(tmp_path, body):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard)
    channel = report_channel.open_channel(tmp_path)
    try:
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONPATH": str(guard),
            **channel.env(),
        }
        argv = [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
            "-p", report_guard.MODULE, "tests/test_x.py",
        ]  # fmt: skip
        subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, pass_fds=channel.pass_fds)
        return channel.read()
    finally:
        channel.close()


def test_a_fixture_raising_in_setup_is_setup_and_the_outcome_stays_failed(tmp_path):
    v = _run(
        tmp_path,
        "import pytest\n"
        "@pytest.fixture\n"
        "def home():\n"
        "    raise KeyError('no such account')\n"
        "def test_a(home):\n"
        "    pass\n",
    )
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {f"{T}test_a": "setup"}


def test_an_oserror_in_the_test_body_is_env(tmp_path):
    v = _run(tmp_path, "def test_a():\n    raise PermissionError(1, 'denied')\n")
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {f"{T}test_a": "env"}


def test_assertion_import_and_runtime_errors_in_the_body_are_not_recorded(tmp_path):
    v = _run(
        tmp_path,
        "def test_assert():\n    assert 1 == 2\n"
        "def test_import():\n    from nowhere import thing\n"
        "def test_key():\n    {}['x']\n",
    )
    assert set(v.outcomes.values()) == {"failed"}
    assert v.fail_kind == {}


def test_xfail_xpass_strict_and_a_skip_in_a_fixture_are_not_recorded(tmp_path):
    v = _run(
        tmp_path,
        "import pytest\n"
        "@pytest.fixture\n"
        "def gone():\n"
        "    pytest.skip('not here')\n"
        "@pytest.mark.xfail(strict=True)\n"
        "def test_xpass():\n    pass\n"
        "@pytest.mark.xfail\n"
        "def test_xfail():\n    raise PermissionError(1, 'denied')\n"
        "def test_skipped(gone):\n    pass\n",
    )
    assert v.outcomes[f"{T}test_xpass"] == "failed"  # strict xpass is a failed call, no exception
    assert v.outcomes[f"{T}test_skipped"] == "skipped"
    assert v.fail_kind == {}


def test_pytest_raises_not_raised_is_not_recorded(tmp_path):
    v = _run(
        tmp_path,
        "import pytest\ndef test_a():\n    with pytest.raises(OSError):\n        pass\n",
    )
    assert v.outcomes == {f"{T}test_a": "failed"}
    assert v.fail_kind == {}


def test_a_failing_shared_module_fixture_marks_every_user_as_setup(tmp_path):
    v = _run(
        tmp_path,
        "import pytest\n"
        "@pytest.fixture(scope='module')\n"
        "def shared():\n"
        "    raise PermissionError(1, 'denied')\n"
        "def test_a(shared):\n    pass\n"
        "def test_b(shared):\n    pass\n",
    )
    assert v.fail_kind == {f"{T}test_a": "setup", f"{T}test_b": "setup"}


def test_one_parametrisation_env_and_another_assertion_are_told_apart(tmp_path):
    v = _run(
        tmp_path,
        "import pytest\n"
        "@pytest.mark.parametrize('case', ['a', 'b'])\n"
        "def test_p(case):\n"
        "    if case == 'a':\n"
        "        raise PermissionError(1, 'denied')\n"
        "    assert False\n",
    )
    assert v.fail_kind == {f"{T}test_p[a]": "env"}


def test_sub_tests_that_all_raise_oserror(tmp_path):
    """Pins what pytest 9 does: the record decides, the plugin adds no rule of its own."""
    v = _run(
        tmp_path,
        "def test_s(subtests):\n"
        "    for i in range(2):\n"
        "        with subtests.test(i=i):\n"
        "            raise PermissionError(1, 'denied')\n",
    )
    assert v.outcomes == {f"{T}test_s": "failed"}
    assert v.fail_kind == {f"{T}test_s": "env"}


@pytest.mark.parametrize("oserror_first", [True, False], ids=["oserror-first", "assertion-first"])
def test_sub_tests_with_one_non_oserror_failure_stay_teeth(tmp_path, oserror_first):
    oserr = "        raise PermissionError(1, 'denied')\n"
    boom = "        assert False\n"
    first, second = (oserr, boom) if oserror_first else (boom, oserr)
    v = _run(
        tmp_path,
        "def test_s(subtests):\n"
        "    with subtests.test(i=0):\n" + first + "    with subtests.test(i=1):\n" + second,
    )
    assert v.outcomes == {f"{T}test_s": "failed"}
    assert v.fail_kind == {}


def _record(**extra):
    return json.dumps(
        {
            "nonce": NONCE, "collected": ["t.py::a"], "outcomes": {"t.py::a": "failed"},
            "exitstatus": 1, "testsfailed": 1, **extra,
        }
    ).encode()  # fmt: skip


def test_a_record_without_fail_kind_reads_as_empty():
    v = report_guard.parse_verdict(_record(), NONCE)
    assert v.recorded and v.fail_kind == {}


def test_a_well_formed_fail_kind_is_kept():
    v = report_guard.parse_verdict(_record(fail_kind={"t.py::a": "env"}), NONCE)
    assert v.recorded and v.fail_kind == {"t.py::a": "env"}


@pytest.mark.parametrize(
    "bad",
    [
        ["t.py::a"],  # not a dict
        {"t.py::a": "other"},  # unknown kind
        {"t.py::a": 1},  # not a string
        {"t.py::elsewhere": "env"},  # not a node in outcomes
        {1: "env"},  # key not a string
    ],
)
def test_a_malformed_fail_kind_refuses_the_record(bad):
    v = report_guard.parse_verdict(_record(fail_kind=bad), NONCE)
    assert not v.recorded and not v.ran and v.outcomes == {}
