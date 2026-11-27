"""#1876: ``entry_dirs`` hands the sweep the realpath of the run dir, so a dir spelled through a
symlink (``/tmp`` for ``/private/tmp``, ``/var`` for ``/private/var``) must be judged by where it
really is. The strict reading stays where the given spelling is what gets used (``resolve=False``)
and for every link that leads out of a root. Nothing is signalled or started."""

import os
import tempfile

import pytest

from cairnwork import proc_cwd

ATTEMPT = "a0123456789abcdef"


@pytest.fixture
def where(tmp_path, monkeypatch):
    real = os.path.realpath(tmp_path)
    top = os.path.realpath(tempfile.gettempdir())
    assert real.startswith(top + "/")
    monkeypatch.setattr(proc_cwd, "_temps", lambda: [top])
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_DARWIN", True)
    os.makedirs(f"{real}/base/runs/{ATTEMPT}")
    os.makedirs(f"{real}/outside/runs/{ATTEMPT}")
    os.symlink(f"{real}/base", f"{real}/via-link")  # a second spelling of base
    os.symlink(f"{real}/outside", f"{real}/base/runs/escape")  # a link out of the run root
    return real


def test_a_dir_spelled_through_a_link_is_judged_by_its_realpath(where):
    root = f"{where}/base/runs"
    dirs = proc_cwd.entry_dirs(f"{where}/via-link/runs/{ATTEMPT}", [root])
    assert dirs == (f"{where}/base/runs/{ATTEMPT}",)


def test_scope_resolve_accepts_the_link_spelling_and_returns_the_realpath(where):
    root = f"{where}/base/runs"
    given = f"{where}/via-link/runs/{ATTEMPT}"
    assert proc_cwd.scope([given], [root], resolve=True) == (f"{where}/base/runs/{ATTEMPT}",)


def test_a_root_spelled_through_a_link_still_works(where):
    root = f"{where}/via-link/runs"
    dirs = proc_cwd.entry_dirs(f"{where}/base/runs/{ATTEMPT}", [root])
    assert dirs == (f"{where}/base/runs/{ATTEMPT}",)


def test_the_given_spelling_is_still_judged_when_it_is_what_gets_used(where):
    root = f"{where}/base/runs"
    given = f"{where}/via-link/runs/{ATTEMPT}"
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([given], [root])  # resolve=False returns ``given``: both spellings must fit


@pytest.mark.parametrize("resolve", [True, False])
def test_a_link_leading_out_of_the_root_is_refused(where, resolve):
    root = f"{where}/base/runs"
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{where}/base/runs/escape/runs/{ATTEMPT}"], [root], resolve=resolve)
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{where}/via-link/runs/escape/runs/{ATTEMPT}"], [root], resolve=resolve)


@pytest.mark.parametrize("resolve", [True, False])
def test_a_link_spelled_dir_whose_target_is_outside_is_refused(where, resolve):
    os.symlink(f"{where}/outside", f"{where}/out-link")
    root = f"{where}/base/runs"
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{where}/out-link/runs/{ATTEMPT}"], [root], resolve=resolve)


@pytest.mark.parametrize("resolve", [True, False])
def test_a_lookalike_sibling_through_a_link_is_refused(where, resolve):
    os.makedirs(f"{where}/base/runs-evil/{ATTEMPT}")
    root = f"{where}/base/runs"
    for spelling in ("base", "via-link"):
        with pytest.raises(proc_cwd.OutOfScope):
            proc_cwd.scope([f"{where}/{spelling}/runs-evil/{ATTEMPT}"], [root], resolve=resolve)


@pytest.mark.parametrize("resolve", [True, False])
def test_the_root_itself_and_a_broad_root_are_still_refused(where, resolve):
    root = f"{where}/base/runs"
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([root], [root], resolve=resolve)  # strictly inside, not the root itself
    broad = os.path.realpath(tempfile.gettempdir())
    with pytest.raises(proc_cwd.OutOfScope):
        proc_cwd.scope([f"{broad}/x"], [broad], resolve=resolve)


def test_dot_dot_and_relative_spellings_stay_plain_errors(where):
    root = f"{where}/base/runs"
    for bad in (f"{where}/via-link/runs/../runs/{ATTEMPT}", "relative/runs"):
        with pytest.raises(ValueError) as caught:
            proc_cwd.scope([bad], [root], resolve=True)
        assert not isinstance(caught.value, proc_cwd.OutOfScope)
