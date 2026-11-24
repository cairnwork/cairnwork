"""#1681: the three experimental sandbox entries run under ``mask_recheck.guard``. Each real entry
function is called with a fake bwrap-shaped backend and a fake ``confined.execute``; no real
sandbox is needed. Masks that moved during the run raise MaskChanged; masks that held return."""

import os
import sys
from types import SimpleNamespace

import pytest
from oracle_support import lay
from second_observer_support import Project

from cairnwork import confined
from cairnwork.mask_recheck import MaskChanged
from cairnwork_sandbox import bwrap
from experimental.import_origin import runner
from experimental.oracle_observer import run as oracle_run
from experimental.second_observer import observe
from experimental.second_observer.inputs import check
from experimental.second_observer.rebuild import rebuild


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "fakehome"
    home.mkdir()
    (home / ".netrc").write_text("old\n")
    (home / "new").write_text("new\n")
    return home.resolve()


def _backend(home):
    def policy(read_roots, write_roots, home_arg=None, mask_home_credentials=True, **_ignored):
        return bwrap.BACKEND.policy(read_roots, write_roots, home, mask_home_credentials)

    return SimpleNamespace(
        name="bwrap",
        run_env=(),
        masked_home_paths=bwrap.BACKEND.masked_home_paths,
        policy=policy,
        wrap=lambda argv, policy_path: argv,
    )


def _install(monkeypatch, module, home, changed):
    monkeypatch.setattr(module.cairnwork_sandbox, "select", lambda: _backend(home))

    def execute(*_args, **_kwargs):
        if changed:
            os.replace(home / "new", home / ".netrc")  # a host event during the run
        return 0, False, 0

    monkeypatch.setattr(module.confined, "execute", execute)
    assert module.confined is confined


def _shadow(tmp_path):
    project = Project(tmp_path)
    inp = check(project.shadow_input(project.base, "tests/test_calc.py::test_add"))
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    return inp, rebuild(inp, shadow, shadow / "work"), shadow


def _call_import_origin(tmp_path):
    inp, checkout, shadow = _shadow(tmp_path)
    return runner.run_pytest(inp, checkout, shadow, timeout=5)


def _call_second_observer(tmp_path):
    inp, checkout, shadow = _shadow(tmp_path)
    return observe.run_observer(inp, checkout, shadow, timeout=5)


def _call_oracle(tmp_path):
    checkout, probe = lay(tmp_path, {"calc.py": "x = 1\n"})
    work = tmp_path / "oracle"
    work.mkdir()
    return oracle_run.run_probe(
        probe,
        checkout,
        work,
        interpreter=sys.executable,
        pythonpath=(),
        timeout=5,
    )


ENTRIES = [
    pytest.param(runner, _call_import_origin, runner.Run, id="import_origin"),
    pytest.param(observe, _call_second_observer, observe.Observation, id="second_observer"),
    pytest.param(oracle_run, _call_oracle, oracle_run.ProbeRun, id="oracle_observer"),
]


@pytest.mark.parametrize(("module", "call", "kind"), ENTRIES)
def test_masks_changed_during_the_run_make_the_entry_raise(
    tmp_path, home, monkeypatch, module, call, kind
):
    _install(monkeypatch, module, home, changed=True)
    with pytest.raises(MaskChanged, match=r"CREDENTIAL_MASK_CHANGED.*\.netrc"):
        call(tmp_path)


@pytest.mark.parametrize(("module", "call", "kind"), ENTRIES)
def test_masks_held_the_entry_returns_normally(tmp_path, home, monkeypatch, module, call, kind):
    _install(monkeypatch, module, home, changed=False)
    assert isinstance(call(tmp_path), kind)
