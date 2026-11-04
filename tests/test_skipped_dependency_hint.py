"""#813: a module skipped at collection (``pytest.importorskip``, as pallets/flask
tests/test_async.py:11) names the missing module in the base comparison's detail; the verdict and
its code are the ones it had before: rc 5 stays BASE_NO_TESTS, a skipped target stays NOT_RUN."""

import json
import subprocess
import sys
from types import SimpleNamespace

from cairnwork import report_guard
from cairnwork.deferred_tests import base_comparison

NONCE = "n0nce"
TARGET = "tests/test_x.py::test_a"
DEP = "asgiref_not_installed"


def _real_run(tmp_path, body):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(body)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    guard, verdict = tmp_path / "guard", tmp_path / "verdict.json"
    guard.mkdir()
    report_guard.install_plugin(guard)
    env = {"PATH": "/usr/bin:/bin", report_guard.PATH_ENV: str(verdict),
           report_guard.NONCE_ENV: NONCE, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTHONPATH": str(guard)}  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", "pytest.ini",
         "-p", report_guard.MODULE, "tests/test_x.py"],
        cwd=tmp_path, env=env, capture_output=True,
    )  # fmt: skip
    return done.returncode, report_guard.read_verdict(verdict, NONCE)


def _stand_in(rc, verdict, outcomes):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), returncode=rc, outcomes=outcomes, timed_out=False,
        report_too_big=False, recorded=verdict.recorded, collect_errors=(), mutated=(),
        teardown_errors=(), fail_kind={}, survivors=0, forged=(), log_too_big=False,
        skipped_deps=verdict.skipped_deps,
    )  # fmt: skip


def test_a_real_importorskip_exits_5_and_names_the_module(tmp_path):
    rc, verdict = _real_run(tmp_path, f"import pytest\npytest.importorskip({DEP!r})\n"
                            "def test_a(): pass\n")  # fmt: skip
    assert rc == 5 and verdict.skipped_deps == (DEP,) and not verdict.ran
    got = base_comparison([_stand_in(rc, verdict, {})], frozenset({TARGET}))
    assert (got.kind, got.code) == ("UNCOMPARABLE", "BASE_NO_TESTS")  # as before
    assert f"could not import 1: {DEP}" in got.detail


def test_a_skipped_target_beside_a_passing_test_is_still_not_run_and_names_it(tmp_path):
    rc, verdict = _real_run(tmp_path, f"import pytest\npytest.importorskip({DEP!r})\n")
    run = _stand_in(0, verdict, {"tests/test_old.py::test_ok": "passed"})
    got = base_comparison([run], frozenset({TARGET}))
    assert (got.kind, got.code) == ("NOT_RUN", "DEFERRED_TESTS_NOT_RUN")  # as before
    assert DEP in got.detail


def test_without_a_skip_the_detail_is_what_it_was():
    verdict = SimpleNamespace(recorded=True, skipped_deps=())
    got = base_comparison([_stand_in(5, verdict, {})], frozenset({TARGET}))
    assert got.detail == "v: BASE_NO_TESTS (exit 5)"


def test_names_in_the_record_are_only_module_names(tmp_path):
    bad = [DEP, "x'\n::error::boom", "a b", 7, "", "k" * 200]
    record = json.dumps({"nonce": NONCE, "collected": [], "outcomes": {}, "exitstatus": 5,
                         "testsfailed": 0, "collect_skipped": ["t.py"],
                         "collect_skipped_deps": bad})  # fmt: skip
    path = tmp_path / "v.json"
    path.write_text(record)
    assert report_guard.read_verdict(path, NONCE).skipped_deps == (DEP,)
    path.write_text(record.replace('"collect_skipped_deps"', '"x"'))
    assert report_guard.read_verdict(path, NONCE).skipped_deps == ()


def test_a_dotted_name_with_a_bad_later_part_is_dropped(tmp_path):
    from cairnwork.skip_hint import is_module

    assert is_module("ok.a_b") and not is_module("ok.a b")
    assert not is_module("ok.") and not is_module("ok.1x")
    assert report_guard.parse_verdict(_record(["ok.a b", "ok.good"]), NONCE).skipped_deps == (
        "ok.good",
    )


def test_a_repeated_module_name_is_listed_once():
    got = report_guard.parse_verdict(_record([DEP, "other", DEP, DEP]), NONCE).skipped_deps
    assert got == (DEP, "other")


def _record(deps):
    return json.dumps({"nonce": NONCE, "collected": [], "outcomes": {}, "exitstatus": 5,
                       "testsfailed": 0, "collect_skipped": ["t.py"],
                       "collect_skipped_deps": deps}).encode()  # fmt: skip
