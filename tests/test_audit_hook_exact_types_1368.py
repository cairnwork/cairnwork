"""Exact string checks in the verifier audit hooks (#1368)."""

import subprocess
import sys

from cairnwork import report_guard


def _run(tmp_path, source, declared=("data/x.csv",)):
    guard = tmp_path / "guard"
    guard.mkdir()
    report_guard.install_plugin(guard, declared)
    (tmp_path / "test_probe.py").write_text(source)
    verdict_path = tmp_path / "verdict.json"
    env = {
        "PATH": "/usr/bin:/bin",
        report_guard.PATH_ENV: str(verdict_path),
        report_guard.NONCE_ENV: "nonce",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(guard),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            report_guard.MODULE,
            "test_probe.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done, report_guard.read_verdict(verdict_path, "nonce")


def test_str_subclass_filename_does_not_run_eq_or_change_refusal(tmp_path):
    source = """
import types
calls = []
class Filename(str):
    def __eq__(self, other):
        calls.append("eq")
        return True
    __hash__ = str.__hash__
def test_filename():
    code = compile("pass", "ordinary.py", "exec").replace(co_filename=Filename("ordinary.py"))
    fn = types.FunctionType(code, {})
    try:
        object.__setattr__(fn, "__code__", compile("pass", "ordinary.py", "exec"))
    except RuntimeError:
        pass
    assert calls == []
"""
    done, verdict = _run(tmp_path, source, declared=())
    assert done.returncode == 0, done.stdout + done.stderr
    assert verdict.ran and verdict.data_refused == ()


def test_str_subclass_code_filename_is_refused(tmp_path):
    source = """
def test_filename():
    import types
    class Filename(str):
        def __eq__(self, other):
            return False
        __hash__ = str.__hash__
    code = compile("pass", "ordinary.py", "exec").replace(co_filename=Filename("data/x.csv"))
    exec(code, {})
"""
    done, verdict = _run(tmp_path, source)
    assert done.returncode == 1, done.stdout + done.stderr
    assert not verdict.ran and verdict.data_refused == ("<non-exact code filename>",)


def test_exact_str_filenames_keep_existing_behavior(tmp_path):
    source = """
def test_filename():
    exec(compile("pass", "ordinary.py", "exec"), {})
"""
    done, verdict = _run(tmp_path, source, declared=())
    assert done.returncode == 0, done.stdout + done.stderr
    assert verdict.ran and verdict.data_refused == ()


def test_str_subclass_filename_on_reassigned_code_keeps_data_refusal(tmp_path):
    source = """
calls = []
class Filename(str):
    def __eq__(self, other):
        calls.append("eq")
        return False
    __hash__ = str.__hash__
def target():
    pass
def test_filename():
    code = compile("pass", "ordinary.py", "exec").replace(co_filename=Filename("ordinary.py"))
    object.__setattr__(target, "__code__", code)
    target.__defaults__ = ()
    assert calls == []
"""
    done, verdict = _run(tmp_path, source)
    assert done.returncode == 0, done.stdout + done.stderr
    assert verdict.ran and verdict.data_refused == ()
