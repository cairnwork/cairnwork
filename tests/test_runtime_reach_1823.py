"""#1823: a base sys.path or plugin entry that cannot be resolved refuses at check_with_base.

Failures inside the probe are swallowed (base_fixture_names._record_run), so the refusal has to
come at check time, from check_runtime_reach. These go through check_with_base itself, with the
real resolve_record run inside the probe, and need no sandbox.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import CODE, resolve_record

OTHER = "tests/test_other.py"
HELPER = "tests/helper_mod.py"
ARCHIVE = "data/lib.zip"


def _record(sys_path=(), plugins=()) -> CollectedRecord:
    return CollectedRecord(frozenset(), tuple(plugins), tuple(sys_path), ())


@pytest.fixture
def co(tmp_path):
    root = tmp_path.resolve() / "co"
    (root / "data").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / ARCHIVE).write_bytes(b"PK")
    return root


def _run(monkeypatch: Any, root: Path, record: CollectedRecord, paths: tuple[str, ...]) -> None:
    def fake_verifiers(ctx, base, specs, run_id):  # resolve_record runs inside the probe's try
        return [SimpleNamespace(collect_record=resolve_record(record, root))]

    monkeypatch.setattr(bfn, "collect", lambda *a: None)
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: None)
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_base_paths", lambda task: None)
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: (OTHER,))
    monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: ())
    monkeypatch.setattr(bfn, "pick_dir_targets", lambda *a: ())
    monkeypatch.setattr(bfn, "run_verifiers", fake_verifiers)
    task = SimpleNamespace(repo=root, base_commit="b", test_commit="t", test_roots=("tests",))
    bfn.check_with_base(SimpleNamespace(account=None), task, paths, "run")


def _closed(monkeypatch: Any) -> None:
    real = os.path.realpath

    def realpath(path, *, strict=False):
        if "closed" in str(path) and strict:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, strict=strict)

    monkeypatch.setattr(os.path, "realpath", realpath)


def _bad_entry(kind: str, root: Path, monkeypatch: Any) -> str:
    if kind == "loop":
        (root / "loop").symlink_to(root / "loop")
        return str(root / "loop")
    if kind == "name":
        return str(root / ("n" * 300))
    _closed(monkeypatch)
    return str(root / "closed" / "inner")


@pytest.mark.parametrize("kind", ["loop", "closed", "name"])
@pytest.mark.parametrize("change", ["plugin", "archive", "entry"])
def test_an_unresolvable_entry_inside_the_checkout_refuses(co, monkeypatch, kind, change):
    entry = _bad_entry(kind, co, monkeypatch)
    plugins, sys_path, paths = [], [entry], (OTHER,)
    if change == "plugin":
        plugins, paths = [("helper_mod", None)], (HELPER,)
    elif change == "archive":
        sys_path, paths = [entry, str(co / ARCHIVE)], (ARCHIVE,)
    else:
        paths = (os.path.relpath(entry, co),)
    with pytest.raises(TestCommitRejected) as caught:
        _run(monkeypatch, co, _record(sys_path, plugins), paths)
    assert CODE in str(caught.value) or getattr(caught.value, "code", None) == CODE


def test_an_unresolvable_plugin_file_inside_the_checkout_refuses(co, monkeypatch):
    entry = _bad_entry("loop", co, monkeypatch)
    with pytest.raises(TestCommitRejected):
        _run(monkeypatch, co, _record(plugins=[("p", entry)]), (OTHER,))


def test_an_unresolvable_entry_outside_the_checkout_passes(co, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "loop").symlink_to(out / "loop")
    _run(monkeypatch, co, _record([str(out / "loop"), str(out / ("n" * 300))]), (OTHER,))


def test_an_archive_inner_entry_passes(co, monkeypatch):
    _run(monkeypatch, co, _record([str(co / ARCHIVE / "inner")]), (OTHER,))


def test_a_missing_entry_passes(co, monkeypatch):
    _run(monkeypatch, co, _record([str(co / "nothere"), "relative/missing"]), (OTHER,))


def test_a_readable_link_entry_passes(co, monkeypatch):
    (co / "link").symlink_to(co / "pkg")
    _run(monkeypatch, co, _record([str(co / "link"), str(co / "pkg")]), (OTHER,))


def test_a_normal_run_passes(co, monkeypatch):
    _run(monkeypatch, co, _record([str(co), str(co / "pkg")], [("helper_mod", None)]), (OTHER,))


def test_resolve_record_does_not_raise_for_an_unresolvable_entry(co, monkeypatch):
    entry = _bad_entry("loop", co, monkeypatch)
    assert resolve_record(_record([entry]), co).real
